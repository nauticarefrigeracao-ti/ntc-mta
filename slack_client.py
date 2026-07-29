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
import time
import urllib.request
import urllib.error
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
            return None
        return body.get("ts")
