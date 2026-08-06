"""Quanto tempo o SAC ficou sem ninguem escutando -- medido, nao estimado.

Eu escrevi que "hoje a janela e de segundos e so na troca". Nao tinha medido
nada. Este arquivo existe para essa frase virar numero ou ser desmentida.

COMO MEDE

  Enquanto vivo, cada listener bate um pulso no banco a cada 30s. Uma LACUNA
  e qualquer intervalo em que NENHUM listener bateu.

  A batida periodica e de proposito, em vez de "avisar quando cair": processo
  que leva SIGKILL nao avisa nada. Ele simplesmente para de bater -- e o
  silencio e o sinal.

POR QUE A LACUNA E CARA

  O Slack NAO reenvia interacao. Cada segundo sem conexao aberta e um clique
  que a Maria da e some, e o que ela ve e "Tivemos alguns problemas de
  conexao" -- exatamente o que aconteceu no ensaio do modal em 06/08/2026.

  Por isso a conta soma as batidas de TODAS as instancias numa linha so: com
  runs sobrepostos, o listener A morrendo enquanto o B ja bate nao e lacuna
  nenhuma. A sobreposicao e o que a gente esta comprando; o medidor precisa
  enxergar isso, senao acusaria queda a cada troca de run.

Uso:
    python listener_saude.py --relatorio --horas 24
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

sys.path.insert(0, str(Path(__file__).parent))

# Intervalo entre pulsos. Curto o bastante para que a menor lacuna que
# importa (uma troca de run malfeita) apareca, e barato: 2.880 linhas por dia
# por instancia e ruido para o Neon.
INTERVALO_S = 30

# Acima disto, silencio vira lacuna. Tem que ser MAIOR que o intervalo, senao
# toda batida normal apareceria como queda -- e um relatorio cheio de falso
# positivo e um relatorio que ninguem le.
TOLERANCIA_S = 90


def _instante(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def lacunas(batidas: Iterable[Mapping[str, Any]],
            inicio: datetime, fim: datetime,
            tolerancia_s: int = TOLERANCIA_S) -> list[dict]:
    """Os intervalos em que NINGUEM estava escutando.

    As batidas de todas as instancias entram numa linha do tempo unica: quem
    bateu nao importa, importa se ALGUEM bateu.
    """
    marcos = sorted(
        d for d in (_instante(b.get("quando")) for b in batidas or []) if d)

    saida: list[dict] = []
    anterior = inicio
    for m in marcos:
        if m < inicio:
            anterior = max(anterior, m)
            continue
        if m > fim:
            break
        vao = (m - anterior).total_seconds()
        if vao > tolerancia_s:
            saida.append({"de": anterior, "ate": m, "segundos": vao})
        anterior = m

    # A borda final: listener que morreu e nao voltou e o pior caso, e some
    # do relatorio se a conta parar na ultima batida.
    vao = (fim - anterior).total_seconds()
    if vao > tolerancia_s:
        saida.append({"de": anterior, "ate": fim, "segundos": vao})
    return saida


def disponibilidade(fora_s: float, janela_s: float) -> float:
    """% do tempo com alguem escutando.

    Nao arredonda para 100: "caiu uma vez" e "nunca caiu" dizem coisas
    diferentes, e arredondar faz ninguem investigar.
    """
    if janela_s <= 0:
        return 100.0
    pct = (1 - fora_s / janela_s) * 100
    if 0 < fora_s and pct >= 100.0:
        return 99.99
    return max(0.0, pct)


def resumo(ls: list, inicio: datetime, fim: datetime) -> dict:
    total = sum(l["segundos"] for l in ls)
    janela = (fim - inicio).total_seconds()
    return {
        "n": len(ls),
        "total_s": total,
        "maior_s": max((l["segundos"] for l in ls), default=0.0),
        "janela_s": janela,
        "disponibilidade": disponibilidade(total, janela),
    }


def fmt_dur(segundos: float) -> str:
    s = int(segundos)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}min {s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}"


# --- I/O -------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS sac_listener_pulso (
    instancia TEXT        NOT NULL,
    quando    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (instancia, quando)
);
CREATE INDEX IF NOT EXISTS ix_sac_pulso_quando ON sac_listener_pulso (quando);
"""


def nome_da_instancia() -> str:
    """Quem esta batendo. No Actions, o id do run; local, o hostname."""
    run = os.environ.get("GITHUB_RUN_ID")
    if run:
        return f"actions-{run}"
    return f"local-{socket.gethostname()}"


def garantir_tabela(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_DDL)
    conn.commit()


def bater(conn, instancia: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sac_listener_pulso (instancia) VALUES (%s) "
            "ON CONFLICT DO NOTHING", (instancia,))
    conn.commit()


def ler_batidas(conn, desde: datetime) -> list[dict]:
    cur = conn.cursor()
    cur.execute("SELECT instancia, quando FROM sac_listener_pulso "
                "WHERE quando >= %s ORDER BY quando", (desde,))
    return [{"instancia": i, "quando": q} for i, q in cur.fetchall()]


def limpar_antigos(conn, dias: int = 30) -> int:
    """O pulso e barato, mas nao e eterno."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sac_listener_pulso "
                    "WHERE quando < now() - make_interval(days => %s)", (dias,))
        n = cur.rowcount
    conn.commit()
    return n


def relatorio(horas: int = 24) -> int:
    from src.db.connection import get_db_connection

    conn = get_db_connection()
    garantir_tabela(conn)

    fim = datetime.now(timezone.utc)
    inicio = fim - timedelta(hours=horas)
    b = ler_batidas(conn, inicio)
    ls = lacunas(b, inicio, fim)
    r = resumo(ls, inicio, fim)

    instancias = sorted({x["instancia"] for x in b})
    print(f"Listener do SAC — últimas {horas}h")
    print(f"  pulsos            : {len(b)} de {len(instancias)} instância(s)")
    print(f"  disponibilidade   : {r['disponibilidade']:.2f}%".replace(".", ","))
    print(f"  tempo sem ninguém : {fmt_dur(r['total_s'])}")
    print(f"  quedas            : {r['n']}"
          + (f" (maior: {fmt_dur(r['maior_s'])})" if r["n"] else ""))

    if not b:
        print("\n  [!] nenhum pulso na janela — ou o listener nunca subiu, ou "
              "a medição só começou agora. Sem dado não há afirmação.")
        return 1

    for l in ls[:10]:
        de = l["de"].astimezone(timezone(timedelta(hours=-3)))
        print(f"    - {de:%d/%m %H:%M:%S} por {fmt_dur(l['segundos'])}")
    # Lacuna e clique perdido: o job fica vermelho para alguem olhar.
    return 1 if ls else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--relatorio", action="store_true")
    ap.add_argument("--horas", type=int, default=24)
    args = ap.parse_args()
    if not args.relatorio:
        ap.print_help()
        return 0
    return relatorio(args.horas)


if __name__ == "__main__":
    sys.exit(main())
