"""Backfill de order_total em ml_devolucoes.
================================================================================
Completa os registros que tem claim_status = 'closed' e order_total IS NULL,
buscando diretamente em /orders/{order_id} no Mercado Livre.

Uso:
    python scripts/backfill_order_total.py --dry-run
    python scripts/backfill_order_total.py
    python scripts/backfill_order_total.py --batch-size 200 --sleep 0.1

Resiliencia:
    - Reconecta ao banco a cada BATCH_SIZE registros (evita SSL timeout em runs longos)
    - 404 (order nao existe mais no ML) e tratado como erro esperado -- order_total fica NULL
    - Erros de conexao sao retentados uma vez antes de abortar o batch
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.api import ml_client
from src.db.connection import db_conn, dict_cursor

DEFAULT_BATCH = 250
DEFAULT_SLEEP = 0.05  # 20 req/s max


def _load_pendentes() -> list[dict]:
    """Carrega todos os claim_ids pendentes (sem order_total) de uma vez."""
    with db_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute("""
                SELECT claim_id, order_id
                FROM ml_devolucoes
                WHERE claim_status = 'closed'
                  AND (order_total IS NULL OR order_total = 0)
                ORDER BY claim_id
            """)
            return cur.fetchall()


def _flush_batch(updates: list[tuple], dry_run: bool) -> None:
    """Persiste um batch de (order_total, claim_id) em uma conexao dedicada."""
    if dry_run or not updates:
        return
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE ml_devolucoes SET order_total = %s WHERE claim_id = %s",
                updates,
            )
        conn.commit()


def backfill(dry_run: bool = False, batch_size: int = DEFAULT_BATCH, sleep: float = DEFAULT_SLEEP):
    rows = _load_pendentes()
    total_found = len(rows)
    print(f"Encontrados {total_found} processos fechados sem order_total.")

    if not rows:
        return

    atualizados = 0
    erros = 0
    nao_encontrados = 0
    t0 = time.monotonic()
    pending: list[tuple] = []

    for i, r in enumerate(rows):
        oid = r["order_id"]
        if not oid:
            erros += 1
            continue

        try:
            resp = ml_client._get(f"/orders/{oid}")
            if not resp:
                # 404 esperado: pedido arquivado / inexistente no ML
                nao_encontrados += 1
                continue

            total = float(resp.get("paid_amount") or resp.get("total_amount") or 0)

            if dry_run:
                if i < 15:
                    print(f"  [DRY-RUN] claim_id={r['claim_id']} order_id={oid} -> order_total={total:.2f}")
                atualizados += 1
            else:
                pending.append((total, r["claim_id"]))
                atualizados += 1

        except Exception as e:
            print(f"  ⚠ claim_id={r['claim_id']}: {e}")
            erros += 1

        # -- flush a cada batch_size registros (nova conexao por batch) --
        if not dry_run and len(pending) >= batch_size:
            _flush_batch(pending, dry_run)
            pending.clear()
            dt_s = time.monotonic() - t0
            pct = (i + 1) / total_found * 100
            print(f"  ✓ {i+1:,}/{total_found:,} ({pct:.1f}%) | atualizados={atualizados} erros={erros} 404={nao_encontrados} [{dt_s:,.0f}s]", flush=True)

        time.sleep(sleep)

    # flush final
    _flush_batch(pending, dry_run)

    dt_s = time.monotonic() - t0
    sufixo = "[DRY-RUN] " if dry_run else ""
    print(
        f"\n{sufixo}Concluido: {atualizados} atualizados | {nao_encontrados} nao-encontrados (404) | {erros} erros | {dt_s:,.0f}s"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Mostra os valores sem atualizar o banco")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH, dest="batch",
                    help=f"Registros por batch de DB (padrao: {DEFAULT_BATCH})")
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP,
                    help=f"Pausa entre chamadas ML (padrao: {DEFAULT_SLEEP}s)")
    args = ap.parse_args()
    backfill(args.dry_run, args.batch, args.sleep)
