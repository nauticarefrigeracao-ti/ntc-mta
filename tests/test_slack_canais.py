"""Testes -- garantir_canal: achar/criar/entrar num canal. Toda rede mockada."""
from unittest.mock import patch

import slack_client


def test_canal_existente_e_reaproveitado():
    """Nao cria de novo -- so devolve o id e garante que o bot esta dentro."""
    with patch.object(slack_client, "listar_canais",
                      return_value={"sac": "C111", "sac-fechamento": "C222"}):
        with patch.object(slack_client, "criar_canal") as criar:
            with patch.object(slack_client, "entrar_no_canal", return_value=True) as entrar:
                assert slack_client.garantir_canal("#sac-fechamento") == "C222"
    criar.assert_not_called()
    entrar.assert_called_once_with("C222")


def test_canal_inexistente_e_criado_e_entra():
    with patch.object(slack_client, "listar_canais", return_value={"sac": "C111"}):
        with patch.object(slack_client, "criar_canal", return_value="C333") as criar:
            with patch.object(slack_client, "entrar_no_canal", return_value=True) as entrar:
                assert slack_client.garantir_canal("#sac-fechamento") == "C333"
    criar.assert_called_once_with("sac-fechamento")
    entrar.assert_called_once_with("C333")


def test_prefixo_cerquilha_e_opcional():
    with patch.object(slack_client, "listar_canais", return_value={"sac-fechamento": "C222"}):
        with patch.object(slack_client, "entrar_no_canal", return_value=True):
            assert slack_client.garantir_canal("sac-fechamento") == "C222"


def test_falha_ao_criar_devolve_none_sem_levantar():
    with patch.object(slack_client, "listar_canais", return_value={}):
        with patch.object(slack_client, "criar_canal", return_value=None):
            assert slack_client.garantir_canal("#novo") is None


def test_sem_permissao_para_listar_devolve_none():
    # listar_canais devolve None (missing_scope) -> nao inventa que nao existe
    # e sai criando canal duplicado
    with patch.object(slack_client, "listar_canais", return_value=None):
        with patch.object(slack_client, "criar_canal") as criar:
            assert slack_client.garantir_canal("#sac-fechamento") is None
    criar.assert_not_called()
