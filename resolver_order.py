"""Resolve shipment_id gravado como order_id nos claims historicos.

Claims de cancel_purchase trazem um SHIPMENT id em resource_id, nao o id do
pedido. Foi isso que produziu o link 404 que o Gabriel abriu. O sync ja
resolve nos claims ABERTOS; os fechados seguiram com o shipment gravado e,
como meli_page_saldos e chaveado por order_id, esses casos nunca casam --
caem como "conciliacao pendente" no fechamento do chefe e somem do numero.

Uso:
    python resolver_order.py --dry-run     # so mede, nao grava
    python resolver_order.py               # resolve e grava
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from src.api import ml_client

# Pedido do ML tem 15+ digitos (padrao 2000...). Abaixo disso e shipment.
DIGITOS_ORDER_REAL = 15


def parece_shipment(valor) -> bool:
    """True se o valor gravado como order_id na verdade e um shipment id."""
    if valor is None:
        return False
    texto = str(valor).strip()
    if not texto.isdigit():
        return False
    return len(texto) < DIGITOS_ORDER_REAL


def resolver_order_id(valor) -> Optional[int]:
    """Devolve o order_id real. Se ja for real, devolve como esta. Se for
    shipment, consulta a API. None quando nao da para resolver -- trocar um
    link quebrado por outro nao seria correcao."""
    if not parece_shipment(valor):
        return int(valor) if valor is not None else None
    envio = ml_client.get_shipment(valor)
    if not envio:
        return None
    oid = envio.get("order_id")
    if oid is None or parece_shipment(oid):
        return None
    return int(oid)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="mede sem gravar")
    ap.add_argument("--limite", type=int, default=500)
    args = ap.parse_args()

    from src.db.connection import get_db_connection

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT claim_id, order_id FROM ml_devolucoes "
                "WHERE order_id IS NOT NULL "
                "  AND LENGTH(order_id::text) < %s "
                "ORDER BY date_updated DESC NULLS LAST LIMIT %s",
                (DIGITOS_ORDER_REAL, args.limite))
            alvos = cur.fetchall()

        print(f"claims com shipment gravado como order_id: {len(alvos)}")
        if args.dry_run:
            print("(dry-run) amostra:")
            for claim_id, oid in alvos[:10]:
                novo = resolver_order_id(oid)
                print(f"  claim {claim_id}: {oid} -> {novo or 'NAO RESOLVIDO'}")
            return 0

        # backup antes de escrever: o UPDATE troca uma chave usada em joins,
        # entao precisa ser desfazivel linha a linha.
        import csv
        from datetime import datetime
        backup = f"backup_order_id_{datetime.now():%Y%m%d_%H%M%S}.csv"

        resolvidos = falhas = 0
        with open(backup, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["claim_id", "order_id_antigo", "order_id_novo"])
            with conn.cursor() as cur:
                for claim_id, oid in alvos:
                    novo = resolver_order_id(oid)
                    if not novo:
                        falhas += 1
                        continue
                    w.writerow([claim_id, oid, novo])
                    fh.flush()
                    cur.execute(
                        "UPDATE ml_devolucoes SET order_id = %s "
                        "WHERE claim_id = %s AND order_id = %s",
                        (novo, claim_id, oid))
                    resolvidos += 1
                    if resolvidos % 25 == 0:
                        conn.commit()
                        print(f"  ... {resolvidos} resolvidos")
            conn.commit()
        print(f"\nresolvidos={resolvidos}  nao resolvidos={falhas}")
        print(f"backup (para desfazer): {backup}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
