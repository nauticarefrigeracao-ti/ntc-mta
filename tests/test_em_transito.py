""""As que chegam hoje" — o coração do quadro que a Thayná desenhou.

Medido em 06/08/2026: dos 32 casos abertos, a previsão de entrega estava em
**0**. Não porque o Mercado Livre não dá — porque nunca fomos buscar. O dado
mora em dois lugares:

    /post-purchase/v2/claims/{id}/returns  -> shipments[].destination
    /shipments/{id}  (header x-format-new) -> lead_time.estimated_delivery_time

Dois defeitos que este arquivo existe para impedir:

**1. Nem toda devolução chega na loja.** A amostra real (claim 5550146826) vai
para o galpão do Mercado Livre em Cajamar — `types: [warehouse, triage]`. A
Maria nunca vai bipar essa etiqueta, porque o pacote não passa por ela. Se o
Slack perguntar "já chegou?" para uma caixa que nunca chega, ele ensina a
Maria a ignorar a pergunta — e aí perde as que importam.

**2. Fuso horário come um dia.** O ML devolve `2026-08-21T00:00:00.000-03:00`.
Lido como UTC, isso vira 21/08 03:00 — mesmo dia, por sorte. Mas
`2026-08-21T23:00:00-03:00` lido como UTC vira **22/08**. Um dia a mais na
previsão é a devolução aparecendo na lista errada, e a Maria procurando um
pacote que já chegou ontem.
"""
from datetime import date

import pytest

from em_transito import (
    chega_ate,
    destino_do_shipment,
    dia_da_previsao,
    previsao_do_detalhe,
    resumo_do_retorno,
)


# --- destino: loja ou Full ------------------------------------------------

WAREHOUSE = {"destination": {"name": "warehouse", "shipping_address": {
    "types": ["logistic_center_BRSP29", "warehouse", "triage"],
    "city": {"name": "Cajamar"}}}}

LOJA = {"destination": {"name": "seller_address", "shipping_address": {
    "types": ["default_selling_address"],
    "city": {"name": "Praia Grande"}}}}


def test_warehouse_e_full():
    assert destino_do_shipment(WAREHOUSE) == "full"


def test_endereco_do_vendedor_e_loja():
    assert destino_do_shipment(LOJA) == "loja"


def test_triage_sem_warehouse_ainda_e_full():
    """Centro de triagem do ML também não passa pela Maria."""
    s = {"destination": {"shipping_address": {"types": ["triage"]}}}
    assert destino_do_shipment(s) == "full"


def test_destino_ausente_nao_vira_loja():
    """Chutar "loja" faria o Slack cobrar confirmação de um pacote que talvez
    nunca chegue. Desconhecido tem que aparecer como desconhecido."""
    assert destino_do_shipment({}) is None


def test_destino_sem_tipos_usa_o_nome():
    assert destino_do_shipment({"destination": {"name": "warehouse"}}) == "full"


# --- previsão de entrega --------------------------------------------------

DETALHE = {"lead_time": {
    "estimated_delivery_time": {"date": "2026-08-21T00:00:00.000-03:00"},
    "estimated_delivery_limit": {"date": "2026-08-22T00:00:00.000-03:00"},
    "shipping_method": {"name": "Devolução padrão"}}}


def test_previsao_sai_do_lead_time():
    assert previsao_do_detalhe(DETALHE) == "2026-08-21T00:00:00.000-03:00"


def test_sem_lead_time_nao_inventa_previsao():
    assert previsao_do_detalhe({}) is None


def test_lead_time_nulo_nao_explode():
    assert previsao_do_detalhe({"lead_time": None}) is None


def test_previsao_cai_no_limite_quando_nao_ha_estimativa():
    """`estimated_delivery_time` às vezes vem nulo e o `limit` não. Pior data
    conhecida é melhor que data nenhuma — e é a que não atrasa a Maria."""
    d = {"lead_time": {"estimated_delivery_time": {"date": None},
                       "estimated_delivery_limit": {"date": "2026-08-22T00:00:00.000-03:00"}}}
    assert previsao_do_detalhe(d) == "2026-08-22T00:00:00.000-03:00"


# --- o dia, sem perder para o fuso ----------------------------------------

def test_dia_respeita_o_fuso_do_mercado_livre():
    assert dia_da_previsao("2026-08-21T00:00:00.000-03:00") == date(2026, 8, 21)


def test_fim_do_dia_nao_pula_para_o_seguinte():
    """23h no fuso de Brasília lido como UTC viraria 22/08. A devolução
    apareceria na lista de amanhã e a Maria procuraria um pacote que já
    chegou."""
    assert dia_da_previsao("2026-08-21T23:00:00.000-03:00") == date(2026, 8, 21)


def test_data_vazia_nao_vira_hoje():
    assert dia_da_previsao(None) is None


def test_data_quebrada_nao_explode():
    assert dia_da_previsao("sem data") is None


def test_chega_ate_hoje():
    assert chega_ate("2026-08-06T00:00:00.000-03:00", date(2026, 8, 6))


def test_atrasado_tambem_entra_na_lista_de_hoje():
    """Previsão de ontem que não chegou é mais urgente que a de hoje, não
    menos. Sumir com ela do quadro é como a devolução deixar de existir."""
    assert chega_ate("2026-08-05T00:00:00.000-03:00", date(2026, 8, 6))


def test_futuro_nao_entra():
    assert not chega_ate("2026-08-21T00:00:00.000-03:00", date(2026, 8, 6))


def test_sem_previsao_nao_entra_na_lista_de_hoje():
    """Sem data não é "chega hoje": é "não sabemos". Entram numa seção
    própria, para não sumirem nem poluírem."""
    assert not chega_ate(None, date(2026, 8, 6))


# --- resumo de um retorno --------------------------------------------------

RETORNO = {
    "id": 151471892,
    "status": "shipped",
    "subtype": "return_total",
    "status_money": "retained",
    "shipments": [{
        "shipment_id": 47621259856,
        "status": "shipped",
        "tracking_number": "AP273556466BR",
        "destination": WAREHOUSE["destination"],
        "type": "return",
    }],
}


def test_resumo_traz_o_shipment():
    r = resumo_do_retorno(RETORNO)
    assert r["shipment_id"] == 47621259856
    assert r["tracking_number"] == "AP273556466BR"


def test_resumo_classifica_o_destino():
    assert resumo_do_retorno(RETORNO)["destino"] == "full"


def test_resumo_sem_shipment_nao_explode():
    r = resumo_do_retorno({"id": 1, "status": "opened", "shipments": []})
    assert r["shipment_id"] is None and r["destino"] is None


def test_resumo_ignora_shipment_que_nao_e_devolucao():
    """O pacote da VENDA também aparece; ele não é o que volta."""
    payload = {"id": 1, "shipments": [
        {"shipment_id": 111, "type": "forward"},
        {"shipment_id": 222, "type": "return", "destination": LOJA["destination"]},
    ]}
    assert resumo_do_retorno(payload)["shipment_id"] == 222


def test_resumo_guarda_o_status_do_dinheiro():
    """`retained` = o ML ainda segura o valor. É o que separa 'já perdemos'
    de 'ainda dá para reaver'."""
    assert resumo_do_retorno(RETORNO)["status_money"] == "retained"
