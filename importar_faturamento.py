"""Importa os relatórios de Faturamento ML (baixados via RPA) — a fonte
DEFINITIVA de tarifas por pedido (venda, envio, devolução, cancelamentos),
direto do sistema de cobrança do Mercado Livre. Substitui a estimativa
'2× frete' do motor por valores reais, linha a linha.
"""
from __future__ import annotations

import sys
import glob
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
from psycopg2.extras import execute_values
from src.db.connection import get_db_connection

_DDL = """
CREATE TABLE IF NOT EXISTS faturamento_ml (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT,
    numero_tarifa BIGINT,
    data_tarifa DATE,
    detalhe TEXT,
    valor NUMERIC(14,2),
    tarifa_cancelada BOOLEAN,
    numero_envio BIGINT,
    mes_arquivo TEXT,
    UNIQUE (numero_tarifa, mes_arquivo)
)
"""


def main() -> None:
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(_DDL)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fat_order ON faturamento_ml (order_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fat_detalhe ON faturamento_ml (detalhe)")
    conn.commit()

    total = 0
    for f in sorted(glob.glob(str(ROOT / "tmp_csvs" / "faturamento_ml_*.xlsx"))):
        mes = Path(f).stem.split("_")[2]  # ex: junho2026
        df = pd.read_excel(f, engine="calamine", sheet_name="REPORT", header=7)
        df = df.dropna(subset=["Número da tarifa"])
        rows = []
        for _, r in df.iterrows():
            oid = r.get("Número da venda")
            try:
                oid = int(oid) if pd.notna(oid) else None
            except (ValueError, TypeError):
                oid = None
            try:
                num_tar = int(r["Número da tarifa"])
            except (ValueError, TypeError):
                continue
            cancelada = str(r.get("Tarifa cancelada") or "").strip() not in ("", "nan")
            env = r.get("Número da envío")
            try:
                env = int(env) if pd.notna(env) else None
            except (ValueError, TypeError):
                env = None
            rows.append((oid, num_tar, r.get("Data da tarifa"), str(r.get("Detalhe") or "")[:120],
                        float(r.get("Valor da tarifa") or 0), cancelada, env, mes))
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO faturamento_ml
                    (order_id, numero_tarifa, data_tarifa, detalhe, valor, tarifa_cancelada, numero_envio, mes_arquivo)
                VALUES %s ON CONFLICT (numero_tarifa, mes_arquivo) DO NOTHING
            """, rows, page_size=1000)
        conn.commit()
        total += len(rows)
        print(f"  {mes}: {len(rows):,} linhas ({Path(f).name})")

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT order_id) FROM faturamento_ml")
        n, nord = cur.fetchone()
    conn.close()
    print(f"\n✓ total importado: {n:,} linhas | {nord:,} pedidos distintos ({total:,} processadas nesta rodada)")


if __name__ == "__main__":
    main()
