"""Duas abas "Quadro do SAC" no mesmo canal, e ninguém sabia.

05/08/2026. Publiquei o fechamento de julho, conferi por API que os marcadores
estavam lá, e disse que estava validado. O Lucas abriu o Slack e viu o que a
API não mostra: **duas abas com o mesmo nome** no #sac, e no #sac-fechamento o
título do Canvas repetido dentro do próprio Canvas.

`canvases.sections.lookup` responde "o texto está lá" e fica satisfeito. A
pergunta que importa é outra: *o que a Maria e o chefe encontram quando abrem
o canal?* Duas abas iguais fazem a pessoa clicar na errada — e a errada é um
Canvas de 26/07 que ninguém atualiza desde então.

`conversations.info` devolve `properties.tabs`, e devolve **sem** `files:read`
— medido em 05/08. Ou seja: a checagem que faltava estava disponível o tempo
todo. O que faltou foi olhar a tela em vez de olhar o conteúdo.
"""
import pytest

from confianca import Achado, checar_abas_do_canal


def aba(file_id, label="Quadro do SAC"):
    return {"id": f"Ct{file_id}", "label": label, "type": "canvas",
            "data": {"file_id": file_id}}


OFICIAL = "F0BMJ5W0KU5"


def test_uma_aba_por_proposito_nao_gera_achado():
    assert checar_abas_do_canal("#sac", [aba(OFICIAL)], {OFICIAL}) is None


def test_abas_com_o_mesmo_rotulo_geram_achado():
    """O que o olho vê é o rótulo. Dois rótulos iguais = clique na errada."""
    a = checar_abas_do_canal("#sac", [aba(OFICIAL), aba("F0BLE0ZV6KV")],
                             {OFICIAL})
    assert a is not None and isinstance(a, Achado)


def test_aba_sem_rotulo_conta_como_duplicata_do_titulo():
    """Rótulo vazio faz o Slack exibir o título do próprio Canvas — que é
    "Quadro do SAC". Na tela ficam dois iguais; na API, um vazio e um cheio.
    Foi exatamente esse par que passou despercebido."""
    a = checar_abas_do_canal("#sac", [aba(OFICIAL), aba("F0BLE0ZV6KV", "")],
                             {OFICIAL})
    assert a is not None


def test_o_achado_diz_qual_aba_sobra():
    a = checar_abas_do_canal("#sac", [aba(OFICIAL), aba("F0BLE0ZV6KV", "")],
                             {OFICIAL})
    assert "F0BLE0ZV6KV" in a.evidencia


def test_o_achado_nao_manda_apagar_a_oficial():
    a = checar_abas_do_canal("#sac", [aba(OFICIAL), aba("F0BLE0ZV6KV", "")],
                             {OFICIAL})
    assert OFICIAL not in a.evidencia


def test_aba_desconhecida_e_achado_mesmo_sem_duplicata():
    """Canvas que ninguém atualiza é Canvas que envelhece calado — e o chefe
    não tem como saber que está lendo dado de duas semanas atrás."""
    a = checar_abas_do_canal("#sac", [aba("F0DESCONHECIDO", "Outro")],
                             {OFICIAL})
    assert a is not None


def test_rotulos_diferentes_e_ambos_oficiais_passam():
    """#sac-fechamento tem julho e junho ao mesmo tempo, de propósito."""
    abas = [aba("F0JUL", "Balanço julho/2026"), aba("F0JUN", "Balanço junho/2026")]
    assert checar_abas_do_canal("#sac-fechamento", abas,
                                {"F0JUL", "F0JUN"}) is None


def test_abas_que_nao_sao_canvas_sao_ignoradas():
    """`files` e `channel_canvas` são nativas do Slack, não nossas."""
    abas = [aba(OFICIAL),
            {"type": "files", "label": "", "id": "files"},
            {"type": "channel_canvas", "label": "Canvas", "id": "channel_canvas"}]
    assert checar_abas_do_canal("#sac", abas, {OFICIAL}) is None


def test_canal_sem_aba_nao_explode():
    assert checar_abas_do_canal("#sac", [], {OFICIAL}) is None


def test_severidade_e_quebra():
    """Aba errada não é estética: é o chefe lendo número velho achando que é
    o de hoje."""
    a = checar_abas_do_canal("#sac", [aba(OFICIAL), aba("F0BLE0ZV6KV", "")],
                             {OFICIAL})
    assert a.severidade == "quebra"


def test_acao_diz_o_que_fazer():
    a = checar_abas_do_canal("#sac", [aba(OFICIAL), aba("F0BLE0ZV6KV", "")],
                             {OFICIAL})
    assert "aba" in a.acao.lower()


def test_entra_na_bateria_geral():
    import inspect

    import confianca
    assert inspect.getsource(confianca).count("checar_abas_do_canal") >= 2
