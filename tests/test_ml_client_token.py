"""Testes -- resolucao da fonte do token no ml_client.

Regressao real: st.secrets.get() LANCA ("No secrets found") quando nao existe
secrets.toml. Como a leitura era `st.secrets.get(...) or os.environ.get(...)`,
a excecao acontecia ANTES do fallback e a variavel de ambiente nunca era lida.
No GitHub Actions nao ha secrets.toml -- so env var -- entao o token ficava
vazio e todo o sync respondia 401/403.
"""
import sys
import types
from unittest.mock import patch

import pytest

import src.api.ml_client as ml_client


@pytest.fixture(autouse=True)
def _limpa_cache():
    ml_client._TOKEN_CACHE = {"value": "", "expires": 0.0}
    yield
    ml_client._TOKEN_CACHE = {"value": "", "expires": 0.0}


class _SecretsQueLancam:
    """Reproduz o streamlit sem secrets.toml: qualquer acesso levanta."""

    def get(self, *_a, **_k):
        raise RuntimeError("No secrets found. Valid paths for a secrets.toml file...")


def _streamlit_sem_secrets():
    mod = types.ModuleType("streamlit")
    mod.secrets = _SecretsQueLancam()
    return mod


def test_le_neon_url_do_env_quando_streamlit_nao_tem_secrets(monkeypatch):
    """Sem secrets.toml (= GitHub Actions), ML_NEON_URL do ambiente tem que
    ser usada. Antes do fix o token voltava vazio -> 401 em tudo."""
    monkeypatch.setenv("ML_NEON_URL", "postgresql://fake/db")
    monkeypatch.delenv("ML_ACCESS_TOKEN", raising=False)
    monkeypatch.setitem(sys.modules, "streamlit", _streamlit_sem_secrets())

    with patch.object(ml_client, "_fetch_token_from_neon", return_value="TOKEN-DO-NEON") as fetch:
        assert ml_client._token() == "TOKEN-DO-NEON"
    fetch.assert_called_once_with("postgresql://fake/db")


def test_sem_nenhuma_fonte_devolve_vazio_sem_levantar(monkeypatch):
    monkeypatch.delenv("ML_NEON_URL", raising=False)
    monkeypatch.delenv("ML_ACCESS_TOKEN", raising=False)
    monkeypatch.setitem(sys.modules, "streamlit", _streamlit_sem_secrets())
    assert ml_client._token() == ""


def test_env_ml_access_token_serve_de_ultimo_fallback(monkeypatch):
    monkeypatch.delenv("ML_NEON_URL", raising=False)
    monkeypatch.setenv("ML_ACCESS_TOKEN", "TOKEN-ESTATICO")
    monkeypatch.setitem(sys.modules, "streamlit", _streamlit_sem_secrets())
    assert ml_client._token() == "TOKEN-ESTATICO"


def test_token_do_neon_tem_prioridade_sobre_o_estatico(monkeypatch):
    monkeypatch.setenv("ML_NEON_URL", "postgresql://fake/db")
    monkeypatch.setenv("ML_ACCESS_TOKEN", "TOKEN-ESTATICO")
    monkeypatch.setitem(sys.modules, "streamlit", _streamlit_sem_secrets())
    with patch.object(ml_client, "_fetch_token_from_neon", return_value="TOKEN-DO-NEON"):
        assert ml_client._token() == "TOKEN-DO-NEON"


def test_cai_no_estatico_quando_neon_nao_devolve_token(monkeypatch):
    monkeypatch.setenv("ML_NEON_URL", "postgresql://fake/db")
    monkeypatch.setenv("ML_ACCESS_TOKEN", "TOKEN-ESTATICO")
    monkeypatch.setitem(sys.modules, "streamlit", _streamlit_sem_secrets())
    with patch.object(ml_client, "_fetch_token_from_neon", return_value=""):
        assert ml_client._token() == "TOKEN-ESTATICO"


def test_token_e_cacheado_entre_chamadas(monkeypatch):
    monkeypatch.setenv("ML_NEON_URL", "postgresql://fake/db")
    monkeypatch.setitem(sys.modules, "streamlit", _streamlit_sem_secrets())
    with patch.object(ml_client, "_fetch_token_from_neon", return_value="TOKEN-DO-NEON") as fetch:
        ml_client._token()
        ml_client._token()
    fetch.assert_called_once()
