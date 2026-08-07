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
import listener_saude
import sac_fluxo
import slack_client

APP_TOKEN_FILE = Path(r"C:\Users\Pichau\slack_app_token.txt")
_ABRIR = "https://slack.com/api/apps.connections.open"

# Canal para onde vai o caso encaminhado. O botao de supervisor do desenho da
# Thayna so tem serventia se alguem do outro lado for avisado.
CANAL_SUPERVISOR = "#sac-supervisao"

PREFIXO = "sac_"

# Conexoes simultaneas ao Socket Mode. A documentacao oficial permite ate 10
# e recomenda explicitamente mais de uma:
#
#   "you can use multiple connections for temporary active-active redundancy"
#   "generate an additional connection BEFORE the restart occurs"
#
# O Slack refresca a conexao "once every few hours". Com uma so, cada refresh
# e um buraco entre fechar e reabrir -- e a doc NAO diz o que acontece com um
# clique quando ha zero conexoes abertas. E por ser indocumentado que a gente
# nao deixa o buraco existir.
#
# Duas cobrem o refresh; dez seriam socket e chamada a mais sem cobertura
# extra, porque cada envelope vai para UMA conexao so.
CONEXOES = 2


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
    clique sem nada mudar no numero. Barato, mas inutil -- e ruido no canal.

    CORRECAO: eu tinha escrito aqui que o limite era "1 requisicao por
    minuto por metodo". Errado. Aquele limite vale so para
    `conversations.history` e `conversations.replies`, e so para apps
    distribuidos fora do Marketplace. Apps internos como o nosso mantem os
    limites antigos, e `chat.update` e Tier 3 (50+/min).
    https://docs.slack.dev/changelog/2025/06/03/rate-limits-clarity/
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


# De quanto em quanto tempo o listener vai buscar novidade no Mercado Livre.
#
# Medido em 07/08/2026: sem ninguem rodando a coleta, o card mostrava
# "a caminho" enquanto o Meli ja dizia "entregue" ha 2h40. Nao era defeito de
# dado -- era atraso, porque a coleta so acontecia quando o dev a executava na
# mao. Dez minutos e o que transforma "informacao de horas atras" em
# "informacao de agora" sem martelar a API: ~144 ciclos por dia contra o
# limite de 50+ requisicoes por minuto.
COLETA_MIN = 10


def deve_sair_por_versao(meu_sha: Optional[str], sha_do_main: Optional[str],
                         tem_run_mais_novo: bool) -> bool:
    """Este listener ficou velho e ja ha quem o substitua?

    Medido em 07/08/2026: QUATRO runs no ar com TRES versoes de codigo. O
    Slack distribui cada clique entre as conexoes abertas, entao metade caia
    no codigo antigo -- e o botao do WhatsApp aparecia ou nao, conforme a
    sorte. Dois avisos seguidos no canal chegaram a se contradizer.

    Sai SO quando ha substituto no ar. Codigo velho servindo e ruim; ninguem
    servindo e pior, e um run que sai sozinho sem sucessor abre buraco ate o
    proximo cron -- que a medicao do vigia mostra chegar em ~100 minutos.

    Sem SHA (rodando na maquina ou no VPS), nao sai: la o deploy reinicia o
    processo e este problema nao existe.
    """
    if not meu_sha or not sha_do_main:
        return False
    return meu_sha != sha_do_main and tem_run_mais_novo


def _estado_do_repo() -> tuple[Optional[str], bool]:
    """(sha do main, existe run mais novo deste workflow rodando)."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    meu_run = os.environ.get("GITHUB_RUN_ID")
    if not repo:
        return None, False

    def _api(caminho: str):
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/{caminho}",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "ntc-sac-listener"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    sha = (_api("commits/main") or {}).get("sha")
    corridas = (_api("actions/workflows/sac_listener.yml/runs"
                     "?status=in_progress&per_page=20") or {})
    mais_novo = any(
        str(w.get("id")) != str(meu_run)
        and str(w.get("id")) > str(meu_run or "")
        for w in corridas.get("workflow_runs", []))
    return sha, mais_novo


async def _vigiar_versao() -> None:
    """Encerra o processo quando ele ficou obsoleto e ha substituto."""
    meu = os.environ.get("GITHUB_SHA")
    if not meu:
        return
    while True:
        await asyncio.sleep(5 * 60)
        try:
            sha, mais_novo = _estado_do_repo()
            if deve_sair_por_versao(meu, sha, mais_novo):
                print(f"encerrando: código mudou ({meu[:7]} -> {sha[:7]}) e "
                      f"já há run mais novo no ar", flush=True)
                # SIGTERM em si mesmo: o `finally` de escutar() cancela as
                # tarefas e fecha os sockets, em vez de morrer no meio.
                raise SystemExit(0)
        except SystemExit:
            raise
        except Exception as e:
            print(f"[versão] não consegui conferir: {e!r}", file=sys.stderr,
                  flush=True)


async def _coletar_do_meli() -> None:
    """Busca previsao, destino e estado no ML enquanto o listener vive.

    Mora AQUI, e nao num cron, porque este e o unico processo que ja fica
    24/7 de pe e ja tem o token. Um agendamento separado seria mais uma coisa
    para cair calada -- e foi exatamente assim que a tabela `orders` ficou 13
    dias congelada em julho.
    """
    while True:
        try:
            import em_transito

            c = em_transito.coletar()
            print(f"[coleta] {c['com_previsao']} de {c['casos']} casos com "
                  f"previsão · {c.get('loja', 0)} loja / {c.get('full', 0)} full",
                  flush=True)
        except Exception as e:
            # Coleta e alimentacao, nao o coracao: se ela falhar, os botoes
            # continuam funcionando. Mas falha FALANDO -- coleta que morre
            # calada e o card envelhecendo sem ninguem perceber.
            print(f"[coleta] falhou: {e!r}", file=sys.stderr, flush=True)
        await asyncio.sleep(COLETA_MIN * 60)


async def _uma_conexao(vistos: set, ate: Optional[float],
                       n: int = 0) -> str:
    """Uma sessao de Socket Mode. Devolve por que ela terminou."""
    from websockets.asyncio.client import connect

    url = abrir_conexao()
    print(f"[conexão {n}] conectada — escutando {card_maria.CANAL}",
          flush=True)

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


async def _pulsar(instancia: str) -> None:
    """Bate um pulso enquanto este processo viver.

    Periodico de proposito, em vez de "avisar quando cair": processo que leva
    SIGKILL nao avisa nada -- ele so para de bater, e o silencio e o sinal.

    Vai para ARQUIVO, nao para o Neon. O pulso de 30s segurava conexao aberta
    24/7 e impedia o autosuspend: 182 CU-hours contra 100 do plano Free.
    A instrumentacao derrubaria o banco que ela existe para vigiar.
    """
    caminho = listener_saude.ARQUIVO_PADRAO
    while True:
        try:
            listener_saude.bater_arquivo(caminho, instancia)
        except Exception as e:
            # O pulso e instrumentacao: se ele cair, o listener continua
            # trabalhando. Mas cai FALANDO -- medicao que some calada faz o
            # relatorio mentir para o lado bom.
            print(f"[pulso] falhou: {e!r}", file=sys.stderr, flush=True)
        await asyncio.sleep(listener_saude.INTERVALO_S)


async def escutar(duracao_min: Optional[int] = None) -> int:
    """Fica no ar, reconectando, ate o prazo acabar.

    `duracao_min` existe para o GitHub Actions: o job tem teto de 6h, e sair
    sozinho antes disso e mais limpo do que ser morto no meio de um clique.
    Sem prazo, roda ate alguem parar.
    """
    ate = time.monotonic() + duracao_min * 60 if duracao_min else None
    vistos: set = set()
    pulso = asyncio.create_task(
        _pulsar(listener_saude.nome_da_instancia()))
    coleta = asyncio.create_task(_coletar_do_meli())
    versao = asyncio.create_task(_vigiar_versao())

    # Redundancia ativa-ativa, como a doc do Slack recomenda: enquanto uma
    # conexao e refrescada, a outra continua recebendo. `vistos` e
    # compartilhado -- se o mesmo envelope chegar nas duas, ele so vira
    # marcacao uma vez.
    conexoes = [asyncio.create_task(_manter_conexao(n, vistos, ate))
                for n in range(CONEXOES)]
    try:
        restante = (ate - time.monotonic()) if ate else None
        # O vigia de versao entra no wait: se ele levantar
        # SystemExit, o processo sai limpo em vez de seguir
        # atendendo com codigo obsoleto.
        pronto, _ = await asyncio.wait(
            [*conexoes, versao], timeout=restante,
            return_when=asyncio.FIRST_COMPLETED)
        for t in pronto:
            if t is versao and not t.cancelled():
                t.result()   # propaga o SystemExit
        print("encerrando: prazo do job cumprido", flush=True)
        return 0
    finally:
        pulso.cancel()
        coleta.cancel()
        versao.cancel()
        for c in conexoes:
            c.cancel()


async def _manter_conexao(n: int, vistos: set, ate: Optional[float]) -> None:
    """Uma das conexoes, viva enquanto o job durar.

    Cada uma se reconecta por conta propria. Quem decide o fim do expediente
    e `escutar`, por fora -- o `async for` fica bloqueado esperando clique, e
    conferir o prazo aqui dentro so funcionaria quando alguem clicasse.
    """
    espera = 1.0
    while True:
        if ate and time.monotonic() >= ate:
            return
        try:
            motivo = await _uma_conexao(vistos, ate, n)
            espera = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as e:
            motivo = f"caiu: {e!r}"

        if ate and time.monotonic() >= ate:
            return

        print(f"[conexão {n}] reconectando em {espera:.0f}s — {motivo}",
              file=sys.stderr, flush=True)
        await asyncio.sleep(espera)
        # Teto baixo de propósito: com a outra conexão no ar isto não é
        # urgente, mas se as duas caírem juntas, minuto parado é clique
        # perdido na cara da Maria — não linha a mais no log.
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
