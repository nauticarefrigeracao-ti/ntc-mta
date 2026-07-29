"""Backfill de order_total em ml_devolucoes.
================================================================================
Completa os registros que tem claim_status = 'closed' e order_total IS NULL,
buscando diretamente em /orders/{order_id} no Mercado Livre.
Uso:
    python scripts/backfill_order_total.py --dry-run
    python scripts/backfill_order_total.py
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.api import ml_client
from src.db.connection import db_conn, dict_cursor

def backfill(dry_run: bool = False):
    with db_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute("""
                SELECT claim_id, order_id 
                FROM ml_devolucoes 
                WHERE claim_status = 'closed' AND (order_total IS NULL OR order_total = 0)
            """)
            rows = cur.fetchall()
            
    total_found = len(rows)
    print(f"Encontrados {total_found} processos fechados sem order_total.")
    
    if not rows:
        return
        
    atualizados = 0
    erros = 0
    t0 = time.monotonic()
    
    with db_conn() as conn:
        for i, r in enumerate(rows):
            oid = r["order_id"]
            if not oid:
                continue
                
            try:
                resp = ml_client._get(f"/orders/{oid}")
                if not resp:
                    erros += 1
                    continue
                    
                total = float(resp.get("paid_amount") or resp.get("total_amount") or 0)
                if dry_run:
                    if i < 10:  # Mostrar apenas alguns no dry run
                        print(f"[DRY-RUN] claim_id={r['claim_id']} order_id={oid} -> order_total={total}")
                    atualizados += 1
                    continue
                    
                with conn.cursor() as cur:
                    cur.execute("UPDATE ml_devolucoes SET order_total = %s WHERE claim_id = %s", (total, r["claim_id"]))
                
                atualizados += 1
                if atualizados % 100 == 0:
                    conn.commit()
                    dt_s = time.monotonic() - t0
                    print(f"  progresso: {atualizados}/{total_found} ({dt_s:,.0f}s)", flush=True)
            except Exception as e:
                print(f"Erro no claim_id={r['claim_id']}: {e}")
                erros += 1
                
            time.sleep(0.05)  # Rate limit suave (20/s)
            
        if not dry_run:
            conn.commit()
            
    dt_s = time.monotonic() - t0
    if dry_run:
        print(f"✓ DRY-RUN finalizado. {atualizados} simulados em {dt_s:,.1f}s.")
    else:
        print(f"✓ Backfill concluído: {atualizados} registros atualizados, {erros} erros em {dt_s:,.1f}s.")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Mostra os valores sem atualizar o banco")
    args = ap.parse_args()
    backfill(args.dry_run)
