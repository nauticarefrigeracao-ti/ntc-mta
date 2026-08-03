"""Auditoria dos canais do SAC: duplicidade, lacuna e divergência.

O chefe abre o Slack e confere — clica nos links, soma os números, compara com
o Mercado Livre. Então a conferência tem que existir antes dele.

Já aconteceu de tudo aqui: o fechamento publicado duas vezes no
#sac-fechamento; uma aba de Canvas vazia no #sac que quase levou o Canvas
certo junto na limpeza; o mesmo caso contado duas vezes inflando o prejuízo em
45%. Nenhum desses apareceu como erro em job nenhum — todos passaram como
"rodou sem exceção".

Três perguntas, nas MENSAGENS e nos CANVAS de cada canal:

    DUPLICIDADE   o mesmo caso publicado duas vezes?
    LACUNA        um caso que devia estar publicado e não está?
    DIVERGENCIA   o que está publicado bate com o estado atual no banco?

E uma quarta, implícita: ler zero e reportar "sem divergência" NÃO é
aprovação. Auditoria de canal vazio reprova.

Uso:
    python auditoria_slack.py                    # todos os canais do SAC
    python auditoria_slack.py "#sac"
    python auditoria_slack.py --dias 30
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Optional

sys.path.insert(0, str(Path(__file__).parent))

# Formatos de order_id MEDIDOS na API do ML em 30/07/2026:
#   10 dígitos -> pedido antigo legítimo (5.099 casos, 6/6 abrem em /orders/)
#   11 dígitos -> SHIPMENT (8/8 em /shipments/) — não é pedido
#   16 dígitos -> pedido novo (2000…)
# A regex aceita 10 e 16 e recusa 11 de propósito: um shipment publicado como
# se fosse pedido gera link que não abre — e link quebrado na cara do chefe
# custa mais que o dado faltando.
_RE_ID = re.compile(r"(?<![\d.,])(\d{16}|\d{10})(?![\d.,])")

CANAIS_DO_SAC = ("#sac", "#sac-fechamento")

# Canais cuja FUNÇÃO é publicar caso a caso. Só deles faz sentido cobrar
# lacuna. A primeira execução real (03/08/2026) acusou 72 lacunas no
# #sac-fechamento — nenhuma verdadeira: aquele canal é o placar do chefe,
# números agregados, nunca pedido individual. Cobrar publicação por caso de um
# canal que nunca prometeu isso repete a acusação injusta que o primeiro
# validador fez contra 36 de 40 casos corretos.
CANAIS_POR_CASO = ("#sac", "#sac-teste")


def cobra_lacuna(canal: str) -> bool:
    return (canal or "").strip().lower() in CANAIS_POR_CASO


def _regra_de_publicacao():
    """A MESMA regra que o notificador usa (D3). Auditoria com regra própria
    audita a própria opinião, não o sistema."""
    from slack_notify import deve_notificar_no_canal
    return deve_notificar_no_canal


devido_no_canal = _regra_de_publicacao()


def ids_no_texto(texto: str) -> set:
    """order_ids citados num texto (mensagem ou Canvas)."""
    return {int(m) for m in _RE_ID.findall(texto or "")}


def duplicidades(ids: Iterable[int]) -> list:
    """[(id, vezes), ...] do mais repetido para o menos. Vazio se limpo."""
    c = Counter(ids)
    return sorted(((i, n) for i, n in c.items() if n > 1),
                  key=lambda t: (-t[1], t[0]))


def lacunas(devidos: set, publicados: set) -> list:
    """O que o banco cobra e o canal não mostra.

    Sobra não é lacuna: um caso publicado que o banco não cobra mais pode ser
    histórico legítimo que saiu do recorte de dias.
    """
    return sorted(set(devidos) - set(publicados))


def divergencias_de_estado(publicado: Mapping[int, str],
                           banco: Mapping[int, str]) -> list:
    """[(id, estado_publicado, estado_no_banco), ...] onde os dois discordam.

    O quadro mostrando "A Fazer" o que o ML já encerrou é o defeito mais caro:
    a Maria trabalha o que não existe mais.
    """
    fora = []
    for ident, estado in publicado.items():
        atual = banco.get(ident)
        if atual is not None and atual != estado:
            fora.append((ident, estado, atual))
    return sorted(fora)


_RE_SECAO = re.compile(r"^##\s+.*$", re.M)
_MARCA_A_FAZER = "A Fazer"


def estado_publicado_no_canvas(markdown: str) -> dict:
    """{order_id: coluna} conforme o Canvas MOSTRA hoje.

    O Quadro só lista caso a caso na coluna "A Fazer" — "Aguardando" e "Feito"
    são contadores (decisão de 31/07: listar os 37 restantes recriaria o
    afogamento que o quadro veio resolver). Então o único estado publicado e
    verificável é `a_fazer`, e é esse que precisa bater com o banco.

    Sem isto a auditoria reportaria "0 divergências" sem ter conferido nada —
    o mesmo silêncio que já deixou um validador dar OK numa tela de login.
    """
    if not markdown:
        return {}
    secoes = list(_RE_SECAO.finditer(markdown))
    for i, m in enumerate(secoes):
        if _MARCA_A_FAZER.lower() not in m.group(0).lower():
            continue
        fim = secoes[i + 1].start() if i + 1 < len(secoes) else len(markdown)
        bloco = markdown[m.end():fim]
        return {oid: "a_fazer" for oid in ids_no_texto(bloco)}
    return {}


def resumir_auditoria(canal: str, duplicados: list, faltando: list,
                      divergentes: list, lidos: int) -> dict:
    """Veredito de um canal. `ok` só é True com evidência de leitura."""
    if not lidos:
        return {"ok": False,
                "texto": f"{canal}: nada foi lido — sem evidência, não há "
                         f"aprovação (canal vazio? bot sem acesso?)"}
    problemas = []
    if duplicados:
        problemas.append(f"{len(duplicados)} duplicado(s): " +
                         ", ".join(f"{i} ({n}x)" for i, n in duplicados[:5]))
    if faltando:
        problemas.append(f"{len(faltando)} lacuna(s): " +
                         ", ".join(str(i) for i in faltando[:5]))
    if divergentes:
        problemas.append(f"{len(divergentes)} divergência(s) de estado: " +
                         ", ".join(f"{i} publicado {p}, banco {b}"
                                   for i, p, b in divergentes[:3]))
    if not problemas:
        return {"ok": True,
                "texto": f"{canal}: {lidos} item(ns) conferido(s), "
                         f"sem duplicidade nem lacuna"}
    return {"ok": False,
            "texto": f"{canal}: {lidos} item(ns) lido(s) — " + " | ".join(problemas)}


# --- leitura real ----------------------------------------------------------

def historico_do_canal(cid: str, dias: int) -> Optional[list]:
    """Textos de TODAS as mensagens do período — paginado.

    `conversations.history` devolve no máximo as N mais recentes. A primeira
    execução leu 200 e acusou 3 lacunas no #sac; com 141 avisos em 7 dias mais
    quadro e canvas, 200 mensagens não cobrem a janela — as "lacunas" eram
    mensagens que existiam fora do recorte lido. Auditoria que inventa achado
    é pior que auditoria nenhuma: queima a confiança de quem vai conferir.

    Devolve None quando não deu para ler (≠ canal vazio).
    """
    import slack_client
    import time as _time

    oldest = _time.time() - dias * 86400
    textos, cursor, paginas = [], None, 0
    while paginas < 25:  # teto de segurança: 25 x 200 = 5.000 mensagens
        payload = {"channel": cid, "limit": "200", "oldest": f"{oldest:.6f}"}
        if cursor:
            payload["cursor"] = cursor
        body = slack_client._api("conversations.history", payload, get=True)
        if body is None:
            return None if not textos else textos
        textos += [m.get("text") or "" for m in (body.get("messages") or [])]
        cursor = ((body.get("response_metadata") or {}).get("next_cursor") or "")
        paginas += 1
        if not cursor:
            break
    return textos

def _canvas_do_canal(cid: str) -> list:
    """Todos os Canvas do canal, com o markdown de cada um.

    Lê o canal-nativo (`properties.canvas`) e as abas em `files`. Um Canvas
    órfão e vazio já quase levou o certo junto numa limpeza — por isso a
    auditoria olha TODOS, não só o que achamos que criamos.
    """
    import slack_client

    achados = []
    info = slack_client._api("conversations.info",
                             {"channel": cid, "include_num_members": "false"},
                             get=True) or {}
    canal = info.get("channel") or {}
    nativo = ((canal.get("properties") or {}).get("canvas") or {}).get("file_id")
    ids = [nativo] if nativo else []

    lista = slack_client._api("files.list",
                              {"channel": cid, "types": "canvas", "limit": "50"},
                              get=True) or {}
    for f in (lista.get("files") or []):
        if f.get("id") and f["id"] not in ids:
            ids.append(f["id"])

    for fid in ids:
        det = slack_client._api("files.info", {"file": fid}, get=True) or {}
        arq = det.get("file") or {}
        md = ""
        for chave in ("canvas_markdown", "plain_text", "preview"):
            if arq.get(chave):
                md = arq[chave]
                break
        achados.append({"id": fid, "titulo": arq.get("title") or "(sem título)",
                        "markdown": md})
    return achados


def _coluna_no_banco(ids: set) -> dict:
    """{order_id: coluna do Kanban} segundo o estado ATUAL no banco.

    Usa a mesma `classificar_kanban` que monta o Canvas — comparar com outra
    regra compararia duas opiniões, não o quadro com a verdade.
    """
    if not ids:
        return {}
    from slack_notify import classificar_kanban
    from src.db.connection import dict_cursor, get_db_connection

    conn = get_db_connection()
    try:
        with dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM ml_devolucoes WHERE order_id = ANY(%s)",
                        (list(ids),))
            linhas = cur.fetchall()
    finally:
        conn.close()
    return {r["order_id"]: classificar_kanban(r) for r in linhas}


def _devidos_no_periodo(dias: int) -> set:
    """order_ids que a regra D3 manda PUBLICAR no canal, no período.

    Cuidado que custou uma acusação falsa: `slack_notificados` registra CASO
    PROCESSADO (é a chave de deduplicação), não MENSAGEM PUBLICADA. Pela D3,
    disputa aberta fica no Canvas e nunca vira mensagem. A primeira versão
    tratava toda linha da tabela como "devia estar no canal" e acusou 3 casos
    corretos (2000017357006052, 2000017582555852, 2000017686941586).

    Por isso o filtro final é `devido_no_canal` — a MESMA função que o
    notificador usa para decidir. Auditoria com regra própria audita a
    própria opinião.
    """
    from src.db.connection import dict_cursor, get_db_connection

    conn = get_db_connection()
    try:
        with dict_cursor(conn) as cur:
            cur.execute("""
                SELECT DISTINCT ON (d.order_id) d.*
                FROM ml_devolucoes d
                JOIN slack_notificados n ON n.claim_id::text = d.claim_id::text
                WHERE n.avisado_em > NOW() - (%s || ' days')::interval
                  AND LENGTH(d.order_id::text) IN (10, 16)
            """, (str(dias),))
            return {r["order_id"] for r in cur.fetchall() if devido_no_canal(r)}
    finally:
        conn.close()


def auditar_canal(canal: str, dias: int = 7) -> dict:
    import slack_client

    cid = slack_client.garantir_canal(canal)
    if not cid:
        return {"ok": False, "texto": f"{canal}: não resolvi o canal"}

    # ultimas_mensagens devolve TEXTOS (não dicts) e devolve None quando não
    # conseguiu ler. None não pode virar "canal vazio": ler zero e aprovar é
    # exatamente como um validador já deu OK numa tela de login.
    textos = historico_do_canal(cid, dias=dias)
    if textos is None:
        return {"ok": False,
                "texto": f"{canal}: não consegui LER o histórico "
                         f"(bot sem channels:history?) — sem leitura não há "
                         f"aprovação"}
    ids_msgs = [i for t in textos for i in ids_no_texto(t)]

    canvas = _canvas_do_canal(cid)
    ids_canvas = []
    for c in canvas:
        ids_canvas += list(ids_no_texto(c["markdown"]))

    publicados = set(ids_msgs) | set(ids_canvas)
    devidos = _devidos_no_periodo(dias)

    # O estado que o Canvas MOSTRA, contra o estado que o banco tem hoje.
    publicado_por_canvas = {}
    for c in canvas:
        publicado_por_canvas.update(estado_publicado_no_canvas(c["markdown"]))
    div = divergencias_de_estado(publicado_por_canvas,
                                 _coluna_no_banco(set(publicado_por_canvas)))

    # No CANAL, repetir é esperado (um caso muda de estágio e é reavisado);
    # o que não pode é o mesmo caso repetido DENTRO de um Canvas, que é
    # fotografia de estado, não histórico.
    dup_canvas = duplicidades(ids_canvas)
    falt = lacunas(devidos, publicados) if cobra_lacuna(canal) else []

    r = resumir_auditoria(canal, dup_canvas, falt, div,
                          lidos=len(textos) + len(canvas))
    r.update({"mensagens": len(textos), "canvas": canvas,
              "ids_mensagens": len(set(ids_msgs)),
              "ids_canvas": len(set(ids_canvas)),
              "duplicados_canvas": dup_canvas, "lacunas": falt,
              "divergencias": div,
              "duplicados_mensagens": duplicidades(ids_msgs)})
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("canais", nargs="*", default=list(CANAIS_DO_SAC))
    ap.add_argument("--dias", type=int, default=7)
    args = ap.parse_args()

    tudo_ok = True
    for canal in (args.canais or CANAIS_DO_SAC):
        r = auditar_canal(canal, dias=args.dias)
        print("=" * 92)
        print(f"CANAL {canal}")
        print("=" * 92)
        print(f"  mensagens lidas    : {r.get('mensagens', 0)}")
        print(f"  canvas encontrados : {len(r.get('canvas') or [])}")
        for c in (r.get("canvas") or []):
            n = len(ids_no_texto(c["markdown"]))
            print(f"     - {c['titulo']!r}  ({len(c['markdown'])} chars, "
                  f"{n} pedido(s))")
        print(f"  pedidos nas mensagens : {r.get('ids_mensagens', 0)}")
        print(f"  pedidos nos canvas    : {r.get('ids_canvas', 0)}")
        print()
        for rot, chave in (("duplicados no CANVAS", "duplicados_canvas"),
                           ("repetidos nas MENSAGENS (esperado: mudança de estágio)",
                            "duplicados_mensagens"),
                           ("lacunas", "lacunas"),
                           ("divergencias Canvas x banco", "divergencias")):
            v = r.get(chave) or []
            print(f"  {rot}: {len(v)}")
            for item in v[:8]:
                print(f"     {item}")
        print()
        print(f"  >> {r['texto']}")
        print()
        if not r.get("ok"):
            tudo_ok = False

    return 0 if tudo_ok else 1


if __name__ == "__main__":
    sys.exit(main())

