"""Telemetria de tempo do processo de SAC.

De "acho que demora" para número. É a base da medição por setor que vem
depois: se cada etapa e cada passagem de bastão têm tempo medido, dá para
saber onde o processo trava sem depender da percepção de ninguém.

Começa pelo que o dado JÁ permite (17.641 casos fechados com abertura e
fechamento carimbados), em vez de esperar semanas de coleta nova. O que falta
instrumentar está declarado no fim deste arquivo — honestidade sobre o que
ainda não sabemos vale mais que um número inventado.

Uso:
    python telemetria.py              # relatório do histórico
    python telemetria.py --dias 30    # só o período recente
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional


def _dt(v) -> Optional[datetime]:
    """Aceita datetime ou texto ISO; devolve com fuso, ou None se inválido."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def horas_entre(inicio, fim) -> Optional[float]:
    a, b = _dt(inicio), _dt(fim)
    if a is None or b is None:
        return None
    h = (b - a).total_seconds() / 3600.0
    return h if h >= 0 else None


def duracao_dias(inicio, fim) -> Optional[float]:
    """Dias entre dois instantes. None quando a data é inválida OU quando o
    fim vem antes do início -- isso é dado corrompido, não duração negativa."""
    h = horas_entre(inicio, fim)
    return None if h is None else h / 24.0


def percentil(valores: Iterable[float], p: float) -> Optional[float]:
    """p-ésimo percentil por interpolação linear.

    A média esconde o caso que demorou muito; o p90 é quem denuncia. Um
    processo com média de 15 dias e p90 de 120 não é o mesmo processo."""
    vs = sorted(v for v in valores if v is not None)
    if not vs:
        return None
    if len(vs) == 1:
        return vs[0]
    pos = (len(vs) - 1) * (p / 100.0)
    baixo = int(pos)
    alto = min(baixo + 1, len(vs) - 1)
    peso = pos - baixo
    return vs[baixo] * (1 - peso) + vs[alto] * peso


def resumo_etapas(casos: Iterable[Mapping[str, Any]]) -> dict:
    """Tempo de vida por etapa, apenas de casos FECHADOS.

    Caso aberto ainda está correndo: incluí-lo baixaria a média de mentira,
    porque o tempo dele ainda não terminou de acontecer."""
    por_etapa: dict[str, list[float]] = defaultdict(list)
    for c in casos:
        if c.get("claim_status") != "closed":
            continue
        d = duracao_dias(c.get("date_created"), c.get("date_updated"))
        if d is None:
            continue
        por_etapa[str(c.get("claim_stage") or "—")].append(d)

    return {
        etapa: {
            "casos": len(vs),
            "media_dias": round(sum(vs) / len(vs), 1),
            "mediana_dias": round(percentil(vs, 50) or 0, 1),
            "p90_dias": round(percentil(vs, 90) or 0, 1),
            "max_dias": round(max(vs), 1),
        }
        for etapa, vs in por_etapa.items() if vs
    }


def tempo_de_reacao(pares: Iterable[Mapping[str, Any]]) -> dict:
    """Quanto tempo entre o caso abrir no ML e a Maria ser avisada.

    É o handoff que depende só de nós -- e por isso o primeiro que devemos
    cobrar de nós mesmos."""
    horas = []
    for p in pares:
        h = horas_entre(p.get("abriu"), p.get("avisou"))
        if h is not None:
            horas.append(h)
    if not horas:
        return {"casos": 0, "media_horas": None, "mediana_horas": None,
                "p90_horas": None}
    return {
        "casos": len(horas),
        "media_horas": round(sum(horas) / len(horas), 1),
        "mediana_horas": round(percentil(horas, 50) or 0, 1),
        "p90_horas": round(percentil(horas, 90) or 0, 1),
    }


# --- coleta ----------------------------------------------------------------

def coletar(dias: Optional[int] = None) -> dict:
    from src.db.connection import get_db_connection, dict_cursor

    conn = get_db_connection()
    try:
        filtro = ""
        if dias:
            filtro = (f"AND date_created::timestamptz > NOW() - "
                      f"interval '{int(dias)} days'")
        with dict_cursor(conn) as cur:
            cur.execute(f"""
                SELECT claim_id, claim_status, claim_stage,
                       date_created, date_updated
                FROM ml_devolucoes
                WHERE date_created ~ '^[0-9]{{4}}-'
                  AND date_updated ~ '^[0-9]{{4}}-'
                  {filtro}
            """)
            casos = cur.fetchall()

            cur.execute(f"""
                SELECT DISTINCT ON (sn.claim_id)
                       d.date_created AS abriu, sn.avisado_em AS avisou
                FROM slack_notificados sn
                JOIN ml_devolucoes d ON d.claim_id = sn.claim_id
                WHERE d.date_created ~ '^[0-9]{{4}}-'
                  {filtro.replace('date_created', 'd.date_created')}
                ORDER BY sn.claim_id, sn.avisado_em ASC
            """)
            pares = cur.fetchall()
    finally:
        conn.close()

    return {"etapas": resumo_etapas(casos),
            "reacao": tempo_de_reacao(pares),
            "total_casos": len(casos)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=None,
                    help="limita ao período recente")
    args = ap.parse_args()

    r = coletar(args.dias)
    rot = f"últimos {args.dias} dias" if args.dias else "todo o histórico"

    print("=" * 72)
    print(f"TELEMETRIA DE TEMPO — {rot}")
    print("=" * 72)
    print(f"  casos analisados: {r['total_casos']:,}")

    print()
    print("  TEMPO DE VIDA DO CASO, POR ETAPA (só fechados)")
    print(f"  {'etapa':<14}{'casos':>8}{'média':>9}{'mediana':>10}"
          f"{'p90':>9}{'máximo':>10}")
    for etapa, m in sorted(r["etapas"].items(),
                           key=lambda x: -x[1]["casos"]):
        print(f"  {etapa:<14}{m['casos']:>8,}{m['media_dias']:>8.1f}d"
              f"{m['mediana_dias']:>9.1f}d{m['p90_dias']:>8.1f}d"
              f"{m['max_dias']:>9.1f}d")

    re_ = r["reacao"]
    print()
    print("  TEMPO DE REAÇÃO (abrir no ML → avisar a Maria)")
    if re_["casos"]:
        print(f"    {re_['casos']:,} casos | média {re_['media_horas']}h | "
              f"mediana {re_['mediana_horas']}h | p90 {re_['p90_horas']}h")
    else:
        print("    sem dados")

    print()
    print("  AINDA NÃO INSTRUMENTADO (não vamos inventar):")
    print("    · quando a Maria efetivamente respondeu — o ML não expõe")
    print("    · quando o produto entrou no galpão — depende do recebimento")
    print("    · transição de etapa — o banco guarda só o estado atual")
    return 0


if __name__ == "__main__":
    sys.exit(main())
