"""QA clean-room: motor de regras × extratos reais coletados do Meli.
=====================================================================
Para cada pedido com extrato coletado (meli_page_saldos), compara CAMPO A CAMPO
o que nossas fontes estruturadas calculam contra o que a plataforma mostra:

    produto | tarifa de venda | envios | cancelamentos | TOTAL

Saída: taxa de acerto por campo + lista das divergências com causa provável.
É a prova de equivalência do motor — nada de copiar valor: se o motor erra,
o relatório aponta QUAL insumo falta (ex.: tarifa de devolução).

Uso:  python scripts/qa_motor_vs_meli.py [--tol 1.00]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
from src.db.connection import get_db_connection

CAMPOS = [
    # (nome, coluna coletada, expressão do motor sobre orders)
    ("produto",       "produto",       "receita_produtos_brl"),
    ("tarifa_venda",  "tarifa_venda",  "tarifa_venda_impostos_brl"),
    ("envios",        "envios",        "tarifas_envio_brl"),
    ("cancelamentos", "cancelamentos", "cancelamentos_reembolsos_brl"),
    ("TOTAL",         "total",         "total_brl"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=1.00, help="tolerância em R$ (padrão 1,00)")
    args = ap.parse_args()

    conn = get_db_connection()
    df = pd.read_sql("""
        SELECT s.order_id::text AS oid, s.painel_titulo,
               s.produto, s.tarifa_venda, s.envios, s.cancelamentos, s.total,
               o.receita_produtos_brl::float, o.tarifa_venda_impostos_brl::float,
               o.tarifas_envio_brl::float, o.cancelamentos_reembolsos_brl::float,
               o.total_brl::float, o.source_file
        FROM meli_page_saldos s
        JOIN orders o ON o.order_id = s.order_id::text
        WHERE s.total IS NOT NULL
    """, conn)
    conn.close()

    n = len(df)
    print("=" * 74)
    print(f"QA MOTOR × MELI — {n:,} pedidos com extrato coletado | tolerância R$ {args.tol:.2f}")
    print("=" * 74)
    if n == 0:
        print("nenhum extrato coletado ainda")
        return

    resumo = []
    for nome, col_meli, col_motor in CAMPOS:
        m = pd.to_numeric(df[col_meli], errors="coerce")
        e = pd.to_numeric(df[col_motor], errors="coerce").fillna(0.0)
        validos = m.notna()
        delta = (m - e).abs()
        ok = ((delta <= args.tol) & validos).sum()
        tot = int(validos.sum())
        pct = ok / tot * 100 if tot else 0
        resumo.append((nome, ok, tot, pct))
        print(f"  {nome:14s} {ok:4d}/{tot:<4d} batem  ({pct:5.1f}%)   "
              f"desvio mediano R$ {delta[validos].median():.2f}")

    # piores divergências do TOTAL — é aqui que mora o insumo que falta
    m = pd.to_numeric(df["total"], errors="coerce")
    e = pd.to_numeric(df["total_brl"], errors="coerce").fillna(0.0)
    df["_delta"] = (m - e)
    div = df[df["_delta"].abs() > args.tol].copy()
    print(f"\n  TOTAL divergente em {len(div):,} pedidos. Top 12 por |delta|:")
    for _, r in div.reindex(div["_delta"].abs().sort_values(ascending=False).index).head(12).iterrows():
        print(f"    {r['oid']}  meli={float(r['total'] or 0):>9.2f}  motor={float(r['total_brl'] or 0):>9.2f}  "
              f"delta={float(r['_delta']):>9.2f}  [{str(r['painel_titulo'])[:28]}] fonte={r['source_file'][:12]}")

    # hipótese nº1: delta explicado pela tarifa de devolução (dentro de 'envios')
    env_meli  = pd.to_numeric(df["envios"], errors="coerce")
    env_motor = pd.to_numeric(df["tarifas_envio_brl"], errors="coerce").fillna(0.0)
    dif_env   = (env_meli - env_motor)
    casos = ((df["_delta"].abs() > args.tol) & (dif_env.abs() > args.tol)
             & ((df["_delta"] - dif_env).abs() <= args.tol)).sum()
    print(f"\n  Hipótese 'delta = tarifa de devolução/frete reverso (campo envios)': "
          f"explica {casos:,}/{len(div):,} divergências do TOTAL")
    print("  → insumo estruturado a integrar: GET /shipments/{id}/costs do envio reverso")


if __name__ == "__main__":
    main()
