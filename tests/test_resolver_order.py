"""Resolver shipment_id -> order_id real nos claims historicos.

Claims de cancel_purchase guardam um SHIPMENT id em resource_id, nao o id do
pedido. Foi isso que gerou o link 404 que o Gabriel abriu. O sync passou a
resolver isso nos claims ABERTOS, mas 46 casos fechados seguem com shipment
gravado -- nao batem com meli_page_saldos (chaveado por order_id) e por isso
caem como "conciliacao pendente" no fechamento do chefe.
"""
from unittest.mock import patch

from resolver_order import em_lotes, parece_shipment, resolver_order_id


# --- lotes: o Neon derruba conexao longa ------------------------------------
# "SSL connection has been closed unexpectedly" no meio de um run de 12k --
# sem lote, o commit final leva junto tudo que ja tinha sido resolvido.

def test_divide_em_lotes_do_tamanho_pedido():
    assert list(em_lotes([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_lote_maior_que_a_lista_devolve_uma_leva_so():
    assert list(em_lotes([1, 2], 10)) == [[1, 2]]


def test_lista_vazia_nao_gera_lote():
    assert list(em_lotes([], 5)) == []


def test_tamanho_invalido_nao_trava_em_loop_infinito():
    assert list(em_lotes([1, 2, 3], 0)) == [[1, 2, 3]]


def test_id_de_16_digitos_e_pedido_real():
    assert parece_shipment(2000012345678901) is False


def test_id_de_11_digitos_e_shipment():
    assert parece_shipment(47536582431) is True


def test_pedido_antigo_de_10_digitos_nao_e_shipment():
    """Medido na API: 5.462.527.754 abre em /orders/ (status=cancelled).
    Tratar 10 digitos como shipment gastaria 5.099 chamadas a toa e, na
    invariante, acusaria pedido valido."""
    assert parece_shipment(5462527754) is False


def test_order_resolvido_de_shipment_nao_vira_shipment_de_novo():
    """/shipments/40388797435 -> order 4351746836 (10 digitos). Se 10 fosse
    shipment, o resolver entraria em loop tentando reresolver o que acabou
    de consertar."""
    assert parece_shipment(4351746836) is False


def test_id_ausente_nao_e_shipment():
    # nao ha o que resolver; nunca tratar None como shipment
    assert parece_shipment(None) is False


def test_id_como_texto_tambem_e_avaliado():
    assert parece_shipment("47536582431") is True
    assert parece_shipment("2000012345678901") is False
    assert parece_shipment("5462527754") is False


def test_resolve_shipment_para_order_do_pedido():
    with patch("resolver_order.ml_client.get_shipment",
               return_value={"id": 47536582431, "order_id": 2000012345678901}):
        assert resolver_order_id(47536582431) == 2000012345678901


def test_api_sem_resposta_devolve_none_sem_levantar():
    with patch("resolver_order.ml_client.get_shipment", return_value=None):
        assert resolver_order_id(47536582431) is None


def test_shipment_sem_order_id_devolve_none():
    with patch("resolver_order.ml_client.get_shipment", return_value={"id": 47536582431}):
        assert resolver_order_id(47536582431) is None


def test_nao_chama_api_para_order_que_ja_e_real():
    with patch("resolver_order.ml_client.get_shipment") as get:
        assert resolver_order_id(2000012345678901) == 2000012345678901
    get.assert_not_called()


def test_order_resolvido_precisa_ser_plausivel():
    # se a API devolver outro id curto, nao aceitamos -- trocar um link
    # quebrado por outro link quebrado nao e correcao
    with patch("resolver_order.ml_client.get_shipment",
               return_value={"order_id": 12345}):
        assert resolver_order_id(47536582431) is None
