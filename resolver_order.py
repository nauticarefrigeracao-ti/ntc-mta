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

# Formato do order_id do ML -- MEDIDO na API em 30/07/2026, nao suposto:
#   10 digitos (5.099) -> PEDIDO antigo legitimo  (6/6 abrem em /orders/)
#   11 digitos (2.922) -> SHIPMENT                (8/8 em /shipments/)
#   16 digitos (10.092, 2000…) -> pedido novo
#
# A primeira versao usava "< 15 digitos = shipment": teria tentado resolver
# 5.099 pedidos VALIDOS. Nada foi corrompido porque resolver_order_id so grava
# quando a API devolve um order plausivel, e esses dao 404 em /shipments --
# mas era desperdicio de chamada e, na invariante, falso-positivo eterno
# (o shipment de 11 resolve para um order de 10).
DIGITOS_SHIPMENT = 11
# Tamanhos de order_id que o ML de fato usa (antigo e novo).
DIGITOS_ORDER_VALIDOS = (10, 16)


def parece_order_valido(valor) -> bool:
    """True se o valor tem cara de pedido do ML. Usado para NAO aceitar
    qualquer coisa que a API devolva -- trocar um link quebrado por outro
    link quebrado nao seria correcao."""
    if valor is None:
        return False
    texto = str(valor).strip()
    return texto.isdigit() and len(texto) in DIGITOS_ORDER_VALIDOS


def parece_shipment(valor) -> bool:
    """True se o valor gravado como order_id na verdade e um shipment id."""
    if valor is None:
        return False
    texto = str(valor).strip()
    if not texto.isdigit():
        return False
    return len(texto) == DIGITOS_SHIPMENT


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
    if not parece_order_valido(oid):
        return None
    return int(oid)


def em_lotes(itens, tamanho: int):
    """Fatia a lista em levas. O Neon derruba conexao ociosa/longa
    ("SSL connection has been closed unexpectedly") no meio de um run de 12k;
    sem lote, o commit final leva junto tudo que ja tinha sido resolvido."""
    itens = list(itens)
    if tamanho <= 0:
        tamanho = len(itens) or 1
    for i in range(0, len(itens), tamanho):
        yield itens[i:i + tamanho]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="mede sem gravar")
    ap.add_argument("--limite", type=int, default=500)
    ap.add_argument("--lote", type=int, default=200,
                    help="reconecta e commita a cada N (default 200)")
    args = ap.parse_args()

    from src.db.connection import get_db_connection

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT claim_id, order_id FROM ml_devolucoes "
                "WHERE order_id IS NOT NULL "
                "  AND LENGTH(order_id::text) = %s "
                "ORDER BY date_updated DESC NULLS LAST LIMIT %s",
                (DIGITOS_SHIPMENT, args.limite))
            alvos = cur.fetchall()

        print(f"claims com shipment gravado como order_id: {len(alvos)}")
        if args.dry_run:
            print("(dry-run) amostra:")
            for claim_id, oid in alvos[:10]:
                novo = resolver_order_id(oid)
                print(f"  claim {claim_id}: {oid} -> {novo or 'NAO RESOLVIDO'}")
            return 0
    finally:
        conn.close()

    # backup antes de escrever: o UPDATE troca uma chave usada em joins,
    # entao precisa ser desfazivel linha a linha.
    import csv
    from datetime import datetime
    backup = f"backup_order_id_{datetime.now():%Y%m%d_%H%M%S}.csv"

    resolvidos = falhas = 0
    with open(backup, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["claim_id", "order_id_antigo", "order_id_novo"])
        fh.flush()

        for n, lote in enumerate(em_lotes(alvos, args.lote), 1):
            # conexao NOVA por lote: uma queda so custa o lote corrente, e o
            # que ja foi commitado fica. O SELECT filtra por tamanho, entao
            # rodar de novo retoma naturalmente de onde parou.
            conn = get_db_connection()
            try:
                with conn.cursor() as cur:
                    for claim_id, oid in lote:
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
                conn.commit()
                print(f"  lote {n}: {resolvidos} resolvidos / {falhas} sem resolver")
            except Exception as exc:
                # FAIL-LOUD: diz qual lote caiu e por que, em vez de morrer mudo
                print(f"  lote {n} FALHOU: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    print(f"\nresolvidos={resolvidos}  nao resolvidos={falhas}")
    print(f"backup (para desfazer): {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
