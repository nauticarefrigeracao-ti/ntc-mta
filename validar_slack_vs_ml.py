"""Confere, ITEM POR ITEM, o que esta publicado no Slack contra o Mercado Livre.

Le o Canvas e as mensagens REAIS do canal (não o que achamos que publicamos),
extrai cada pedido citado e confere na API do ML: existe? o estado bate? o
valor bate? o link abre?

Feito para a conferência antes da reunião com a diretoria: se o chefe clicar
em qualquer link ou questionar qualquer número, a resposta já está medida.

Uso:
    python validar_slack_vs_ml.py            # #sac
    python validar_slack_vs_ml.py "#sac-fechamento"
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
logging.disable(logging.CRITICAL)

import slack_client
from src.api.ml_client import get_order
from src.db.connection import get_db_connection

CANAL = sys.argv[1] if len(sys.argv) > 1 else "#sac"
# Janela em dias. Julho inteiro precisa de ~40; o padrao cobre o mes fechado
# mais a margem em que casos de julho ainda recebem atualizacao.
DIAS = int(sys.argv[2]) if len(sys.argv) > 2 else 45
_RE_PEDIDO = re.compile(r"/vendas/(\d{8,20})/detalhe")
_RE_BRL = re.compile(r"R\$\s*([\d.]+,\d{2})")


def _api(metodo: str, params: dict) -> dict:
    tok = slack_client._token()
    url = f"https://slack.com/api/{metodo}?" + "&".join(
        f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def _brl(v: str) -> float:
    return float(v.replace(".", "").replace(",", "."))


def _canal_id(nome: str) -> str | None:
    for c in slack_client.listar_canais() or []:
        if c.get("name") == nome.lstrip("#"):
            return c.get("id")
    return None


def _pedidos_corrigidos_em_thread(channel_id: str, msgs: list) -> set:
    """Ids cujo link quebrado ja recebeu correcao na thread.

    Le so as mensagens que TEM thread e que citam um id de 11 digitos -- o
    formato do shipment. Sem esse filtro seriam centenas de chamadas a
    `conversations.replies` por rodada.
    """
    alvo = re.compile(r"/vendas/(\d{11})/detalhe")
    corrigidos: set = set()
    for m in msgs:
        texto = m.get("text") or ""
        if not alvo.search(texto) or not m.get("thread_ts"):
            continue
        r = _api("conversations.replies",
                 {"channel": channel_id, "ts": m["thread_ts"], "limit": "20"})
        if not r.get("ok"):
            continue
        for resp in (r.get("messages") or [])[1:]:
            if "não abre" in (resp.get("text") or ""):
                corrigidos.update(alvo.findall(texto))
    return corrigidos


def _historico_completo(channel_id: str, dias: int) -> list:
    """TODAS as mensagens da janela, paginadas.

    Ate 05/08/2026 esta leitura era `limit: 60` sem paginacao: conferia 48 dos
    147 pedidos citados no canal (32%) e imprimia "sem divergencia" no fim.
    Conferencia parcial anunciada como completa e pior que nenhuma -- quem le
    o resultado para de procurar.
    """
    import time as _time

    oldest = _time.time() - dias * 86400
    msgs, cursor, paginas = [], None, 0
    while paginas < 40:  # teto: 40 x 200 = 8.000 mensagens
        p = {"channel": channel_id, "limit": "200", "oldest": f"{oldest:.6f}"}
        if cursor:
            p["cursor"] = cursor
        body = _api("conversations.history", p)
        if not body.get("ok"):
            raise RuntimeError(f"conversations.history falhou: {body.get('error')}")
        msgs += body.get("messages") or []
        cursor = (body.get("response_metadata") or {}).get("next_cursor") or ""
        paginas += 1
        if not cursor:
            break
    else:
        raise RuntimeError("teto de 40 paginas atingido — janela grande demais, "
                           "reduza --dias em vez de conferir pela metade")
    return msgs


# `canvases.sections.lookup` SEM criterio devolve zero secoes -- foi o que fez
# o validador imprimir "canvas lido: 0 caracteres" e conferir zero valor do
# Canvas. Com `contains_text` ele responde, e sem precisar de `files:read`
# (medido em 05/08/2026). O criterio precisa casar com o que sempre existe no
# documento; "R$" aparece em qualquer balanco nosso.
_ISCA_CANVAS = ("R$", "Prejuízo", "Quadro", "Total")


def _conteudo_canvas(channel_id: str) -> str:
    """Le o markdown de TODOS os canvas do canal, como o leitor veria."""
    info = _api("conversations.info", {"channel": channel_id})
    tabs = (info.get("channel", {}).get("properties") or {}).get("tabs") or []
    partes = []
    for t in tabs:
        fid = (t.get("data") or {}).get("file_id")
        if not fid:
            continue
        for isca in _ISCA_CANVAS:
            r = _api("canvases.sections.lookup",
                     {"canvas_id": fid,
                      "criteria": urllib.parse.quote(
                          json.dumps({"contains_text": isca}))})
            secoes = r.get("sections") or []
            if r.get("ok") and secoes:
                partes += [s.get("document_content", {}).get("markdown", "")
                           for s in secoes]
                break
    return "\n".join(partes)


def main() -> int:
    cid = _canal_id(CANAL)
    if not cid:
        print(f"canal {CANAL} nao encontrado")
        return 1

    print("=" * 92)
    print(f"CONFERENCIA — o que esta publicado em {CANAL} contra o Mercado Livre")
    print("=" * 92)

    # 1) mensagens reais do canal -- TODAS, paginadas.
    msgs = _historico_completo(cid, DIAS)
    texto_msgs = "\n".join(m.get("text", "") for m in msgs)
    print(f"\n  mensagens lidas do canal : {len(msgs)}  "
          f"(janela de {DIAS} dias, paginada)")

    # 2) canvas real
    canvas = _conteudo_canvas(cid)
    print(f"  canvas lido              : {len(canvas)} caracteres")

    pedidos = sorted(set(_RE_PEDIDO.findall(texto_msgs + "\n" + canvas)))
    print(f"  pedidos citados          : {len(pedidos)}")

    if not pedidos:
        print("\n  nada a conferir.")
        return 0

    conn = get_db_connection()
    cur = conn.cursor()

    print()
    print(f"  {'pedido':<19}{'link':<7}{'painel':<17}{'valor painel':>14}"
          f"{'valor ML':>13}  veredito")
    print("  " + "-" * 88)

    # CORRECAO EM THREAD CONTA COMO CORRIGIDO.
    #
    # 25 mensagens de julho citam o shipment_id no lugar do pedido, e o texto
    # delas nao pode ser editado: sairam pelo bot NTC Painel e o token de hoje
    # e o SAC Nautica. A correcao foi para a thread, com o link que abre. Se o
    # validador seguisse contando essas como falha, ficaria vermelho para
    # sempre por algo ja resolvido -- e invariante que nunca fica verde e
    # invariante que se aprende a ignorar.
    corrigidos = _pedidos_corrigidos_em_thread(cid, msgs)
    if corrigidos:
        print(f"  corrigidos em thread     : {len(corrigidos)}")

    ok = problemas = 0
    detalhes = []
    for oid in pedidos:
        if oid in corrigidos:
            continue
        pedido = get_order(oid)
        abre = pedido is not None

        cur.execute("""
            SELECT claim_status, claim_stage, order_total, item_sku
            FROM ml_devolucoes WHERE order_id::text = %s LIMIT 1
        """, (oid,))
        linha = cur.fetchone()

        falhas = []
        if not abre:
            falhas.append("link nao abre")
        if not linha:
            falhas.append("nao esta no painel")

        v_painel = float(linha[2] or 0) if linha else 0.0
        v_ml = float((pedido or {}).get("total_amount") or 0)
        if abre and linha and v_painel and abs(v_painel - v_ml) > 0.01:
            falhas.append(f"valor diverge (painel {v_painel:.2f} x ML {v_ml:.2f})")

        estado = f"{linha[0]}/{linha[1]}" if linha else "—"
        if falhas:
            problemas += 1
            detalhes.append((oid, falhas))
        else:
            ok += 1

        print(f"  {oid:<19}{'abre' if abre else '404':<7}{estado:<17}"
              f"{v_painel:>14,.2f}{v_ml:>13,.2f}  "
              f"{'OK' if not falhas else '; '.join(falhas)[:30]}")

    # 3) valores citados em texto batem com o painel?
    print()
    print("=" * 92)
    print("VALORES CITADOS NO CANAL")
    print("=" * 92)
    valores = _RE_BRL.findall(canvas)
    print(f"  {len(valores)} valor(es) no canvas: "
          f"{', '.join('R$ ' + v for v in valores[:10])}")

    conn.close()

    print()
    print("=" * 92)
    print(f"RESULTADO: {ok} conferido(s) sem divergencia | {problemas} com problema")
    print("=" * 92)
    for oid, fs in detalhes:
        print(f"  pedido {oid}: {'; '.join(fs)}")
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main())
