"""A mensagem diz R$ 659,00; o chefe clica e o Mercado Livre diz R$ 1.318,00.

05/08/2026, conferência mensagem por mensagem contra a API do ML (145 pedidos
conferidos, paginado). Duas divergências de valor, e as duas com a mesma
causa:

    2000017376418588   painel R$   659,00   ML R$ 1.318,00   (2 unidades)
    2000017277002676   painel R$   232,98   ML R$   310,64   (4 unidades)

`ml_devolucoes.order_total` não é o total do pedido quando há mais de uma
unidade. `order_items` tem o dado certo: unidades × preço unitário fecha com
o ML nos dois casos.

Por que isso é grave e não cosmético: a mensagem existe para o chefe e a Maria
clicarem no link e conferirem. Se o número da mensagem não bate com o número
da tela do ML, a mensagem inteira perde valor — e junto com ela o painel, o
Canvas e o balanço, porque vêm todos da mesma fonte.

Regra que fica: **valor publicado é valor que fecha com a tela do ML.** Onde
`order_items` existe, ele manda; `order_total` é fallback.
"""
import pytest

from slack_notify import bloco_financeiro, valor_da_venda


def test_multiplas_unidades_usam_itens_do_pedido():
    """2 × R$ 659,00 = R$ 1.318,00, que é o que o ML mostra."""
    assert valor_da_venda({"order_total": 659.00},
                          itens=[{"unidades": 2, "preco_unitario": 659.00}]
                          ) == pytest.approx(1318.00)


def test_o_caso_das_quatro_unidades():
    assert valor_da_venda({"order_total": 232.98},
                          itens=[{"unidades": 4, "preco_unitario": 77.66}]
                          ) == pytest.approx(310.64)


def test_unidade_unica_continua_igual():
    assert valor_da_venda({"order_total": 499.00},
                          itens=[{"unidades": 1, "preco_unitario": 499.00}]
                          ) == pytest.approx(499.00)


def test_varios_itens_somam():
    assert valor_da_venda({"order_total": 0},
                          itens=[{"unidades": 2, "preco_unitario": 100.0},
                                 {"unidades": 1, "preco_unitario": 50.0}]
                          ) == pytest.approx(250.0)


def test_sem_itens_cai_no_order_total():
    """Nem todo pedido tem itens sincronizados. Melhor o valor antigo que
    nenhum — mas só quando não há alternativa."""
    assert valor_da_venda({"order_total": 499.00}, itens=None
                          ) == pytest.approx(499.00)


def test_sem_itens_e_sem_total_e_desconhecido():
    """Zero nunca é "grátis": é "não sincronizado". Publicar R$ 0,00 como
    valor da venda foi defeito corrigido em 31/07 e não pode voltar."""
    assert valor_da_venda({"order_total": 0}, itens=None) is None


def test_itens_zerados_nao_apagam_o_total():
    """Item com unidades 0 é dado sujo, não venda de graça."""
    assert valor_da_venda({"order_total": 499.00},
                          itens=[{"unidades": 0, "preco_unitario": 0}]
                          ) == pytest.approx(499.00)


def test_item_sem_preco_nao_derruba_a_soma():
    v = valor_da_venda({"order_total": 100.0},
                       itens=[{"unidades": 2, "preco_unitario": 100.0},
                              {"unidades": 1, "preco_unitario": None}])
    assert v == pytest.approx(200.0)


# --- o que sai na mensagem -------------------------------------------------

def test_mensagem_publica_o_valor_que_fecha_com_o_ml():
    txt = bloco_financeiro({"order_total": 659.00, "claim_status": "opened"},
                           None,
                           itens=[{"unidades": 2, "preco_unitario": 659.00}])
    assert "1.318,00" in txt


def test_mensagem_nao_publica_o_valor_unitario():
    txt = bloco_financeiro({"order_total": 659.00, "claim_status": "opened"},
                           None,
                           itens=[{"unidades": 2, "preco_unitario": 659.00}])
    assert "R$ 659,00" not in txt


def test_sem_dado_continua_dizendo_nao_sincronizado():
    txt = bloco_financeiro({"order_total": 0, "claim_status": "opened"}, None)
    assert "não sincronizado" in txt


def test_chamada_antiga_sem_itens_nao_quebra():
    """Compatibilidade: o resto do módulo chama sem `itens`."""
    txt = bloco_financeiro({"order_total": 499.0, "claim_status": "opened"}, None)
    assert "499,00" in txt
