"""Testes -- slack_client.py. Toda rede e mockada (urlopen fake)."""
import email.message
import io
import json
import os
import urllib.error
from unittest.mock import patch, MagicMock

import slack_client


def _fake_response(body: dict):
    cm = MagicMock()
    cm.__enter__.return_value = io.BytesIO(json.dumps(body).encode("utf-8"))
    cm.__exit__.return_value = False
    return cm


def _http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    m = email.message.Message()
    for k, v in (headers or {}).items():
        m[k] = v
    return urllib.error.HTTPError(
        url="https://slack.com/api/chat.postMessage", code=code, msg="erro", hdrs=m,
        fp=io.BytesIO(b""),
    )


# --- token -------------------------------------------------------------

def test_token_sem_arquivo_e_sem_env_retorna_none():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SLACK_BOT_TOKEN", None)
        with patch.object(slack_client, "TOKEN_FILE") as mock_path:
            mock_path.read_text.side_effect = FileNotFoundError
            assert slack_client._token() is None


def test_token_valido_do_arquivo_e_lido():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SLACK_BOT_TOKEN", None)
        with patch.object(slack_client, "TOKEN_FILE") as mock_path:
            mock_path.read_text.return_value = "xoxb-123-456\n"
            assert slack_client._token() == "xoxb-123-456"


def test_env_var_tem_prioridade_sobre_arquivo():
    with patch.object(slack_client, "TOKEN_FILE") as mock_path:
        mock_path.read_text.return_value = "xoxb-do-arquivo\n"
        with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-da-env"}):
            assert slack_client._token() == "xoxb-da-env"


# --- post_message --------------------------------------------------------

def test_sem_token_retorna_none_sem_chamar_rede():
    with patch.object(slack_client, "_token", return_value=None):
        with patch("urllib.request.urlopen") as mock_urlopen:
            assert slack_client.post_message("#sac", "oi") is None
            mock_urlopen.assert_not_called()


def test_sucesso_retorna_ts():
    with patch.object(slack_client, "_token", return_value="xoxb-x"):
        with patch("urllib.request.urlopen", return_value=_fake_response({"ok": True, "ts": "1234.5678"})):
            assert slack_client.post_message("#sac", "oi") == "1234.5678"


def test_falha_logica_ok_false_retorna_none():
    with patch.object(slack_client, "_token", return_value="xoxb-x"):
        with patch("urllib.request.urlopen", return_value=_fake_response(
            {"ok": False, "error": "channel_not_found"}
        )):
            assert slack_client.post_message("#canal-errado", "oi") is None


def test_thread_ts_e_enviado_no_payload():
    captured = {}

    def fake_urlopen(req, timeout=15):
        captured["body"] = json.loads(req.data)
        return _fake_response({"ok": True, "ts": "1"})

    with patch.object(slack_client, "_token", return_value="xoxb-x"):
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            slack_client.post_message("#sac", "oi", thread_ts="raiz-123")
    assert captured["body"]["thread_ts"] == "raiz-123"


def test_sem_thread_ts_nao_envia_campo():
    captured = {}

    def fake_urlopen(req, timeout=15):
        captured["body"] = json.loads(req.data)
        return _fake_response({"ok": True, "ts": "1"})

    with patch.object(slack_client, "_token", return_value="xoxb-x"):
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            slack_client.post_message("#sac", "oi")
    assert "thread_ts" not in captured["body"]


# --- retry / backoff ------------------------------------------------------

def test_429_espera_retry_after_e_tenta_de_novo():
    chamadas, esperas = [], []

    def fake_urlopen(req, timeout=15):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise _http_error(429, {"Retry-After": "2"})
        return _fake_response({"ok": True, "ts": "final-ts"})

    with patch.object(slack_client, "_token", return_value="xoxb-x"):
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            r = slack_client.post_message("#sac", "oi", sleep_fn=esperas.append)
    assert r == "final-ts"
    assert esperas == [2.0]


def test_5xx_faz_backoff_exponencial():
    chamadas, esperas = [], []

    def fake_urlopen(req, timeout=15):
        chamadas.append(1)
        if len(chamadas) < 3:
            raise _http_error(503)
        return _fake_response({"ok": True, "ts": "ok-ts"})

    with patch.object(slack_client, "_token", return_value="xoxb-x"):
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            r = slack_client.post_message("#sac", "oi", sleep_fn=esperas.append)
    assert r == "ok-ts"
    assert esperas == [0.5, 1.0]


def test_4xx_nao_retryable_retorna_none_sem_esperar():
    chamadas, esperas = [], []

    def fake_urlopen(req, timeout=15):
        chamadas.append(1)
        raise _http_error(400)

    with patch.object(slack_client, "_token", return_value="xoxb-x"):
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            r = slack_client.post_message("#sac", "oi", sleep_fn=esperas.append)
    assert r is None
    assert len(chamadas) == 1
    assert esperas == []


def test_falha_logica_nao_retenta():
    chamadas, esperas = [], []

    def fake_urlopen(req, timeout=15):
        chamadas.append(1)
        return _fake_response({"ok": False, "error": "channel_not_found"})

    with patch.object(slack_client, "_token", return_value="xoxb-x"):
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            slack_client.post_message("#sac", "oi", sleep_fn=esperas.append)
    assert len(chamadas) == 1
    assert esperas == []


def test_erro_de_rede_tambem_faz_retry_com_backoff():
    chamadas, esperas = [], []

    def fake_urlopen(req, timeout=15):
        chamadas.append(1)
        if len(chamadas) < 2:
            raise OSError("timeout")
        return _fake_response({"ok": True, "ts": "ts-ok"})

    with patch.object(slack_client, "_token", return_value="xoxb-x"):
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            r = slack_client.post_message("#sac", "oi", sleep_fn=esperas.append)
    assert r == "ts-ok"
    assert esperas == [0.5]


# --- update_message ------

def test_update_sucesso_retorna_ts():
    with patch.object(slack_client, "_token", return_value="xoxb-x"):
        with patch("urllib.request.urlopen", return_value=_fake_response({"ok": True, "ts": "1234.5678"})):
            assert slack_client.update_message("C123456", "1234.5678", "texto atualizado") == "1234.5678"


def test_update_channel_not_found_retorna_none():
    with patch.object(slack_client, "_token", return_value="xoxb-x"):
        with patch("urllib.request.urlopen", return_value=_fake_response(
            {"ok": False, "error": "channel_not_found"}
        )):
            assert slack_client.update_message("C999", "1234.5678", "texto") is None


def test_update_message_not_found_retorna_none():
    with patch.object(slack_client, "_token", return_value="xoxb-x"):
        with patch("urllib.request.urlopen", return_value=_fake_response(
            {"ok": False, "error": "message_not_found"}
        )):
            assert slack_client.update_message("C123", "999.999", "texto") is None


def test_update_payload_contem_ts_e_channel_corretos():
    captured = {}

    def fake_urlopen(req, timeout=15):
        captured["body"] = json.loads(req.data)
        return _fake_response({"ok": True, "ts": "1"})

    with patch.object(slack_client, "_token", return_value="xoxb-x"):
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            slack_client.update_message("C123456", "1234.5678", "novo texto")
    assert captured["body"]["channel"] == "C123456"
    assert captured["body"]["ts"] == "1234.5678"
    assert captured["body"]["text"] == "novo texto"


def test_update_429_retry_after():
    chamadas, esperas = [], []

    def fake_urlopen(req, timeout=15):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise _http_error(429, {"Retry-After": "1"})
        return _fake_response({"ok": True, "ts": "1234.5678"})

    with patch.object(slack_client, "_token", return_value="xoxb-x"):
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            r = slack_client.update_message("C123", "1234.5678", "texto", sleep_fn=esperas.append)
    assert r == "1234.5678"
    assert esperas == [1.0]
