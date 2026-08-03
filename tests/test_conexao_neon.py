"""A conexão com o Neon precisa sobreviver ao cold start.

Em 02/08/2026, às 03h49 (domingo), o run 30731254024 do notificador morreu
com `psycopg2.OperationalError: ... port 5432 failed: timeout expired`.
Não era credencial errada nem rede caída: o Neon suspende o compute por
inatividade e a primeira conexão depois disso espera a instância acordar.
Uma tentativa só não cobre essa espera — e a Maria ficou sem aviso naquele
ciclo.

O que estes testes travam:
  - erro de wake-up é re-tentado, e a segunda tentativa vale;
  - erro que NÃO passa sozinho (senha errada) falha na hora, sem gastar
    30s tentando de novo o que nunca vai funcionar;
  - depois de esgotar as tentativas, a exceção original sobe — o job tem
    que sair com código 1, não fingir sucesso.
"""
import psycopg2
import pytest

from src.db import connection


@pytest.fixture(autouse=True)
def _dsn_falso(monkeypatch):
    """Evita depender de secrets/env para testar a política de retry."""
    monkeypatch.setattr(connection, "_get_dsn", lambda: "postgresql://x/y")


def _sem_espera(monkeypatch):
    """O retry dorme 2s e 4s. No teste isso é só lentidão."""
    import time
    monkeypatch.setattr(time, "sleep", lambda _s: None)


def test_timeout_de_cold_start_e_retentado(monkeypatch):
    _sem_espera(monkeypatch)
    tentativas = []

    def falha_uma_vez(dsn, **kw):
        tentativas.append(dsn)
        if len(tentativas) == 1:
            raise psycopg2.OperationalError(
                'connection to server at "ep-plain-cherry.neon.tech", '
                "port 5432 failed: timeout expired")
        return "conexao"

    monkeypatch.setattr(psycopg2, "connect", falha_uma_vez)
    assert connection.get_db_connection() == "conexao"
    assert len(tentativas) == 2, "deveria ter tentado de novo"


def test_o_caso_real_de_domingo(monkeypatch):
    """Mensagem literal do run 30731254024."""
    _sem_espera(monkeypatch)
    n = {"i": 0}

    def acorda_na_terceira(dsn, **kw):
        n["i"] += 1
        if n["i"] < 3:
            raise psycopg2.OperationalError(
                "connection to server at "
                '"ep-plain-cherry-adgln486-pooler.c-2.us-east-1.aws.neon.tech" '
                "(3.209.1.1), port 5432 failed: timeout expired")
        return "conexao"

    monkeypatch.setattr(psycopg2, "connect", acorda_na_terceira)
    assert connection.get_db_connection() == "conexao"


def test_senha_errada_falha_na_primeira_sem_insistir(monkeypatch):
    """Retry em erro permanente é só atraso: o job demora mais para dizer
    a mesma coisa, e o operador demora mais para saber."""
    _sem_espera(monkeypatch)
    tentativas = []

    def sempre_falha(dsn, **kw):
        tentativas.append(dsn)
        raise psycopg2.OperationalError(
            'FATAL: password authentication failed for user "ntc"')

    monkeypatch.setattr(psycopg2, "connect", sempre_falha)
    with pytest.raises(psycopg2.OperationalError, match="password"):
        connection.get_db_connection()
    assert len(tentativas) == 1, "não devia insistir em senha errada"


def test_neon_fora_do_ar_ainda_estoura_com_a_mensagem_original(monkeypatch):
    """Retry não pode virar `except: pass`. Se o banco está fora mesmo,
    a falha tem que subir com o motivo — o job sai 1 e alguém trata."""
    _sem_espera(monkeypatch)
    tentativas = []

    def sempre_timeout(dsn, **kw):
        tentativas.append(dsn)
        raise psycopg2.OperationalError("timeout expired")

    monkeypatch.setattr(psycopg2, "connect", sempre_timeout)
    with pytest.raises(psycopg2.OperationalError, match="timeout expired"):
        connection.get_db_connection()
    assert len(tentativas) == 3, "três tentativas antes de desistir"


def test_conexao_boa_nao_paga_pedagio(monkeypatch):
    """O caminho feliz — a esmagadora maioria — tem que ser uma chamada só."""
    _sem_espera(monkeypatch)
    tentativas = []

    def ok(dsn, **kw):
        tentativas.append(dsn)
        return "conexao"

    monkeypatch.setattr(psycopg2, "connect", ok)
    assert connection.get_db_connection() == "conexao"
    assert len(tentativas) == 1
