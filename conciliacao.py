"""Conciliação mensal — o número que a diretoria confere linha a linha.

Reunião de 05/08/2026 com Thayná e Gabriel: fechar julho caso a caso, batendo
o painel do Mercado Livre, o Slack e os sistemas NTC. Não é relatório de
leitura: cada linha vai ser aberta na frente do chefe.

Três decisões que o módulo toma e o resto do sistema herda:

**Prejuízo é a manchete.** Total negativo é dinheiro que saiu. Total positivo
é receita da venda que ficou de pé (houve reclamação, não houve devolução) —
não é ganho da disputa e nunca soma com prejuízo. Esse defeito já apareceu
duas vezes: Canvas mensal (03/08) e fechamento diário (05/08).

**Zero e ausente são coisas diferentes.** Zero foi medido: o ML cobriu.
Ausente é resultado desconhecido. Confundir os dois faz o mês parecer fechado
com apuração faltando.

**A página do ML tem que fechar com ela mesma.** Produto + tarifa + envios +
cancelamentos + parcelamento tem que dar o Total exibido. Não dando, a coleta
perdeu uma linha — foi o caso do pedido 2000017711327732, em que a diferença
dava exatamente a tarifa de venda. Divergência não conferida vira número
errado na frente de quem paga o salário.

Uso:
    python conciliacao.py --mes 2026-07                 # resumo no terminal
    python conciliacao.py --mes 2026-07 --csv julho.csv # linha a linha
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Iterable, Mapping, Optional

# Centavo de arredondamento do ML não pode virar achado: 290 falsos positivos
# fariam a conferência inteira ser ignorada.
TOLERANCIA = 0.02

COMPONENTES = ("produto", "tarifa_venda", "envios", "cancelamentos",
               "parcelamento")

CABECALHO = ("order_id;claim_id;data;sku;produto;motivo;desfecho;"
             "total;componentes;divergencia")


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def classificar(row: Mapping[str, Any]) -> str:
    """prejuizo · coberto · revertido · pendente."""
    t = _num(row.get("total"))
    if t is None:
        return "pendente"
    if t < 0:
        return "prejuizo"
    if t > 0:
        return "revertido"
    return "coberto"


def soma_componentes(row: Mapping[str, Any]) -> Optional[float]:
    """Soma das parcelas que o painel do ML mostra. None quando nenhuma foi
    coletada — página não coletada não é página que fecha."""
    valores = [_num(row.get(c)) for c in COMPONENTES]
    presentes = [v for v in valores if v is not None]
    if not presentes:
        return None
    return round(sum(presentes), 2)


def divergencia(row: Mapping[str, Any]) -> Optional[float]:
    """Total exibido menos soma das parcelas. None quando não há o que
    conferir — devolver 0,0 ali diria 'confere' sem ter conferido."""
    total = _num(row.get("total"))
    soma = soma_componentes(row)
    if total is None or soma is None:
        return None
    return round(total - soma, 2)


def tem_divergencia(row: Mapping[str, Any],
                    tolerancia: float = TOLERANCIA) -> bool:
    d = divergencia(row)
    return d is not None and abs(d) > tolerancia


def _dedup(rows: Iterable[Mapping[str, Any]]) -> list:
    """Um claim tem várias chaves de estado em slack_notificados e o JOIN
    devolve uma linha por chave. Sem isto o prejuízo sai dobrado."""
    vistos, unicos = set(), []
    for r in rows:
        ident = r.get("claim_id") or r.get("order_id")
        if ident is not None and ident in vistos:
            continue
        if ident is not None:
            vistos.add(ident)
        unicos.append(r)
    return unicos


def _por_pedido(rows: Iterable[Mapping[str, Any]]) -> list:
    """Uma linha por PEDIDO — `meli_page_saldos` é por pedido, não por claim.

    Julho tem o pedido 2000017031981690 com dois claims fechados. Somar por
    claim contaria o mesmo -R$ 144,15 duas vezes e inflaria o prejuízo do mês.
    O atendimento continua contando dois (o SAC trabalhou duas vezes); o que
    se conta uma vez é o dinheiro.
    """
    vistos, unicos = set(), []
    for r in rows:
        oid = r.get("order_id")
        if oid is not None and oid in vistos:
            continue
        if oid is not None:
            vistos.add(oid)
        unicos.append(r)
    return unicos


def consolidar(rows: Iterable[Mapping[str, Any]]) -> dict:
    linhas = _dedup(rows)
    grupos = {"prejuizo": [], "coberto": [], "revertido": [], "pendente": []}
    for r in linhas:
        grupos[classificar(r)].append(r)

    prejuizo = round(sum(_num(r["total"])
                         for r in _por_pedido(grupos["prejuizo"])), 2)
    revertido = round(sum(_num(r["total"])
                          for r in _por_pedido(grupos["revertido"])), 2)
    divergentes = [r for r in linhas if tem_divergencia(r)]
    total = len(linhas)
    apurados = total - len(grupos["pendente"])

    return {
        "casos": total,
        "n_prejuizo": len(grupos["prejuizo"]),
        "n_coberto": len(grupos["coberto"]),
        "n_revertido": len(grupos["revertido"]),
        "n_pendente": len(grupos["pendente"]),
        "prejuizo": prejuizo,
        "revertido": revertido,
        "cobertura_pct": round(100.0 * apurados / total, 1) if total else 0.0,
        "n_divergentes": len(divergentes),
        "divergentes": divergentes,
        "grupos": grupos,
    }


def fmt_valor(v: Any) -> str:
    """Vazio para ausente. "0,00" afirmaria que foi medido e deu zero."""
    n = _num(v)
    if n is None:
        return ""
    return f"{n:.2f}".replace(".", ",")


def fmt_brl(v: Any) -> str:
    n = _num(v) or 0.0
    s = f"{abs(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-R$ {s}" if n < 0 else f"R$ {s}"


def linha_csv(row: Mapping[str, Any]) -> str:
    def limpo(v):
        return str(v or "").replace(";", ",").replace("\n", " ").strip()

    return ";".join([
        str(row.get("order_id") or ""),
        str(row.get("claim_id") or ""),
        str(row.get("date_updated") or "")[:10],
        limpo(row.get("item_sku")),
        limpo(row.get("item_title"))[:60],
        limpo(row.get("reason_label"))[:40],
        classificar(row),
        fmt_valor(row.get("total")),
        fmt_valor(soma_componentes(row)),
        fmt_valor(divergencia(row)),
    ])


# --- I/O -------------------------------------------------------------------

SQL = """
    SELECT d.order_id, d.claim_id, d.item_sku, d.item_title, d.reason_label,
           d.date_updated, d.claim_type, d.claim_status,
           s.total, s.produto, s.tarifa_venda, s.envios,
           s.cancelamentos, s.parcelamento, s.status_pagina
    FROM ml_devolucoes d
    LEFT JOIN meli_page_saldos s ON s.order_id = d.order_id
    WHERE d.claim_status = 'closed'
      AND d.date_updated ~ '^[0-9]{4}-'
      AND d.date_updated::timestamptz >= %(ini)s::timestamptz
      AND d.date_updated::timestamptz <  %(fim)s::timestamptz
    ORDER BY s.total NULLS LAST
"""


def _proximo_mes(ano: int, mes: int) -> tuple[int, int]:
    return (ano + 1, 1) if mes == 12 else (ano, mes + 1)


def buscar(mes: str) -> list[dict]:
    from src.db.connection import get_db_connection

    ano, m = int(mes[:4]), int(mes[5:7])
    pa, pm = _proximo_mes(ano, m)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(SQL, {"ini": f"{ano}-{m:02d}-01", "fim": f"{pa}-{pm:02d}-01"})
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mes", required=True, help="AAAA-MM")
    ap.add_argument("--csv", help="grava linha a linha para conferência")
    args = ap.parse_args()

    linhas = buscar(args.mes)
    if not linhas:
        print(f"nenhum caso fechado em {args.mes} — nada a conciliar")
        return 1

    c = consolidar(linhas)
    print("=" * 68)
    print(f"CONCILIAÇÃO {args.mes}")
    print("=" * 68)
    print(f"  casos fechados      : {c['casos']}")
    print(f"  🔴 prejuízo         : {c['n_prejuizo']:>3}  {fmt_brl(c['prejuizo'])}")
    print(f"  ⚪ ML cobriu        : {c['n_coberto']:>3}  sem prejuízo nem ganho")
    print(f"  🟢 revertidos       : {c['n_revertido']:>3}  {fmt_brl(c['revertido'])}"
          f"  (receita de venda, não ganho da disputa)")
    print(f"  ⏳ sem apuração     : {c['n_pendente']:>3}")
    print(f"  apurado             : {c['cobertura_pct']}%")
    print()
    if c["n_divergentes"]:
        print(f"  ⚠ {c['n_divergentes']} caso(s) em que as parcelas do painel do ML")
        print(f"    não somam o Total exibido — conferir antes de apresentar:")
        for r in c["divergentes"][:10]:
            print(f"      {r['order_id']}  total={fmt_valor(r['total'])}  "
                  f"parcelas={fmt_valor(soma_componentes(r))}  "
                  f"dif={fmt_valor(divergencia(r))}")
        if c["n_divergentes"] > 10:
            print(f"      e mais {c['n_divergentes'] - 10} — veja o CSV")
    else:
        print("  ✓ todas as páginas do ML fecham com o próprio Total")

    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig") as fh:
            fh.write(CABECALHO + "\n")
            for r in _dedup(linhas):
                fh.write(linha_csv(r) + "\n")
        print(f"\n  linha a linha em {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
