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
    IRRECUPERAVEIS,
    Achado,
    checar_cobertura_nao_caiu,
    checar_conciliados_nao_caiu,
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


# --- catraca sobre o ABSOLUTO, nao sobre o percentual ----------------------
# 31/07/2026: resolver 2.920 shipments fez a cobertura CAIR de 9,1% para 7,6%
# -- nao porque algo piorou, mas porque 2.920 pedidos validos entraram no
# denominador. A catraca percentual acusou uma correcao bem-sucedida como
# regressao. O numero de casos conciliados (absoluto) so cai quando ha perda
# de verdade, entao e ele que serve de catraca.

def test_absoluto_que_sobe_nao_gera_achado():
    assert checar_conciliados_nao_caiu(atual=1372, anterior=1300) is None


def test_absoluto_estavel_nao_gera_achado():
    assert checar_conciliados_nao_caiu(atual=1372, anterior=1372) is None


def test_absoluto_que_cai_gera_quebra():
    a = checar_conciliados_nao_caiu(atual=1200, anterior=1372)
    assert a is not None and a.severidade == "quebra"
    assert "172" in a.evidencia


def test_primeira_medicao_do_absoluto_nao_compara():
    assert checar_conciliados_nao_caiu(atual=1372, anterior=None) is None


def test_universo_maior_derruba_o_percentual_mas_nao_o_absoluto():
    """O caso real: 2.920 pedidos entraram no denominador."""
    # percentual cai...
    assert checar_cobertura_nao_caiu(atual=7.6, anterior=9.1) is not None
    # ...mas nenhum caso conciliado foi perdido
    assert checar_conciliados_nao_caiu(atual=1372, anterior=1372) is None


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


def test_order_id_de_11_digitos_e_shipment_e_gera_quebra():
    a = checar_order_ids_reais([2000012345678901, 47536582431])
    assert a is not None and a.severidade == "quebra"
    assert "1" in a.evidencia


def test_lista_vazia_nao_gera_achado():
    assert checar_order_ids_reais([]) is None


# --- formato do order_id: medido na API, nao suposto -----------------------
# A primeira versao usava "< 15 digitos = shipment" e estava ERRADA. Medicao
# na API do ML (30/07/2026):
#   10 digitos (5.099) -> PEDIDO antigo legitimo   (6/6 abrem em /orders/)
#   11 digitos (2.922) -> SHIPMENT                 (8/8 em /shipments/)
#   16 digitos (10.092, 2000…) -> pedido novo
# O shipment de 11 digitos resolve para um order de 10 -- ou seja, a regra
# antiga acusaria o RESULTADO da propria correcao como defeito, para sempre.

def test_pedido_antigo_de_10_digitos_nao_e_acusado():
    # 5.462.527.754 abre em /orders/ (status=cancelled, "Cj 4 Pé Nivelador")
    assert checar_order_ids_reais([5462527754]) is None


def test_pedido_antigo_e_novo_convivem_sem_achado():
    assert checar_order_ids_reais([5462527754, 2000012345678901]) is None


def test_resultado_da_resolucao_nao_vira_novo_achado():
    """Regressao: resolver 11 digitos gera order de 10. Se 10 fosse acusado,
    o CI ficaria vermelho para sempre depois de uma correcao BEM-SUCEDIDA."""
    resolvido = 4351746836  # veio de /shipments/40388797435
    assert checar_order_ids_reais([resolvido]) is None


def test_id_curto_demais_ainda_e_suspeito():
    # nem pedido (10/16) nem shipment (11) -- nao abre link nenhum
    a = checar_order_ids_reais([12345])
    assert a is not None and a.severidade == "quebra"


# --- irrecuperaveis: dado que o proprio ML nao tem mais -------------------
# Sem isto a invariante fica vermelha para sempre por causa de 2 casos de
# 2021/2024 que nao ha como consertar -- e invariante que grita sem acao
# possivel vira ruido que ninguem trata.

def test_caso_irrecuperavel_nao_gera_achado():
    assert checar_order_ids_reais([40627136344]) is None


def test_irrecuperavel_nao_esconde_shipment_novo():
    """A lista dispensa casos verificados, nao a invariante inteira."""
    a = checar_order_ids_reais([40627136344, 47536582431])
    assert a is not None and "1 caso" in a.evidencia


def test_todo_irrecuperavel_tem_motivo_escrito():
    for oid, motivo in IRRECUPERAVEIS.items():
        assert len(motivo) > 25, f"{oid} sem evidência do porquê"


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

