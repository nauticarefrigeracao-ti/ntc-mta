"""Token do Slack morto = Maria sem aviso, e ninguém sabendo.

Em 05/08/2026 o app SAC Náutica quase foi reinstalado com dezenas de escopos
extras (vários `admin.*`, que exigem Enterprise Grid). O Slack recusou o
pacote inteiro — "Permissões inválidas solicitadas" — e por isso nada mudou:
o token seguiu com os mesmos 12 escopos e o sistema não parou.

Mas o susto expôs um buraco: **reinstalar o app rotaciona o bot token**. Se
alguém clicar em Install e o `SLACK_BOT_TOKEN` do GitHub não for atualizado no
mesmo minuto, o notificador para. O job fica vermelho, sim — mas ninguém olha
o painel do Actions de hora em hora, e a Maria fica sem aviso enquanto isso.

E há o caso mais traiçoeiro: uma reinstalação que RETIRE um escopo. O token
continua válido, `chat:write` segue funcionando, mas `canvases:write` some — e
o Quadro simplesmente para de atualizar sem nenhum erro visível. Token vivo
não é o mesmo que token suficiente.

Por isso a bateria confere as duas coisas: o token responde, e responde com
tudo que o sistema usa.
"""
import pytest

from confianca import ESCOPOS_NECESSARIOS, checar_token_slack


TODOS = list(ESCOPOS_NECESSARIOS)


def test_token_completo_nao_gera_achado():
    assert checar_token_slack(TODOS) is None


def test_escopo_a_mais_nao_incomoda():
    assert checar_token_slack(TODOS + ["files:read", "pins:write"]) is None


def test_token_morto_gera_quebra():
    """None = auth.test não respondeu. É o pior caso, não 'tudo bem'."""
    a = checar_token_slack(None)
    assert a is not None and a.severidade == "quebra"


def test_token_morto_diz_o_que_fazer():
    a = checar_token_slack(None)
    assert "SLACK_BOT_TOKEN" in a.acao


def test_escopo_faltando_gera_quebra():
    faltando = [e for e in TODOS if e != "canvases:write"]
    a = checar_token_slack(faltando)
    assert a is not None and a.severidade == "quebra"
    assert "canvases:write" in a.evidencia


def test_o_achado_lista_todos_os_que_faltam():
    a = checar_token_slack(["chat:write"])
    for e in TODOS:
        if e != "chat:write":
            assert e in a.evidencia


def test_o_caso_traicoeiro_token_vivo_e_insuficiente():
    """Reinstalação que retira `canvases:write`: o token continua válido,
    chat:write funciona, e o Quadro para de atualizar sem erro nenhum."""
    a = checar_token_slack([e for e in TODOS if e != "canvases:write"])
    texto = (a.resumo + " " + a.evidencia).lower()
    assert "escopo" in texto or "permiss" in texto


def test_lista_vazia_nao_e_confundida_com_token_morto():
    """[] = respondeu sem escopo nenhum (app quebrado). None = não respondeu.
    Confundir os dois manda procurar o problema no lugar errado."""
    a_vazio = checar_token_slack([])
    a_morto = checar_token_slack(None)
    assert a_vazio.resumo != a_morto.resumo


# --- a lista de escopos é derivada do que o código USA ---------------------

def test_cobre_o_que_o_notificador_precisa():
    for e in ("chat:write", "channels:history", "channels:read"):
        assert e in ESCOPOS_NECESSARIOS


def test_cobre_o_canvas():
    """O Quadro da Maria e o balanço do chefe são Canvas."""
    assert "canvases:write" in ESCOPOS_NECESSARIOS
    assert "canvases:read" in ESCOPOS_NECESSARIOS


def test_nao_cobra_escopo_que_nao_usamos():
    """Cobrar `files:read` faria a bateria ficar vermelha para sempre por uma
    permissão que medimos em 05/08 e concluímos ser dispensável."""
    assert "files:read" not in ESCOPOS_NECESSARIOS


def test_entra_na_bateria_geral():
    import inspect

    import confianca
    assert inspect.getsource(confianca).count("checar_token_slack") >= 2
