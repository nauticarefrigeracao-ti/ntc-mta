"""Resolver shipment_id -> order_id real nos claims historicos.

Claims de cancel_purchase guardam um SHIPMENT id em resource_id, nao o id do
pedido. Foi isso que gerou o link 404 que o Gabriel abriu. O sync passou a
resolver isso nos claims ABERTOS, mas 46 casos fechados seguem com shipment
gravado -- nao batem com meli_page_saldos (chaveado por order_id) e por isso
caem como "conciliacao pendente" no fechamento do chefe.
"""
from unittest.mock import patch

from resolver_order import parece_shipment, resolver_order_id


def test_id_de_16_digitos_e_pedido_real():
    assert parece_shipment(2000012345678901) is False


def test_id_curto_e_shipment():
    assert parece_shipment(47536582431) is True


def test_id_ausente_nao_e_shipment():
    # nao ha o que resolver; nunca tratar None como shipment
    assert parece_shipment(None) is False


def test_id_como_texto_tambem_e_avaliado():
    assert parece_shipment("47536582431") is True
    assert parece_shipment("2000012345678901") is False


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
