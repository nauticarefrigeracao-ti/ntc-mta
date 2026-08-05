"""Auditar o Canvas SEM `files:read` — o escopo que já temos basta.

Desde 03/08 a auditoria dizia "não consigo ler os Canvas, falta files:read /
canvases:read". Metade disso estava errado: o token JÁ tem `canvases:read`
(medido em 05/08 no header `x-oauth-scopes`). Só `files:read` falta, e ele é
exigido por `files.info`/`files.list` — não pela API de Canvas.

Medido em 05/08 contra os Canvas reais do #sac-fechamento:

    canvases.sections.lookup {"contains_text": "..."}   6 de 6 acertos
        'Prejuízo do mês'       -> 1 seção   (está lá, e deveria)
        'Onde o dinheiro vaza'  -> 1 seção   (está lá, e deveria)
        'Saldo do mês'          -> 0 seções  (removido em 03/08, e não está)
        'zzz nao existe zzz'    -> 0 seções

Ou seja: dá para provar o que o Canvas contém, e o que ele NÃO contém, com o
token atual. Sem reinstalar o app, sem rotacionar token, sem tocar em escopo.

O que continua fora de alcance: LISTAR canvas do canal (isso é `files.list`).
Então a auditoria confere os Canvas que nós publicamos — os IDs estão no
banco — e não descobre um canvas órfão criado por outra pessoa. Isso fica
declarado, não escondido.
"""
import pytest

from auditoria_slack import MARCOS_ESPERADOS, verificar_marcos


class ApiFalsa:
    """Responde como o Slack: seções quando o texto existe, vazio quando não."""

    def __init__(self, textos_presentes, recusa=False):
        self.presentes = textos_presentes
        self.recusa = recusa
        self.chamadas = []

    def __call__(self, metodo, payload, get=False):
        self.chamadas.append((metodo, payload))
        if self.recusa:
            return None
        alvo = payload.get("criteria", "")
        achou = any(t in alvo for t in self.presentes)
        return {"ok": True, "sections": [{"id": "temp:C:x"}] if achou else []}


def test_marco_presente_e_reconhecido():
    api = ApiFalsa(["Prejuízo do mês"])
    r = verificar_marcos("F123", ["Prejuízo do mês"], api=api)
    assert r == {"Prejuízo do mês": True}


def test_marco_ausente_e_apontado():
    api = ApiFalsa([])
    r = verificar_marcos("F123", ["Prejuízo do mês"], api=api)
    assert r == {"Prejuízo do mês": False}


def test_usa_o_metodo_de_canvas_e_nao_files_info():
    """`files.info` exige files:read e é justamente o que não temos."""
    api = ApiFalsa(["x"])
    verificar_marcos("F123", ["x"], api=api)
    metodos = {m for m, _ in api.chamadas}
    assert metodos == {"canvases.sections.lookup"}


def test_manda_o_canvas_id_certo():
    api = ApiFalsa(["x"])
    verificar_marcos("F999", ["x"], api=api)
    assert api.chamadas[0][1]["canvas_id"] == "F999"


def test_api_que_recusa_devolve_none_e_nao_false():
    """False diria "o texto não está lá". None diz "não consegui olhar".
    Confundir os dois é o mesmo defeito de aprovar uma tela de login."""
    api = ApiFalsa([], recusa=True)
    r = verificar_marcos("F123", ["Prejuízo do mês"], api=api)
    assert r == {"Prejuízo do mês": None}


def test_varios_marcos_de_uma_vez():
    api = ApiFalsa(["Prejuízo", "vaza"])
    r = verificar_marcos("F123", ["Prejuízo", "vaza", "Saldo do mês"], api=api)
    assert r == {"Prejuízo": True, "vaza": True, "Saldo do mês": False}


def test_aspas_no_marco_nao_quebram_o_json():
    """`criteria` é JSON montado à mão; aspas no texto quebrariam a chamada."""
    api = ApiFalsa(['ele disse ola'])
    verificar_marcos("F123", ['ele disse "ola"'], api=api)
    import json
    json.loads(api.chamadas[0][1]["criteria"])  # não pode levantar


def test_sem_marcos_nao_chama_a_api():
    api = ApiFalsa([])
    assert verificar_marcos("F123", [], api=api) == {}
    assert api.chamadas == []


# --- os marcos de cada canal -----------------------------------------------

def test_o_canal_de_fechamento_cobra_o_titular_do_balanco():
    marcos = MARCOS_ESPERADOS["#sac-fechamento"]
    assert any("Prejuízo" in m for m in marcos)


def test_o_fechamento_cobra_a_secao_de_vazamento():
    """"Onde o dinheiro vaza" é a seção que o chefe usa para decidir compra."""
    marcos = MARCOS_ESPERADOS["#sac-fechamento"]
    assert any("vaza" in m for m in marcos)


def test_o_sac_cobra_as_colunas_do_quadro():
    marcos = MARCOS_ESPERADOS["#sac"]
    assert any("A Fazer" in m for m in marcos)


def test_nenhum_marco_e_o_titulo_antigo_que_mentia():
    """"Saldo do mês" somava receita com prejuízo e dizia que devolução dá
    lucro. Se voltar como marco esperado, o defeito volta com ele."""
    for canal, marcos in MARCOS_ESPERADOS.items():
        assert "Saldo do mês" not in marcos, canal
