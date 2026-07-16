"""Sync de pedidos NOVOS via ML API → tabela orders (+ order_items).
====================================================================
A API do Meli é a fonte primária; CSVs de venda/pós-venda/MP viram apenas
conciliação/validação. Este sync garante que a base `orders` acompanha a
plataforma em tempo quase-real (roda dentro do ml_live_poll a cada ciclo).

Uso standalone:
    python scripts/ml_orders_sync.py --desde 2026-05-01
    python scripts/ml_orders_sync.py --dias 7        # últimos 7 dias

O que grava (só o que a API REALMENTE informa — nada inventado):
    order_id, data_venda, estado (rótulo do status), total/pago,
    receita de produtos, tarifa de venda (sale_fee dos itens),
    cancelamentos (pedido cancelado após pago → valor devolvido),
    source_file='api_sync' + order_items (sku, preço, unidades).
Frete do vendedor e ajustes de devolução NÃO vêm nesta API — ficam por conta
do coletor de saldos (meli_page_saldos) e dos relatórios MP (conciliação).
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.api import ml_client
from src.db.connection import get_db_connection

_ESTADO_PT = {
    "paid":          "Pagamento aprovado",
    "confirmed":     "Confirmada",
    "payment_required": "Aguardando pagamento",
    "payment_in_process": "Pagamento em análise",
    "partially_paid": "Parcialmente paga",
    "cancelled":     "Cancelada",
    "invalid":       "Inválida",
}


def _seller_id() -> str:
    me = ml_client._get("/users/me")
    if not me or not me.get("id"):
        raise RuntimeError("não consegui obter o seller_id via /users/me (token?)")
    return str(me["id"])


def _iter_orders(seller: str, date_from: str, date_to: str | None = None):
    """Pagina /orders/search da mais recente para trás, filtrando por data.

    A ML capa o offset em 10.000 — para janelas com mais pedidos que isso,
    fatiar por mês via date_to (sync_orders(date_from, date_to=...)).
    """
    offset, limit = 0, 50
    ate = f"&order.date_created.to={date_to}T23:59:59.999-03:00" if date_to else ""
    while True:
        page = ml_client._get(
            f"/orders/search?seller={seller}&order.date_created.from={date_from}T00:00:00.000-03:00"
            f"{ate}&sort=date_desc&offset={offset}&limit={limit}"
        )
        if not page:
            return
        results = page.get("results") or []
        if not results:
            return
        yield from results
        offset += limit
        total = (page.get("paging") or {}).get("total", 0)
        if offset >= min(total, 10000):  # cap de offset da ML
            return


def sync_orders(date_from: str, quiet: bool = False, date_to: str | None = None) -> dict:
    from psycopg2.extras import execute_values

    seller = _seller_id()
    conn = get_db_connection()
    novos = atualizados = itens = paginas = 0
    t0 = time.monotonic()
    try:
        with conn.cursor() as cur:
            # sequence do serial pode estar atrás dos ids importados via CSV
            cur.execute("""
                SELECT setval(pg_get_serial_sequence('order_items','id'),
                              GREATEST(COALESCE((SELECT MAX(id) FROM order_items), 1), 1))
            """)
            conn.commit()

        buf_ord: list[tuple] = []
        buf_it:  list[tuple] = []

        def _flush():
            nonlocal novos, atualizados, itens, buf_ord, buf_it
            if not buf_ord:
                return
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM orders WHERE source_file='api_sync'")
                antes = cur.fetchone()[0]
                execute_values(cur, """
                    INSERT INTO orders (order_id, data_venda, estado, descricao_status,
                        total_brl, receita_produtos_brl, receita_envio_brl,
                        tarifa_venda_impostos_brl, tarifas_envio_brl,
                        cancelamentos_reembolsos_brl, dinheiro_liberado, source_file)
                    VALUES %s
                    ON CONFLICT (order_id) DO UPDATE SET
                        estado = EXCLUDED.estado,
                        cancelamentos_reembolsos_brl = CASE
                            WHEN EXCLUDED.estado = 'Cancelada' THEN EXCLUDED.cancelamentos_reembolsos_brl
                            ELSE orders.cancelamentos_reembolsos_brl END
                """, buf_ord, page_size=500)
                cur.execute("SELECT COUNT(*) FROM orders WHERE source_file='api_sync'")
                n = cur.fetchone()[0] - antes
                novos += n
                atualizados += len(buf_ord) - n
                if buf_it:
                    execute_values(cur, """
                        INSERT INTO order_items (order_id, sku, preco_unitario, unidades)
                        SELECT v.oid, v.sku, v.preco, v.qtd
                        FROM (VALUES %s) AS v(oid, sku, preco, qtd)
                        WHERE NOT EXISTS (
                            SELECT 1 FROM order_items oi WHERE oi.order_id = v.oid AND oi.sku = v.sku
                        )
                    """, buf_it, page_size=500)
                    itens += len(buf_it)
            conn.commit()
            buf_ord, buf_it = [], []

        for o in _iter_orders(seller, date_from, date_to):
            oid = str(o.get("id") or "")
            if not oid:
                continue
            status  = str(o.get("status") or "")
            estado  = _ESTADO_PT.get(status, status.capitalize())
            pago    = float(o.get("paid_amount") or 0)
            its     = o.get("order_items") or []
            receita = sum(float(i.get("unit_price") or 0) * int(i.get("quantity") or 0) for i in its)
            tarifa  = sum(float(i.get("sale_fee") or 0) * int(i.get("quantity") or 0) for i in its)
            cancel  = -pago if status == "cancelled" and pago > 0 else 0.0
            total_liq = 0.0 if status == "cancelled" else round(receita - tarifa, 2)
            buf_ord.append((oid, o.get("date_created"), estado, f"status ML API: {status}",
                            total_liq, receita, 0, -tarifa, 0, cancel, pago, "api_sync"))
            for i in its:
                sku = str((i.get("item") or {}).get("seller_sku")
                          or (i.get("item") or {}).get("seller_custom_field") or "").strip()
                if sku:
                    buf_it.append((oid, sku, float(i.get("unit_price") or 0),
                                   int(i.get("quantity") or 0)))
            if len(buf_ord) >= 500:
                _flush()
                paginas += 10
                if not quiet:
                    dt_s = time.monotonic() - t0
                    print(f"  sync: {novos+atualizados:,} pedidos processados ({dt_s:,.0f}s)", flush=True)
        _flush()
    finally:
        conn.close()
    dt_s = time.monotonic() - t0
    if not quiet:
        print(f"  ✓ sync orders API: {novos:,} novos | {atualizados:,} atualizados | "
              f"{itens:,} itens | {dt_s:,.0f}s (desde {date_from})")
    return {"novos": novos, "atualizados": atualizados, "itens": itens}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Sync de pedidos via ML API → orders.")
    ap.add_argument("--desde", help="data inicial YYYY-MM-DD")
    ap.add_argument("--dias", type=int, default=30, help="alternativa: últimos N dias (padrão 30)")
    args = ap.parse_args()
    date_from = args.desde or (datetime.now(timezone.utc) - timedelta(days=args.dias)).strftime("%Y-%m-%d")
    print(f"SYNC ORDERS ML API — desde {date_from}")
    sync_orders(date_from)
