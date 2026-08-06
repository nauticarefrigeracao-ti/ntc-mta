"""Socket Mode -- o processo que faz o botao do card virar acao.

Sem isto no ar, botao e decoracao: a Maria clica, nada acontece, e ela volta
para o Meli. Pior do que nao ter botao.

Como funciona, medido na documentacao oficial e na conta:

    POST /api/apps.connections.open   (Bearer xapp-...)  -> url wss://
    <- {"envelope_id": "...", "type": "interactive", "payload": {...}}
    -> {"envelope_id": "..."}          # o ack, em ate 3 segundos

**O ack vem primeiro, antes de gravar qualquer coisa.** Se ele so sair depois
do banco e do redesenho do card, o Slack considera o clique perdido e reenvia
-- e a mesma marcacao entra duas vezes na linha do tempo da Thayna.

**O clique chega sem contexto.** So `action_id` e `value` carregam o que
pusermos neles. O `claim_id` vai no `value`; sem ele o listener nao sabe qual
caso avancar, e adivinhar esta fora de questao.

SECURITY: o token de app (xapp-) e lido de SLACK_APP_TOKEN (env / Secret do
GitHub) ou de C:\\Users\\Pichau\\slack_app_token.txt -- arquivo local, fora do
repo, nunca commitado. Nunca aparece em log, excecao ou retorno.

Uso:
    python socket_sac.py            # fica no ar, escutando
    python socket_sac.py --checar   # so confirma que o token conecta
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

sys.path.insert(0, str(Path(__file__).parent))

import card_maria
import sac_fluxo
import slack_client

APP_TOKEN_FILE = Path(r"C:\Users\Pichau\slack_app_token.txt")
_ABRIR = "https://slack.com/api/apps.connections.open"

# Canal para onde vai o caso encaminhado. O botao de supervisor do desenho da
# Thayna so tem serventia se alguem do outro lado for avisado.
CANAL_SUPERVISOR = "#sac-supervisao"

PREFIXO = "sac_"


def _app_token() -> Optional[str]:
    tok = (os.environ.get("SLACK_APP_TOKEN") or "").strip()
    if tok.startswith("xapp-"):
        return tok
    try:
        t = APP_TOKEN_FILE.read_text(encoding="utf-8").strip().splitlines()[0]
        return t.strip() if t.strip().startswith("xapp-") else None
    except OSError:
        return None


def resposta_de_ack(envelope_id: str) -> dict:
    """O que devolvemos ao Slack para ele nao reenviar o mesmo clique."""
    return {"envelope_id": envelope_id}


def nome_de(usuario: Optional[Mapping[str, Any]]) -> str:
    """Nome de gente, nao ID.

    "U0BH1234" na linha do tempo nao responde "quem marcou isso". A Thayna
    pediu quem -- e quem e "Maria".
    """
    u = usuario or {}
    perfil = u.get("profile") or {}
    for chave in (perfil.get("display_name"), perfil.get("real_name"),
                  u.get("username"), u.get("name"), u.get("id")):
        if chave:
            return str(chave)
    return "alguém"


def interpretar(envelope: Optional[Mapping[str, Any]]) -> Optional[dict]:
    """O clique, achatado no que o resto precisa. None = nao e para nos.

    Outros apps e outros botoes vivem no mesmo canal: reagir a tudo seria
    avancar caso por clique alheio. Dai o prefixo obrigatorio.
    """
    env = envelope or {}
    payload = env.get("payload") or {}
    if payload.get("type") != "block_actions":
        return None

    acoes = payload.get("actions") or []
    if not acoes:
        return None
    acao = acoes[0] or {}

    action_id = str(acao.get("action_id") or "")
    if not action_id.startswith(PREFIXO):
        return None

    try:
        claim_id = int(str(acao.get("value") or "").strip())
    except (TypeError, ValueError):
        # value vazio ou lixo viraria marcacao em caso que nao existe.
        return None

    return {
        "envelope_id": env.get("envelope_id"),
        "acao": action_id[len(PREFIXO):],
        "claim_id": claim_id,
        "quem": nome_de(payload.get("user")),
        # O ID serve para avisar SO quem clicou quando o clique e recusado.
        # Sem ele, a mensagem saia com "<@>" -- uma mencao a ninguem.
        "user_id": (payload.get("user") or {}).get("id"),
        "channel": (payload.get("channel") or {}).get("id"),
        "ts": (payload.get("message") or {}).get("ts"),
        "trigger_id": payload.get("trigger_id"),
    }


def modal_de_observacao(claim_id: int, channel: str, ts: str) -> dict:
    """A janela de escrever a observacao.

    O `view_submission` volta num envelope separado, sem o card por perto --
    por isso claim/channel/ts viajam no `private_metadata`. Sem eles a
    observacao nao sabe onde pousar.
    """
    return {
        "type": "modal",
        "callback_id": "sac_observacao",
        "private_metadata": json.dumps(
            {"claim_id": claim_id, "channel": channel, "ts": ts}),
        # O Slack recusa title acima de 24 caracteres -- e recusa calado: o
        # modal simplesmente nao abre.
        "title": {"type": "plain_text", "text": "Observação"},
        "submit": {"type": "plain_text", "text": "Salvar"},
        "close": {"type": "plain_text", "text": "Cancelar"},
        "blocks": [{
            "type": "input",
            "block_id": "obs",
            "label": {"type": "plain_text", "text": "O que aconteceu?"},
            "element": {
                "type": "plain_text_input",
                "action_id": "texto",
                "multiline": True,
                "placeholder": {"type": "plain_text",
                                "text": "Ex.: cliente parou de responder"},
            },
        }],
    }


def deve_atualizar_cofrinho(acao: str) -> bool:
    """So o desfecho mexe no placar.

    Redesenhar o cofrinho a cada "recebi" seria uma chamada ao Slack por
    clique sem nada mudar no numero -- e o rate limit de app nao-Marketplace
    e de 1 requisicao por minuto por metodo.
    """
    import cofrinho

    return acao in cofrinho.DESFECHOS


def interpretar_modal(envelope: Optional[Mapping[str, Any]]) -> Optional[dict]:
    """A observacao enviada pelo modal."""
    env = envelope or {}
    payload = env.get("payload") or {}
    if payload.get("type") != "view_submission":
        return None
    view = payload.get("view") or {}
    if view.get("callback_id") != "sac_observacao":
        return None
    try:
        meta = json.loads(view.get("private_metadata") or "{}")
        claim_id = int(meta["claim_id"])
    except (ValueError, KeyError, TypeError):
        return None
    texto = (((view.get("state") or {}).get("values") or {})
             .get("obs", {}).get("texto", {}).get("value"))
    return {
        "envelope_id": env.get("envelope_id"),
        "acao": "observacao",
        "claim_id": claim_id,
        "quem": nome_de(payload.get("user")),
        "channel": meta.get("channel"),
        "ts": meta.get("ts"),
        "observacao": (texto or "").strip() or None,
    }


# --- I/O -------------------------------------------------------------------

def abrir_conexao() -> str:
    """Pede ao Slack a URL wss. Levanta alto -- sem ela nao ha o que escutar."""
    tok = _app_token()
    if not tok:
        raise RuntimeError(
            "token de app ausente. Defina SLACK_APP_TOKEN ou grave o "
            f"xapp-... em {APP_TOKEN_FILE} (uma linha, sem aspas)."
        )
    req = urllib.request.Request(
        _ABRIR, data=b"", headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.loads(r.read())
    if not body.get("ok"):
        raise RuntimeError(
            f"apps.connections.open falhou: {body.get('error')}. "
            "Confira se o escopo connections:write está no token de app."
        )
    return body["url"]


def deve_reconectar(envelope: Optional[Mapping[str, Any]]) -> bool:
    """O Slack pediu para trocar de conexao?

    Socket Mode nao e conexao eterna: o Slack manda `disconnect` (em geral
    com `reason: refresh_requested`) a cada ~1h e fecha o socket. E o
    rebalanceamento normal dele, nao um erro -- mas a primeira versao tratava
    como fim de expediente e o processo saia com SUCESSO. O listener morreria
    sozinho depois de uma hora, verde no painel, e a Maria veria "problemas
    de conexao" ao clicar.
    """
    return (envelope or {}).get("type") == "disconnect"


def eh_encerramento_limpo(prazo_vencido: bool) -> bool:
    """Socket fechado no fim do prazo do job e saida planejada.

    No meio do expediente, e queda -- e queda se reconecta.
    """
    return bool(prazo_vencido)


def avisar_quem_clicou(evento: Mapping[str, Any], texto: str) -> bool:
    """Mensagem efemera: so quem clicou ve, e ela some sozinha.

    Sem `user_id` nao da para mandar efemera. Ai o recado vai para a thread
    do card -- feio, mas silencio seria pior: a pessoa clicaria de novo sem
    entender por que nada aconteceu.
    """
    uid = evento.get("user_id")
    if uid:
        r = slack_client._api("chat.postEphemeral", {
            "channel": evento["channel"], "user": uid, "text": texto})
        if r and r.get("ok"):
            return True
    slack_client.post_message_full(evento["channel"], texto,
                                   thread_ts=evento.get("ts"))
    return False


def _timeline(conn, claim_id: int, canal: str) -> list:
    """So as marcacoes deste canal -- treino no #sac-teste nao mexe no #sac."""
    return card_maria.buscar_timelines(
        conn, [claim_id], canal).get(claim_id, [])


def _redesenhar(conn, claim_id: int, channel: str, ts: str) -> bool:
    cur = conn.cursor()
    cur.execute(card_maria.SQL_CASOS.replace(
        "AND d.return_destino = 'loja'",
        "AND d.claim_id = %s"), (claim_id,))
    linhas = card_maria._linhas(cur)
    if not linhas:
        return False
    caso = linhas[0]
    oficial = slack_client.garantir_canal(card_maria.CANAL)
    blocos = card_maria.blocos_do_card(
        caso, _timeline(conn, claim_id, channel), datetime.now().date(),
        ensaio=channel != oficial)
    resumo = (f"Devolução #{card_maria.numero_na_plataforma(caso)} — "
              f"{(caso.get('item_title') or '')[:60]}")
    return bool(slack_client.update_message(channel, ts, resumo, blocks=blocos))


def aplicar_clique(evento: Mapping[str, Any]) -> str:
    """Valida, grava e redesenha. Devolve uma linha de log legivel."""
    from src.db.connection import get_db_connection

    claim_id = evento["claim_id"]
    acao = evento["acao"]
    conn = get_db_connection()

    estado = sac_fluxo.estado_de(_timeline(conn, claim_id, evento["channel"]))
    try:
        novo = sac_fluxo.aplicar(estado, acao)
    except ValueError as e:
        # Clique fora de ordem: duplo clique, ou card velho aberto em outra
        # aba desde de manha. Avisa QUEM CLICOU -- e so ele.
        #
        # A primeira versao respondia na thread do card. Duas consequencias
        # ruins, as duas vistas no QA: a thread encheu de sete avisos que a
        # Maria teria que rolar, e um deslize de dedo virava recado publico.
        # Efemera resolve as duas: aparece so para quem clicou e some sozinha.
        avisar_quem_clicou(evento, f"⚠️ {e}")
        return f"claim {claim_id}: recusado ({e})"

    card_maria.registrar(conn, claim_id, acao, evento.get("quem"),
                         channel_id=evento["channel"],
                         observacao=evento.get("observacao"))

    if acao == "supervisor":
        aviso = (f"🆙 *{evento.get('quem')}* encaminhou o caso `{claim_id}` "
                 f"({sac_fluxo.rotulo_do_estado(estado)}).")
        if evento["channel"] == slack_client.garantir_canal(card_maria.CANAL):
            cid = slack_client.garantir_canal(CANAL_SUPERVISOR)
            if cid:
                slack_client.post_message_full(cid, aviso)
        else:
            # Treino nao acorda supervisor. O aviso fica na thread do proprio
            # card, para a Maria ver que o botao respondeu -- sem ninguem do
            # outro lado receber chamado de mentira.
            slack_client.post_message_full(
                evento["channel"], f"🧪 _(ensaio)_ {aviso}",
                thread_ts=evento["ts"])

    ok = _redesenhar(conn, claim_id, evento["channel"], evento["ts"])

    if deve_atualizar_cofrinho(acao):
        # O placar tem que mexer no instante do clique. Se so atualizasse no
        # job da noite, a Maria fecharia um caso e nao veria nada acontecer --
        # e um cofrinho que nao reage nao e um cofrinho.
        import cofrinho
        # No canal onde o card vive -- nao no #sac fixo. Um ensaio no
        # #sac-teste republicaria o placar na cara de quem opera.
        cofrinho.publicar(channel_id=evento["channel"])

    return (f"claim {claim_id}: {acao} → {novo} por {evento.get('quem')}"
            + ("" if ok else "  [card não redesenhou]"))


async def _uma_conexao(vistos: set, ate: Optional[float]) -> str:
    """Uma sessao de Socket Mode. Devolve por que ela terminou."""
    from websockets.asyncio.client import connect

    url = abrir_conexao()
    print(f"conectado — escutando cliques em {card_maria.CANAL}", flush=True)

    async with connect(url, open_timeout=20) as ws:
        async for cru in ws:
            try:
                env = json.loads(cru)
            except ValueError:
                continue

            if deve_reconectar(env):
                return f"o Slack pediu troca de conexão ({env.get('reason')})"
            if env.get("type") == "hello":
                continue

            # ACK PRIMEIRO. Depois o trabalho. Invertido, o Slack considera o
            # clique perdido e reenvia -- e a marcacao entra duas vezes.
            eid = env.get("envelope_id")
            if eid:
                await ws.send(json.dumps(resposta_de_ack(eid)))

            # Reentrega, ou dois listeners em sobreposicao (que e como se
            # consegue cobertura continua no Actions): o mesmo envelope pode
            # chegar de novo. Anotar duas vezes duplicaria a observacao na
            # linha do tempo, porque anotar e sempre acao valida.
            if eid and eid in vistos:
                continue
            if eid:
                vistos.add(eid)

            evento = interpretar(env) or interpretar_modal(env)
            if not evento:
                continue

            if evento["acao"] == "observacao" and evento.get("trigger_id"):
                slack_client._api("views.open", {
                    "trigger_id": evento["trigger_id"],
                    "view": modal_de_observacao(
                        evento["claim_id"], evento["channel"], evento["ts"]),
                })
                continue

            try:
                print(aplicar_clique(evento), flush=True)
            except Exception as e:
                # Falhar alto no log, mas sem derrubar o listener: um caso
                # quebrado nao pode deixar a Maria sem botao nos outros oito.
                print(f"ERRO no clique {evento}: {e!r}", file=sys.stderr,
                      flush=True)

            if ate and time.monotonic() >= ate:
                return "prazo do job cumprido"
    return "o socket fechou"


async def escutar(duracao_min: Optional[int] = None) -> int:
    """Fica no ar, reconectando, ate o prazo acabar.

    `duracao_min` existe para o GitHub Actions: o job tem teto de 6h, e sair
    sozinho antes disso e mais limpo do que ser morto no meio de um clique.
    Sem prazo, roda ate alguem parar.
    """
    ate = time.monotonic() + duracao_min * 60 if duracao_min else None
    vistos: set = set()
    espera = 1.0

    while True:
        # O `async for` fica bloqueado esperando clique, entao a checagem de
        # prazo la dentro so acontecia QUANDO alguem clicava. Num dia parado
        # o job ia ate o Actions matar -- o oposto de sair planejado. O
        # relogio tem que correr por fora, independente de haver trafego.
        restante = (ate - time.monotonic()) if ate else None
        if restante is not None and restante <= 0:
            print("encerrando: prazo do job cumprido", flush=True)
            return 0
        try:
            motivo = await asyncio.wait_for(
                _uma_conexao(vistos, ate), timeout=restante)
            espera = 1.0
        except (asyncio.TimeoutError, TimeoutError):
            print("encerrando: prazo do job cumprido", flush=True)
            return 0
        except Exception as e:
            motivo = f"caiu: {e!r}"

        vencido = bool(ate and time.monotonic() >= ate)
        if vencido:
            print(f"encerrando: {motivo}", flush=True)
            return 0
        if eh_encerramento_limpo(vencido):
            return 0

        print(f"reconectando em {espera:.0f}s — {motivo}",
              file=sys.stderr, flush=True)
        await asyncio.sleep(espera)
        # Teto baixo de propósito: minuto sem listener é clique perdido na
        # cara da Maria, não linha a mais no log.
        espera = min(espera * 2, 30.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checar", action="store_true",
                    help="só confirma que o token de app conecta")
    ap.add_argument("--minutos", type=int,
                    help="sai sozinho depois de N minutos (o job do Actions "
                         "tem teto de 6h; sair antes é mais limpo que ser "
                         "morto no meio de um clique)")
    args = ap.parse_args()

    if args.checar:
        url = abrir_conexao()
        print("token de app OK — o Slack devolveu uma URL wss")
        return 0 if url.startswith("wss://") else 1

    card_maria.garantir_tabelas()
    return asyncio.run(escutar(args.minutos))


if __name__ == "__main__":
    sys.exit(main())
