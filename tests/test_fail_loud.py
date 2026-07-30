"""FAIL-LOUD: uma falha de envio nao pode terminar como run verde.

Incidente real: o bot saiu de #sac (not_in_channel), chat.postMessage passou a
devolver ok:false, o notificador contou 0 enviadas, imprimiu "0 processo(s)
notificado(s)" e saiu com codigo 0. O GitHub Actions marcou verde por 4 dias
enquanto a Maria nao recebia NADA. Sucesso silencioso e pior que erro.
"""
from slack_notify import status_saida


def test_nada_a_fazer_e_sucesso():
    # ciclo sem processo novo: nao ha o que enviar, run verde e correto
    assert status_saida(tentadas=0, enviadas=0) == 0


def test_tudo_enviado_e_sucesso():
    assert status_saida(tentadas=5, enviadas=5) == 0


def test_falha_total_no_envio_e_erro():
    # exatamente o not_in_channel: tinha o que enviar e nada saiu
    assert status_saida(tentadas=5, enviadas=0) == 1


def test_falha_parcial_tambem_e_erro():
    # 2 de 5 sumiram -- a Maria perdeu 2 casos; nao pode passar batido
    assert status_saida(tentadas=5, enviadas=3) == 1


def test_um_unico_processo_que_falha_e_erro():
    assert status_saida(tentadas=1, enviadas=0) == 1


def test_enviadas_nunca_maior_que_tentadas_ainda_e_sucesso():
    # defensivo: contagem inconsistente nao deve derrubar o run
    assert status_saida(tentadas=2, enviadas=3) == 0
