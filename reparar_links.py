"""Conserta os links que dao 404 nas mensagens ja publicadas no #sac.

Conferencia mensagem por mensagem contra a API do ML, 05/08/2026, paginada
(448 mensagens, 172 pedidos citados). 25 links nao abrem:

    publicado   /vendas/47386687921/detalhe   -> 404
    correto     /vendas/2000017121756758/detalhe

`47386687921` e o **shipment_id**, nao o pedido. Os conferidos batem um a um
com `ml_devolucoes.shipment_id`. O banco ja tem o `order_id` certo hoje -- o
numero errado ficou congelado no texto publicado em julho.

Isso tambem explica as "26 lacunas" que a auditoria acusava: as mensagens
existem, mas citam um numero que nao e o pedido, entao nem a auditoria nem a
Maria conseguem ligar mensagem e caso.

Por que reescrever em vez de reenviar: a mensagem ja foi lida, ja tem thread e
ja tem lugar na linha do tempo. Mandar outra criaria duplicata -- e duplicata
no canal da diretoria levanta "entao qual das duas vale?".

Uso:
    python reparar_links.py --dry-run      # mostra o que mudaria
    python reparar_links.py                # aplica
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Um pedido do Mercado Livre tem 10 ou 16 digitos -- medido, nao presumido.
# 11 digitos e shipment. Presumir "id curto = shipment" ja custou uma
# invariante que acusou 5.099 pedidos validos; aqui a regra e a inversa e
# igualmente estreita: SO 11 digitos entram no reparo.
LINKS = re.compile(r"mercadolivre\.com\.br/vendas/(\d{11})(?=/detalhe)")

CANAL_PADRAO = "#sac"
DIAS_PADRAO = 45


def extrair_shipments(texto: str) -> list[str]:
    """Shipments citados como se fossem pedido, sem repetir e em ordem."""
    vistos, saida = set(), []
    for m in LINKS.findall(texto or ""):
        if m not in vistos:
            vistos.add(m)
            saida.append(m)
    return saida


def precisa_reparo(texto: str) -> bool:
    return bool(extrair_shipments(texto))


def texto_da_correcao(shipments: list[str], mapa: dict) -> str:
    """A resposta que fica embaixo da mensagem com link quebrado.

    Diz o que aconteceu sem jargao: quem le e a Maria e a diretoria, e o que
    eles precisam e o link que abre.
    """
    linhas = ["⚠️ *Link acima não abre* — o número publicado era o do envio, "
              "não o da venda. O link certo:"]
    for s in shipments:
        pedido = mapa.get(s)
        if not pedido:
            continue
        linhas.append(
            f"➡️ <https://www.mercadolivre.com.br/vendas/{pedido}/detalhe"
            f"|Abrir a venda {pedido} no Mercado Livre>")
    return "\n".join(linhas)


def reescrever_link(texto: str, mapa: dict) -> str:
    """Troca shipment por pedido, na URL E no texto visivel do link.

    Shipment sem pedido conhecido fica como esta: link errado e pior que link
    quebrado, porque ninguem percebe.
    """
    novo = texto or ""
    for ship in extrair_shipments(novo):
        pedido = mapa.get(ship)
        if not pedido:
            continue
        novo = novo.replace(ship, str(pedido))
    return novo


# --- I/O -------------------------------------------------------------------

def mapa_shipment_para_pedido(shipments: list[str]) -> dict:
    from src.db.connection import get_db_connection

    if not shipments:
        return {}
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT shipment_id::text, order_id::text
        FROM ml_devolucoes
        WHERE shipment_id::text = ANY(%s)
          AND order_id IS NOT NULL
    """, (shipments,))
    return {s: o for s, o in cur.fetchall()}


def _historico(cid: str, dias: int) -> list:
    import time as _time

    import slack_client

    oldest = _time.time() - dias * 86400
    msgs, cursor, paginas = [], None, 0
    while paginas < 40:
        p = {"channel": cid, "limit": "200", "oldest": f"{oldest:.6f}"}
        if cursor:
            p["cursor"] = cursor
        body = slack_client._api("conversations.history", p, get=True)
        if body is None:
            raise RuntimeError("conversations.history nao respondeu")
        msgs += body.get("messages") or []
        cursor = (body.get("response_metadata") or {}).get("next_cursor") or ""
        paginas += 1
        if not cursor:
            break
    return msgs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canal", default=CANAL_PADRAO)
    ap.add_argument("--dias", type=int, default=DIAS_PADRAO)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--modo", choices=("thread", "editar"), default="thread",
                    help="thread: responde embaixo da mensagem quebrada "
                         "(unico caminho quando a mensagem e de outro app); "
                         "editar: reescreve in-place")
    args = ap.parse_args()

    import slack_client

    cid = None
    for c in slack_client.listar_canais() or []:
        if c.get("name") == args.canal.lstrip("#"):
            cid = c.get("id")
    if not cid:
        print(f"canal {args.canal} nao encontrado")
        return 1

    msgs = _historico(cid, args.dias)
    alvos = [m for m in msgs if precisa_reparo(m.get("text") or "")]
    print(f"  mensagens lidas : {len(msgs)}")
    print(f"  com link 404    : {len(alvos)}")
    if not alvos:
        print("  nada a reparar")
        return 0

    todos = sorted({s for m in alvos
                    for s in extrair_shipments(m.get("text") or "")})
    mapa = mapa_shipment_para_pedido(todos)
    print(f"  shipments       : {len(todos)}  |  com pedido conhecido: {len(mapa)}")

    sem_pedido = [s for s in todos if s not in mapa]
    if sem_pedido:
        print(f"  ⚠ sem pedido no banco (ficam como estao): "
              f"{', '.join(sem_pedido[:8])}")

    feitos = falhas = 0
    for m in alvos:
        texto = m.get("text") or ""
        novo = reescrever_link(texto, mapa)
        if novo == texto:
            continue
        ships = [s for s in extrair_shipments(texto) if s in mapa]
        if args.dry_run:
            print(f"    {m.get('ts')}: {', '.join(ships)} -> "
                  f"{', '.join(mapa[s] for s in ships)}")
            feitos += 1
            continue

        if args.modo == "editar":
            r = slack_client.update_message(cid, m["ts"], novo)
        else:
            # CORRECAO EM THREAD, nao no canal.
            #
            # `chat.update` devolve `cant_update_message`: as mensagens de
            # julho sairam pelo bot B0BHP9ZHZEX (NTC Painel) e o token de hoje
            # e B0BKEBB6QKT (SAC Nautica). O Slack nao deixa um app editar
            # mensagem de outro -- e isso nao se resolve com escopo.
            #
            # A resposta em thread fica exatamente onde a pessoa clicou no
            # link quebrado, e nao acrescenta nada a linha do tempo do canal.
            # Mensagem nova no canal criaria duplicata, e duplicata no canal
            # da diretoria levanta "entao qual das duas vale?".
            r = slack_client.post_message(cid, texto_da_correcao(ships, mapa),
                                          thread_ts=m["ts"])
        if r:
            feitos += 1
        else:
            falhas += 1
            print(f"    FALHOU ts={m.get('ts')}", file=sys.stderr)

    verbo = "seriam corrigidas" if args.dry_run else "corrigidas"
    print(f"\n  {feitos} mensagem(ns) {verbo} | {falhas} falha(s)")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
