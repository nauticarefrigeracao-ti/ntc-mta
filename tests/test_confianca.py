"""Camada de confianca: o sistema tem que provar que esta certo.

Nao basta nao dar erro. Hoje tres coisas passaram como "ok" estando erradas:
o painel caiu com import que nunca foi commitado; o fechamento contava o
mesmo caso duas vezes (prejuizo 45% inflado); e um validador deu "OK" numa
tela de login. Ausencia de erro estava sendo lida como sucesso.

Cada invariante aqui e uma afirmacao que TEM que ser verdade sobre os dados.
Quando quebra, vira um Achado com evidencia numerica e acao -- nao um alerta
vago. A cobertura de conciliacao e uma CATRACA: pode subir, nunca cair.
"""
import pytest

from confianca import (
    Achado,
    checar_cobertura_nao_caiu,
    checar_duplicatas,
    checar_order_ids_reais,
    checar_soma_categorias,
    cobertura_conciliacao,
    formatar_placar,
    severidade_geral,
)


# --- cobertura -------------------------------------------------------------

def test_cobertura_e_a_fracao_conciliada():
    assert cobertura_conciliacao(total=100, conciliados=25) == 25.0


def test_cobertura_de_universo_vazio_e_cem_por_cento():
    # nada para conciliar nao e falha de conciliacao
    assert cobertura_conciliacao(total=0, conciliados=0) == 100.0


def test_cobertura_nunca_passa_de_cem():
    assert cobertura_conciliacao(total=10, conciliados=15) == 100.0


# --- catraca ---------------------------------------------------------------

def test_cobertura_que_sobe_nao_gera_achado():
    assert checar_cobertura_nao_caiu(atual=40.0, anterior=13.8) is None


def test_cobertura_que_cai_gera_quebra():
    a = checar_cobertura_nao_caiu(atual=10.0, anterior=13.8)
    assert a is not None and a.severidade == "quebra"
    assert "13,8" in a.evidencia and "10,0" in a.evidencia


def test_primeira_medicao_nao_tem_com_que_comparar():
    assert checar_cobertura_nao_caiu(atual=13.8, anterior=None) is None


def test_oscilacao_minima_nao_alarma():
    # ruido de arredondamento nao pode virar CI vermelho todo dia
    assert checar_cobertura_nao_caiu(atual=13.79, anterior=13.80) is None


# --- order_id real (R1) ----------------------------------------------------

def test_todos_order_ids_reais_nao_gera_achado():
    assert checar_order_ids_reais([2000012345678901, 2000012345678902]) is None


def test_order_id_curto_e_shipment_e_gera_quebra():
    a = checar_order_ids_reais([2000012345678901, 47536582431])
    assert a is not None and a.severidade == "quebra"
    assert "1" in a.evidencia


def test_lista_vazia_nao_gera_achado():
    assert checar_order_ids_reais([]) is None


# --- duplicatas ------------------------------------------------------------

def test_sem_duplicata_nao_gera_achado():
    assert checar_duplicatas([1, 2, 3]) is None


def test_duplicata_gera_quebra_com_a_conta():
    a = checar_duplicatas([1, 2, 2, 3, 3, 3])
    assert a is not None and a.severidade == "quebra"
    # 6 linhas, 3 distintos -> 3 sobrando
    assert "3" in a.evidencia


# --- soma das categorias ---------------------------------------------------

def test_categorias_que_somam_o_total_nao_geram_achado():
    assert checar_soma_categorias(total=10, categorias={"a": 4, "b": 6}) is None


def test_categoria_faltando_gera_quebra():
    # card na tela que nao fecha com a tabela = numero que mente
    a = checar_soma_categorias(total=10, categorias={"a": 4, "b": 5})
    assert a is not None and a.severidade == "quebra"


# --- severidade / placar ---------------------------------------------------

def test_sem_achados_a_severidade_e_ok():
    assert severidade_geral([]) == "ok"


def test_uma_quebra_derruba_tudo():
    achados = [Achado("i1", "alerta", "x", "y", "z"),
               Achado("i2", "quebra", "x", "y", "z")]
    assert severidade_geral(achados) == "quebra"


def test_so_alertas_nao_derrubam():
    assert severidade_geral([Achado("i1", "alerta", "x", "y", "z")]) == "alerta"


def test_placar_traz_cobertura_e_contagem_de_achados():
    texto = formatar_placar(cobertura=13.8, achados=[Achado("i1", "quebra", "resumo", "ev", "ac")])
    assert "13,8" in texto
    assert "resumo" in texto


def test_placar_limpo_diz_que_esta_limpo():
    texto = formatar_placar(cobertura=100.0, achados=[])
    assert "100,0" in texto
    assert "nenhum" in texto.lower() or "limpo" in texto.lower()


def test_achado_sempre_carrega_acao():
    """Achado sem acao vira alerta vago que ninguem trata."""
    for a in [checar_order_ids_reais([1]), checar_duplicatas([1, 1]),
              checar_cobertura_nao_caiu(atual=1.0, anterior=50.0)]:
        assert a.acao and len(a.acao) > 10
