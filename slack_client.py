"""Cliente Slack Web API -- chat.postMessage com suporte a thread (Bot Token).
================================================================================
Slack_notify.py usava so Incoming Webhook (SLACK_WEBHOOK_URL): simples, mas
nunca devolve o `ts` da mensagem enviada -- sem isso nao ha como responder
"dentro" dela depois. `chat.postMessage` devolve `ts` no corpo da resposta,
permitindo agrupar por THREAD: a primeira notificacao de uma venda vira a
mensagem-raiz; atualizacoes de estado da MESMA venda (ver chave_estado em
slack_notify.py) respondem na MESMA thread, em vez de virarem mensagens
soltas novas no canal.

Retry/backoff: 429 (rate limit) espera o Retry-After que o proprio Slack
devolve; 5xx e erro de rede fazem backoff exponencial curto (0.5s, 1s,
2s...). 4xx (exceto 429) e falha logica (ok:false -- canal errado, thread
apagada, token invalido) NAO sao re-tentadas -- re-tentar nao resolve.

SECURITY: token lido, em ordem de prioridade, de SLACK_BOT_TOKEN (variavel
de ambiente -- Secret no GitHub Actions) ou de um ARQUIVO LOCAL, fora do
repo, nunca commitado: C:\\Users\\Pichau\\slack_bot_token.txt (uma linha:
xoxb-...). Token NUNCA aparece em exceptions, logs ou valores de retorno.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

TOKEN_FILE = Path(r"C:\Users\Pichau\slack_bot_token.txt")
_API = "https://slack.com/api/chat.postMessage"


def _token() -> Optional[str]:
    env_tok = (os.environ.get("SLACK_BOT_TOKEN") or "").strip()
    if env_tok.startswith("xoxb-"):
        return env_tok
    try:
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        return tok if tok.startswith("xoxb-") else None
    except Exception:
        return None


def post_message_full(
    channel: str,
    text: str,
    thread_ts: Optional[str] = None,
    *,
    blocks: Optional[list] = None,
    max_retries: int = 3,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Optional[dict]:
    """Envia mensagem (ou resposta em thread, se thread_ts for informado).
    Retorna o dicionario de resposta da API (incluindo 'ts' e 'channel' resolvido).
    """
    tok = _token()
    if not tok:
        return None
    payload: dict = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    if thread_ts:
        payload["thread_ts"] = thread_ts
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {tok}",
    }
    data = json.dumps(payload).encode("utf-8")

    tentativa = 0
    backoff = 0.5
    while True:
        req = urllib.request.Request(_API, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and tentativa < max_retries:
                espera = 1.0
                try:
                    espera = float(exc.headers.get("Retry-After", 1)) if exc.headers else 1.0
                except Exception:
                    espera = 1.0
                sleep_fn(espera)
                tentativa += 1
                continue
            if 500 <= exc.code < 600 and tentativa < max_retries:
                sleep_fn(backoff)
                backoff *= 2
                tentativa += 1
                continue
            return None
        except Exception:
            if tentativa < max_retries:
                sleep_fn(backoff)
                backoff *= 2
                tentativa += 1
                continue
            return None

        # chat.postMessage responde HTTP 200 mesmo em falha logica -- "ok"
        # no corpo e quem manda (ex.: {"ok": false, "error": "channel_not_found"}).
        if not body.get("ok"):
            return None
        return body

def post_message(
    channel: str,
    text: str,
    thread_ts: Optional[str] = None,
    *,
    blocks: Optional[list] = None,
    max_retries: int = 3,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Optional[str]:
    """Versao legada para retrocompatibilidade que devolve apenas o ts."""
    body = post_message_full(channel, text, thread_ts, blocks=blocks, max_retries=max_retries, sleep_fn=sleep_fn)
    return body.get("ts") if body else None


def _api(metodo: str, payload: Optional[dict] = None, *, get: bool = False) -> Optional[dict]:
    """Chamada generica a Web API. Devolve o corpo em sucesso, None em falha
    (logando o `error` do Slack em stderr -- falha silenciosa aqui ja custou
    4 dias de canal mudo). Nunca levanta."""
    tok = _token()
    if not tok:
        return None
    headers = {"Authorization": f"Bearer {tok}"}
    if get:
        qs = urllib.parse.urlencode(payload or {})
        req = urllib.request.Request(f"https://slack.com/api/{metodo}?{qs}", headers=headers)
    else:
        headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(f"https://slack.com/api/{metodo}",
                                     data=json.dumps(payload or {}).encode("utf-8"),
                                     headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
    except Exception as exc:
        print(f"[slack {metodo}] erro de rede: {type(exc).__name__}", file=sys.stderr)
        return None
    if not body.get("ok"):
        print(f"[slack {metodo}] error={body.get('error')}", file=sys.stderr)
        return None
    return body


def listar_canais() -> Optional[dict]:
    """{nome: id} dos canais publicos. None se faltar permissao -- distinguir
    "nao existe" de "nao consigo ver" evita criar canal duplicado."""
    canais, cursor = {}, ""
    while True:
        params = {"limit": 200, "exclude_archived": "true", "types": "public_channel"}
        if cursor:
            params["cursor"] = cursor
        body = _api("conversations.list", params, get=True)
        if body is None:
            return None
        for c in body.get("channels", []):
            canais[c["name"]] = c["id"]
        cursor = (body.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            return canais


def criar_canal(nome: str) -> Optional[str]:
    body = _api("conversations.create", {"name": nome, "is_private": False})
    return body.get("channel", {}).get("id") if body else None


def entrar_no_canal(channel_id: str) -> bool:
    return _api("conversations.join", {"channel": channel_id}) is not None


def garantir_canal(nome: str) -> Optional[str]:
    """Devolve o id do canal, criando-o se preciso, e garante o bot dentro.

    Sem isto o fechamento diario dependia de alguem lembrar de criar o canal
    e rodar /invite -- e a falha aparecia so como "nao enviou"."""
    nome = nome.lstrip("#")
    canais = listar_canais()
    if canais is None:
        return None
    # listar_canais devolve a LISTA crua da API (cada item tem name/id) --
    # antes havia uma versao que devolvia {nome: id} e este ponto ficou para
    # tras, quebrando com "list object has no attribute get" na primeira
    # execucao real do fechamento.
    por_nome = {c.get("name"): c.get("id") for c in canais}
    cid = por_nome.get(nome) or criar_canal(nome)
    if not cid:
        return None
    entrar_no_canal(cid)
    return cid


_API_CANVAS_CREATE = "https://slack.com/api/conversations.canvases.create"
_API_CANVAS_EDIT = "https://slack.com/api/canvases.edit"


def _post_json(url: str, payload: dict, *, max_retries: int = 3,
               sleep_fn: Callable[[float], None] = time.sleep) -> Optional[dict]:
    """POST autenticado com o mesmo retry/backoff do post_message.

    Extraido para as chamadas de Canvas nao repetirem a politica de retry --
    e para uma correcao nela valer para todas.
    """
    tok = _token()
    if not tok:
        return None
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {tok}",
    }
    data = json.dumps(payload).encode("utf-8")
    tentativa = 0
    backoff = 0.5
    while True:
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and tentativa < max_retries:
                try:
                    espera = float(exc.headers.get("Retry-After", 1)) if exc.headers else 1.0
                except Exception:
                    espera = 1.0
                sleep_fn(espera)
                tentativa += 1
                continue
            if 500 <= exc.code < 600 and tentativa < max_retries:
                sleep_fn(backoff)
                backoff *= 2
                tentativa += 1
                continue
            return None
        except Exception:
            if tentativa < max_retries:
                sleep_fn(backoff)
                backoff *= 2
                tentativa += 1
                continue
            return None
        if not body.get("ok"):
            import sys
            print(f"[{url.rsplit('/', 1)[-1]} FALHOU] error={body.get('error')}",
                  file=sys.stderr)
            return None
        return body


_API_CONV_LIST = "https://slack.com/api/conversations.list"


def listar_canais(limit: int = 200) -> Optional[list]:
    """Canais publicos do workspace. Necessario para converter '#sac' no ID
    que a API de Canvas exige."""
    tok = _token()
    if not tok:
        return None
    url = f"{_API_CONV_LIST}?types=public_channel&limit={limit}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read())
    except Exception:
        return None
    if not body.get("ok"):
        import sys
        print(f"[conversations.list FALHOU] error={body.get('error')}",
              file=sys.stderr)
        return None
    return body.get("channels") or []


def canvas_criar(channel_id: str, markdown: str,
                 titulo: str = "Quadro do SAC", **kw) -> Optional[str]:
    """Cria um Canvas no canal e devolve o canvas_id.

    O `titulo` NAO e cosmetico: e o rotulo da aba no topo do canal. Sem ele o
    Slack mostra "Sem titulo", e com mais de um canvas a Maria nao sabe em
    qual clicar -- foi o que aconteceu no #sac-teste, que acumulou tres abas
    "Sem titulo", uma por execucao.

    Quem chama guarda o id: cada chamada cria um canvas NOVO, nao reaproveita
    o existente."""
    r = _post_json(_API_CANVAS_CREATE, {
        "channel_id": channel_id,
        "title": titulo,
        "document_content": {"type": "markdown", "markdown": markdown},
    }, **kw)
    return r.get("canvas_id") if r else None


_API_SET_PURPOSE = "https://slack.com/api/conversations.setPurpose"
_API_SET_TOPIC = "https://slack.com/api/conversations.setTopic"


def definir_proposito(channel_id: str, texto: str, **kw) -> bool:
    """Proposito do canal -- o que quem entra le antes de qualquer mensagem.

    Canal sem proposito obriga a pessoa nova a deduzir para que ele serve
    lendo o historico. Aqui a explicacao fica no lugar onde ela procura."""
    return bool(_post_json(_API_SET_PURPOSE,
                           {"channel": channel_id, "purpose": texto}, **kw))


def definir_topico(channel_id: str, texto: str, **kw) -> bool:
    """Topico do canal -- a linha curta que aparece no cabecalho."""
    return bool(_post_json(_API_SET_TOPIC,
                           {"channel": channel_id, "topic": texto}, **kw))


def canvas_editar(canvas_id: str, markdown: str, **kw) -> bool:
    """Substitui o conteudo inteiro do Canvas. `replace` e proposital: o
    quadro e reconstruido do estado atual do banco a cada ciclo, entao
    remendar secao a secao so criaria divergencia entre o que esta na tela e
    o que esta no banco."""
    r = _post_json(_API_CANVAS_EDIT, {
        "canvas_id": canvas_id,
        "changes": [{
            "operation": "replace",
            "document_content": {"type": "markdown", "markdown": markdown},
        }],
    }, **kw)
    return bool(r)


_API_UPDATE = "https://slack.com/api/chat.update"


def update_message(
    channel: str,
    ts: str,
    text: str,
    *,
    blocks: Optional[list] = None,
    max_retries: int = 3,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Optional[str]:
    """Atualiza (chat.update) uma mensagem existente IN-PLACE. Retorna o `ts`
    em sucesso, None em falha (nunca lanca). Usado pelo Quadro Kanban do SAC,
    que se ATUALIZA a cada ciclo em vez de postar um quadro novo -- senao
    poluiria o canal com dezenas de quadros por dia. Mesmo retry/backoff do
    post_message."""
    tok = _token()
    if not tok:
        return None
    payload = {"channel": channel, "ts": ts, "text": text}
    if blocks:
        payload["blocks"] = blocks
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {tok}",
    }
    data = json.dumps(payload).encode("utf-8")
    tentativa = 0
    backoff = 0.5
    while True:
        req = urllib.request.Request(_API_UPDATE, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and tentativa < max_retries:
                try:
                    espera = float(exc.headers.get("Retry-After", 1)) if exc.headers else 1.0
                except Exception:
                    espera = 1.0
                sleep_fn(espera)
                tentativa += 1
                continue
            if 500 <= exc.code < 600 and tentativa < max_retries:
                sleep_fn(backoff)
                backoff *= 2
                tentativa += 1
                continue
            return None
        except Exception:
            if tentativa < max_retries:
                sleep_fn(backoff)
                backoff *= 2
                tentativa += 1
                continue
            return None
        if not body.get("ok"):
            error_msg = body.get("error", "unknown")
            import sys
            print(f"[chat.update FALHOU] channel={channel}, ts={ts}, error={error_msg}, body={body}", file=sys.stderr)
            return None
        return body.get("ts")
