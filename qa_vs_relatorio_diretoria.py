"""QA de VERDADE EXTERNA — painel × relatório consolidado da diretoria.
================================================================================
O Excel consolidado do chefe (Prejuizo_Real_Devolucoes_*.xlsx) é a régua de
negócio: prejuízo real = TOTAL LÍQUIDO negativo da venda. Este script cruza,
pedido a pedido, o TOTAL LÍQUIDO do relatório com o saldo coletado da
plataforma (meli_page_saldos) e mede paridade e cobertura por mês.

Divergência esperada e LEGÍTIMA: pedidos cujo estado mudou DEPOIS do corte do
relatório (a plataforma é mais fresca) — o script lista para inspeção.

Uso:
    python scripts/qa_vs_relatorio_diretoria.py [caminho_do_xlsx]
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.db.connection import get_db_connection

XLSX_PADRAO = Path(r"c:\Users\Pichau\Desktop\Bok\Prejuizo_Real_Devolucoes_Jan-Jun2026_Consolidado.xlsx")
MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
         "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


def main() -> int:
    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else XLSX_PADRAO
    xl = pd.ExcelFile(xlsx)
    det = []
    for i, m in enumerate(MESES, start=1):
        aba = f"{m} - Detalhe"
        if aba not in xl.sheet_names:
            continue
        d = xl.parse(aba)
        d["mes"] = i
        d["mes_nome"] = m
        det.append(d)
    chefe = pd.concat(det, ignore_index=True)
    chefe["order_id"] = chefe["ID Pedido"].astype(str).str.replace(r"\.0$", "", regex=True)
    chefe = chefe.rename(columns={"TOTAL LÍQUIDO (R$)": "liq", "Prejuízo Real?": "prej"})
    print(f"Relatório da diretoria: {len(chefe)} disputas | com prejuízo: {(chefe['prej'] == 'SIM').sum()} "
          f"| prejuízo: {chefe.loc[chefe['prej'] == 'SIM', 'liq'].sum():,.2f}")

    conn = get_db_connection()
    plat = pd.read_sql("SELECT order_id::text AS order_id, total FROM meli_page_saldos", conn)
    conn.close()
    plat = plat.drop_duplicates("order_id", keep="last")

    m = chefe.merge(plat, on="order_id", how="left")
    tem = m["total"].notna()
    c = m[tem].copy()
    c["diff"] = (c["liq"] - c["total"]).abs()
    bate = int((c["diff"] <= 1.0).sum())
    print(f"Cobertura da plataforma: {tem.sum()}/{len(m)} ({tem.mean() * 100:.1f}%)")
    print(f"PARIDADE (±R$1): {bate}/{len(c)} = {bate / max(len(c), 1) * 100:.1f}%")

    dv = c[c["diff"] > 1.0].sort_values("diff", ascending=False)
    if len(dv):
        print(f"\n{len(dv)} divergências (plataforma mais fresca que o relatório? inspecionar):")
        print(dv[["order_id", "mes_nome", "liq", "total", "diff", "prej"]].head(15).to_string(index=False))

    print("\nMês | prejuízo relatório | prejuízo plataforma (mesmos ids) | cobertura")
    for i in sorted(m["mes"].unique()):
        g = m[m["mes"] == i]
        gc = g[g["total"].notna()]
        cob = len(gc) / len(g) * 100 if len(g) else 0.0
        print(f"{MESES[i - 1]:10s} | {g.loc[g['prej'] == 'SIM', 'liq'].sum():12,.2f} "
              f"| {gc.loc[gc['total'] < -0.005, 'total'].sum():12,.2f} | {cob:5.1f}%")

    # ids ainda sem saldo coletado → fila para o coletor RPA (--ids-file)
    fila = ROOT / "reports" / "ids_chefe_sem_saldo.txt"
    fila.write_text("\n".join(m.loc[~tem, "order_id"]), encoding="utf-8")
    print(f"\n{(~tem).sum()} ids sem saldo coletado → {fila} (alimenta coletar_saldos_meli --ids-file)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
