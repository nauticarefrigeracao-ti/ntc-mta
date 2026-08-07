"""O card bate com o Meli? — a conferência que evita passar vergonha.

A regra da casa: nada é validado até ser visto na interface com que a pessoa
interage. Aqui a interface é o card no Slack, e a verdade é a tela do
Mercado Livre.

O medo concreto: a Thayná abre o `#sac-teste`, clica no número do pedido, e o
que está no Slack não bate com o que abre no Meli. Já aconteceu uma vez — o
card imprimia o `order_id` e o Meli mostrava o `pack_id`.

Três coisas que este arquivo separa, porque são problemas diferentes:

**Divergência de DADO** — o card diz R$ 659,00 e o Meli diz R$ 1.318,00. É
defeito, e não tem desculpa.

**Divergência de TEMPO** — o card diz "etiqueta gerada" e o Meli já diz "a
caminho", porque o Meli mudou há 20 minutos e o card ainda não foi
redesenhado. Não é defeito: é atraso, e o que importa é o TAMANHO dele.

**Ausência** — o caso existe no Meli e não existe card nenhum. Pior que
divergir, porque não aparece para ninguém.
"""
from datetime import datetime, timedelta, timezone

import pytest

from paridade_card import (
    atraso_de,
    classificar,
    comparar,
    explicar,
)

AGORA = datetime(2026, 8, 7, 15, 0, tzinfo=timezone.utc)


def card(**kw):
    base = {"numero": 2000014291726681, "sku": "NR4321", "unidades": 2,
            "valor": 37.80, "estado": "label_generated"}
    base.update(kw)
    return base


def meli(**kw):
    base = {"numero": 2000014291726681, "sku": "NR4321", "unidades": 2,
            "valor": 37.80, "estado": "label_generated"}
    base.update(kw)
    return base


# --- o caso feliz ---------------------------------------------------------

def test_tudo_igual_nao_diverge():
    assert comparar(card(), meli()) == []


def test_card_ausente_e_a_pior_divergencia():
    """Caso que existe no Meli e não tem card não aparece para ninguém."""
    d = comparar(None, meli())
    assert d and d[0]["campo"] == "card"
    assert classificar(d[0]) == "ausencia"


# --- divergência de dado --------------------------------------------------

def test_valor_diferente_e_divergencia():
    d = comparar(card(valor=659.00), meli(valor=1318.00))
    assert [x["campo"] for x in d] == ["valor"]
    assert classificar(d[0]) == "dado"


def test_numero_diferente_e_divergencia():
    """O número impresso tem que ser o que a tela do Meli mostra — foi
    exatamente esse o defeito de 06/08."""
    d = comparar(card(numero=2000017686941586), meli(numero=2000014291726681))
    assert [x["campo"] for x in d] == ["numero"]


def test_sku_diferente_e_divergencia():
    assert [x["campo"] for x in comparar(card(sku="NR9999"), meli())] == ["sku"]


def test_unidades_diferentes_e_divergencia():
    assert comparar(card(unidades=1), meli(unidades=2))[0]["campo"] == "unidades"


def test_centavo_de_arredondamento_nao_e_divergencia():
    """Float de banco contra float de API difere no último centavo. Acusar
    isso encheria o relatório de ruído e esconderia o que importa."""
    assert comparar(card(valor=37.80), meli(valor=37.799999)) == []


def test_diferenca_de_um_real_e_divergencia():
    assert comparar(card(valor=37.80), meli(valor=38.80))


def test_valor_ausente_de_um_lado_e_divergencia():
    """Não saber o valor não é o mesmo que os valores baterem."""
    assert comparar(card(valor=None), meli(valor=37.80))


# --- divergência de tempo -------------------------------------------------

def test_estado_diferente_e_atraso_nao_defeito():
    """O Meli mudou e o card ainda não foi redesenhado. Isso é atraso — e o
    que importa é o tamanho dele, não o fato."""
    d = comparar(card(estado="label_generated"), meli(estado="shipped"))
    assert classificar(d[0]) == "tempo"


def test_atraso_e_a_distancia_entre_as_duas_atualizacoes():
    mudou = AGORA - timedelta(minutes=47)
    assert atraso_de(mudou, AGORA) == pytest.approx(47 * 60)


def test_sem_saber_quando_mudou_nao_inventa_atraso():
    assert atraso_de(None, AGORA) is None


def test_atraso_negativo_vira_zero():
    """Relógio fora de sincronia não pode virar atraso negativo no relatório."""
    assert atraso_de(AGORA + timedelta(minutes=5), AGORA) == 0


# --- a explicação para quem não é técnico ---------------------------------

def test_explicacao_de_dado_diz_os_dois_valores():
    d = comparar(card(valor=659.00), meli(valor=1318.00))[0]
    t = explicar(d)
    assert "659" in t and "1.318" in t


def test_explicacao_nao_usa_jargao():
    """Quem lê é a Thayná."""
    d = comparar(card(sku="NR9999"), meli())[0]
    for palavra in ("payload", "api", "json", "endpoint", "query", "null"):
        assert palavra not in explicar(d).lower()


def test_explicacao_de_tempo_diz_que_e_atraso_e_nao_erro():
    d = comparar(card(estado="label_generated"), meli(estado="shipped"))[0]
    t = explicar(d).lower()
    assert "atras" in t or "ainda não" in t or "ainda nao" in t


def test_explicacao_de_ausencia_diz_que_nao_aparece():
    t = explicar(comparar(None, meli())[0]).lower()
    assert "não aparece" in t or "nao aparece" in t
