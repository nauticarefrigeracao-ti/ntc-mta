"""Estimativa do motor v2 para pedidos 'em conciliação' — disputas recentes
que ainda não têm saldo confirmado pela coleta RPA (>20h de idade).

IMPORTANTE — não substitui o número validado:
  - O painel usa o saldo COLETADO da página como fonte de verdade (184/184
    validado). Este script preenche uma estimativa (motor_saldo.calcular,
    ~65-67% de acurácia medida em QA) APENAS para exibição complementar,
    rotulada como estimativa, nunca somada aos KPIs/totais oficiais.
  - Assim que a coleta confirmar o pedido, a estimativa é ignorada (o saldo
    real da página sempre tem prioridade — ver processar_relatorios_mp.py).

Uso:
    python scripts/estimar_conciliacao.py [--max 300]
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.db.connection import get_db_connection
from src.services import motor_saldo

_DDL = """
CREATE TABLE IF NOT EXISTS motor_estimativas (
    order_id BIGINT PRIMARY KEY,
    saldo_estimado NUMERIC(14,2),
    regras TEXT,
    calculado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def pedidos_em_conciliacao(max_n: int) -> list[int]:
    """Disputas/cancelados sem saldo confirmado na coleta e sem estimativa
    recente (<12h) — mesmo universo do card 'em conciliação' do painel."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                WITH alvo AS (
                    SELECT DISTINCT o.order_id::bigint AS oid
                    FROM orders o
                    WHERE (
                        EXISTS (SELECT 1 FROM ml_devolucoes m WHERE m.order_id = o.order_id::bigint)
                        OR EXISTS (SELECT 1 FROM mp_transactions t WHERE t.order_id = o.order_id::bigint)
                        OR o.estado ILIKE 'Cancelada%%' OR o.estado ILIKE 'Pacote cancelado%%'
                        OR o.cancelamentos_reembolsos_brl::numeric <> 0
                    )
                    AND o.data_venda >= NOW() - interval '120 days'
                )
                SELECT oid FROM alvo
                WHERE oid NOT IN (SELECT order_id FROM meli_page_saldos WHERE total IS NOT NULL
                                   AND coletado_em > NOW() - interval '20 hours')
                  AND oid NOT IN (SELECT order_id FROM motor_estimativas
                                   WHERE calculado_em > NOW() - interval '12 hours')
                LIMIT %s
            """, (max_n,))
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=300)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(_DDL)
    conn.commit()
    conn.close()

    ids = pedidos_em_conciliacao(args.max)
    print(f"{len(ids):,} pedidos em conciliação a estimar")
    if not ids:
        return

    results: list[tuple] = []
    lock = threading.Lock()
    t0 = time.monotonic()

    def um(oid: int):
        m = motor_saldo.calcular(oid)
        if m is not None:
            with lock:
                results.append((oid, m["saldo"], "+".join(m["regras"])))

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(um, oid) for oid in ids]
        for i, _ in enumerate(as_completed(futs), 1):
            if i % 50 == 0:
                print(f"  {i}/{len(ids)}  ({time.monotonic()-t0:.0f}s)", flush=True)

    conn = get_db_connection()
    with conn.cursor() as cur:
        from psycopg2.extras import execute_values
        execute_values(cur, """
            INSERT INTO motor_estimativas (order_id, saldo_estimado, regras)
            VALUES %s
            ON CONFLICT (order_id) DO UPDATE SET
                saldo_estimado=EXCLUDED.saldo_estimado, regras=EXCLUDED.regras, calculado_em=NOW()
        """, results, page_size=500)
    conn.commit()
    conn.close()
    print(f"✓ {len(results):,} estimativas calculadas e salvas ({time.monotonic()-t0:.0f}s)")


if __name__ == "__main__":
    main()
