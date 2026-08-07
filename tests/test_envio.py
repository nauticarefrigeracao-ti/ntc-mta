"""A viagem do pacote — o que o Mercado Livre mostra e o card não mostrava.

O Lucas comparou as duas telas em 07/08/2026. No Meli:

    Em preparação   3 ago 14:51
    A caminho       4 ago 14:36 · 4 ago 18:29 · 5 ago 01:34 · 6 ago 15:21 …
    Entregue        7 ago 10:27

No card do Slack: "Etiqueta gerada" e mais nada. Uma linha contra uma
história.

**O que a API entrega, medido sondando 12 endpoints:**

    /shipments/{id}/history          as etapas, com data e hora
    /shipments/{id}/status_history   as mesmas, com HORAS ÚTEIS em cada uma
    /shipments/{id}/delays           o atraso que o próprio ML declara
    /shipments/{id}/carrier          transportadora e link de rastreio

**O que ela NÃO entrega:** os eventos granulares da transportadora ("Saiu do
centro de distribuição de Guarulhos"). A tela do Meli mostra; nenhum dos 12
endpoints devolve. Registrado como "não encontrado no que sondei", não como
"não existe" — a diferença importa para quem for procurar de novo.

Então o card vai mostrar a viagem em etapas, não o rastro entre cidades. É
menos que a tela do Meli, e dizer isso é melhor que fingir paridade.
"""
from datetime import datetime, timedelta, timezone

import pytest

from envio import (
    atraso_declarado,
    etapas_do_envio,
    linha_do_envio,
    parado_ha,
    rotulo_da_etapa,
)

BRT = timezone(timedelta(hours=-3))
AGORA = datetime(2026, 8, 7, 12, 0, tzinfo=BRT)

# Resposta real de /shipments/47634280810/history, medida em 07/08/2026.
HISTORICO = [
    {"date": "2026-07-29T10:08:51.386-04:00", "substatus": None,
     "status": "handling"},
    {"date": "2026-07-29T10:08:55.437-04:00", "substatus": "ready_to_print",
     "status": "ready_to_ship"},
    {"date": "2026-08-03T10:52:42.000-04:00", "substatus": "printed",
     "status": "ready_to_ship"},
    {"date": "2026-08-03T10:53:42.000-04:00", "substatus": None,
     "status": "shipped"},
]

# Resposta real de /shipments/47634280810/delays.
ATRASOS = [
    {"type": "handling_delayed", "date": "2026-08-01T14:36:44Z",
     "source": "shipping-delays"},
    {"type": "shipping_delayed_original_promise",
     "date": "2026-08-07T05:15:14Z", "source": "shipping-delays"},
]


# --- as etapas -------------------------------------------------------------

def test_historico_vira_etapas_em_ordem():
    e = etapas_do_envio(HISTORICO)
    assert len(e) == 4
    assert [x["quando"].day for x in e] == [29, 29, 3, 3]


def test_etapa_traz_data_e_hora_no_fuso_daqui():
    """O ML devolve -04:00. Lido cru, a hora aparece errada para a Maria."""
    e = etapas_do_envio(HISTORICO)
    assert e[0]["quando"].hour == 11   # 10:08 -04:00 = 11:08 em Brasília


def test_historico_vazio_nao_inventa_etapa():
    assert etapas_do_envio([]) == []
    assert etapas_do_envio(None) == []


def test_etapa_com_data_quebrada_e_pulada_sem_derrubar():
    """Uma linha estranha não pode apagar a viagem inteira."""
    h = HISTORICO + [{"date": "ontem", "status": "delivered"}]
    assert len(etapas_do_envio(h)) == 4


def test_etapas_fora_de_ordem_saem_ordenadas():
    assert etapas_do_envio(list(reversed(HISTORICO)))[0]["quando"].day == 29


# --- os rótulos, em português de gente ------------------------------------

def test_rotulo_diz_o_que_aconteceu_nao_o_codigo():
    """A Maria não lê `ready_to_ship`."""
    assert "_" not in rotulo_da_etapa("ready_to_ship", "printed")
    assert rotulo_da_etapa("shipped", None)


def test_substatus_refina_o_rotulo():
    """`ready_to_print` e `printed` são momentos diferentes: num a etiqueta
    ainda não saiu, no outro o comprador já a tem na mão."""
    assert (rotulo_da_etapa("ready_to_ship", "ready_to_print")
            != rotulo_da_etapa("ready_to_ship", "printed"))


def test_status_desconhecido_nao_vira_none():
    r = rotulo_da_etapa("status_que_nao_existe", None)
    assert r and "None" not in r


# --- há quanto tempo parado -----------------------------------------------

def test_parado_ha_conta_da_ultima_etapa():
    """Cinco dias entre 'etiqueta impressa' e hoje é caso travado — e hoje
    ninguém vê isso em lugar nenhum."""
    assert parado_ha(etapas_do_envio(HISTORICO), AGORA).days == 4


def test_sem_etapa_nao_diz_ha_quanto_tempo():
    assert parado_ha([], AGORA) is None


def test_relogio_atrasado_nao_vira_tempo_negativo():
    e = etapas_do_envio(HISTORICO)
    assert parado_ha(e, datetime(2026, 1, 1, tzinfo=BRT)).total_seconds() == 0


# --- o atraso que o próprio ML declara ------------------------------------

def test_atraso_declarado_pega_o_mais_recente():
    """Dois atrasos: o de manuseio (01/08) e o de entrega (07/08). O que
    importa para a Maria é o de agora."""
    a = atraso_declarado(ATRASOS)
    assert a["quando"].day == 7


def test_atraso_tem_explicacao_em_portugues():
    t = atraso_declarado(ATRASOS)["texto"].lower()
    assert "_" not in t
    assert "atras" in t or "prazo" in t


def test_atraso_de_manuseio_e_de_entrega_dizem_coisas_diferentes():
    """Um é o comprador que não postou; o outro é a transportadora que
    estourou o prazo. Tratar como a mesma coisa manda a Maria cobrar quem
    não deve."""
    so_manuseio = [ATRASOS[0]]
    assert (atraso_declarado(so_manuseio)["texto"]
            != atraso_declarado(ATRASOS)["texto"])


def test_sem_atraso_nao_inventa():
    assert atraso_declarado([]) is None
    assert atraso_declarado(None) is None


def test_atraso_sem_data_nao_quebra():
    assert atraso_declarado([{"type": "handling_delayed", "date": None}]) is None


# --- a linha que vai para o card ------------------------------------------

def test_linha_mostra_as_etapas_com_data():
    t = linha_do_envio(etapas_do_envio(HISTORICO))
    assert "29/07" in t and "03/08" in t


def test_linha_cabe_no_card():
    """Card com dez linhas de viagem esconde o que a Maria precisa decidir."""
    t = linha_do_envio(etapas_do_envio(HISTORICO * 6))
    assert len(t.splitlines()) <= 5


def test_linha_corta_as_mais_antigas_e_avisa():
    """Some o começo, não o fim: o que importa é onde o pacote está agora."""
    t = linha_do_envio(etapas_do_envio(HISTORICO * 6))
    assert "+" in t or "anterior" in t.lower()


def test_sem_etapas_nao_polui_o_card():
    assert linha_do_envio([]) == ""
