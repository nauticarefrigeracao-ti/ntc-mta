"""Conciliação mensal: o número que a diretoria vai conferir linha a linha.

Reunião de 05/08/2026 com Thayná e Gabriel: fechar julho caso a caso, batendo
Slack, painel do Mercado Livre e os sistemas NTC. Não é relatório de leitura —
é conferência. Cada linha vai ser aberta.

O que os testes fecham:

**A soma da página do ML tem que fechar com o total dela.** O painel do ML
mostra produto, tarifa, envios, cancelamentos e parcelamento, e no fim o
Total. Nossa coleta grava as cinco parcelas em colunas e o Total em outra. Se
as parcelas não somam o Total, a coleta perdeu uma linha — foi exatamente o
caso do pedido 2000017711327732, em que a diferença dava *exatamente* a tarifa
de venda. Divergência não conferida vira número errado na frente do chefe.

**Revertido não é lucro.** Total positivo em caso de reclamação é receita da
venda que ficou de pé. Somar com prejuízo produz manchete positiva num painel
de perdas — defeito que já apareceu duas vezes (Canvas mensal e fechamento
diário).

**Caso sem saldo não vale zero.** Zero é resultado medido ("o ML cobriu");
ausente é resultado desconhecido. Confundir os dois faz o mês parecer fechado
quando ainda falta apurar.
"""
import pytest

from conciliacao import (
    classificar,
    consolidar,
    divergencia,
    fmt_valor,
    linha_csv,
    soma_componentes,
)


def row(total=None, produto=None, tarifa=None, envios=None, canc=None,
        parc=None, **kw):
    base = {"order_id": 2000017000000001, "claim_id": 1, "item_sku": "NR0001",
            "item_title": "Sensor", "reason_label": "Arrependimento",
            "date_updated": "2026-07-15T10:00:00Z", "total": total,
            "produto": produto, "tarifa_venda": tarifa, "envios": envios,
            "cancelamentos": canc, "parcelamento": parc}
    base.update(kw)
    return base


# --- classificação ---------------------------------------------------------

def test_negativo_e_prejuizo():
    assert classificar(row(total=-284.74)) == "prejuizo"


def test_zero_e_coberto():
    """Zero é medido: o ML cobriu. Não é 'sem informação'."""
    assert classificar(row(total=0.0)) == "coberto"


def test_positivo_e_revertido():
    assert classificar(row(total=425.35)) == "revertido"


def test_ausente_e_pendente_nao_coberto():
    """Confundir ausente com zero faz o mês parecer fechado com apuração
    faltando."""
    assert classificar(row(total=None)) == "pendente"


# --- paridade dentro da própria página do ML ------------------------------

def test_componentes_somam_o_total():
    r = row(total=-284.74, produto=786.66, tarifa=-86.53, envios=-210.75,
            canc=-774.12, parc=0.0)
    assert soma_componentes(r) == pytest.approx(-284.74, abs=0.01)
    assert divergencia(r) == pytest.approx(0.0, abs=0.01)


def test_componente_faltando_vira_divergencia():
    """2000017711327732: a diferença dava exatamente a tarifa de venda."""
    r = row(total=-97.44, produto=100.0, tarifa=None, envios=-148.05,
            canc=0.0, parc=0.0)
    d = divergencia(r)
    assert d is not None and abs(d) > 0.01


def test_divergencia_sem_total_e_indefinida():
    """Sem Total não há o que conferir — devolver 0,0 diria 'confere'."""
    assert divergencia(row(total=None, produto=10.0)) is None


def test_divergencia_sem_nenhum_componente_e_indefinida():
    """Página não coletada não é página que fecha."""
    assert divergencia(row(total=-50.0)) is None


def test_tolerancia_de_centavo_nao_vira_achado():
    """Arredondamento do ML não pode gerar 290 achados falsos."""
    r = row(total=-284.74, produto=786.66, tarifa=-86.53, envios=-210.75,
            canc=-774.11, parc=0.0)
    assert abs(divergencia(r)) <= 0.02


# --- consolidação ----------------------------------------------------------

def _mes():
    # order_id distinto por caso: `meli_page_saldos` é por pedido, então
    # repetir o pedido faria o dinheiro contar uma vez só — de propósito.
    return [
        row(total=-284.74, claim_id=1, order_id=101, produto=786.66,
            tarifa=-86.53, envios=-210.75, canc=-774.12, parc=0.0),
        row(total=-100.0, claim_id=2, order_id=102),
        row(total=0.0, claim_id=3, order_id=103),
        row(total=425.35, claim_id=4, order_id=104),
        row(total=None, claim_id=5, order_id=105),
    ]


def test_prejuizo_soma_so_os_negativos():
    c = consolidar(_mes())
    assert c["prejuizo"] == pytest.approx(-384.74)


def test_revertido_nao_entra_no_prejuizo():
    c = consolidar(_mes())
    assert c["revertido"] == pytest.approx(425.35)
    assert c["prejuizo"] != pytest.approx(-384.74 + 425.35)


def test_contagens_batem_com_o_total():
    c = consolidar(_mes())
    assert c["casos"] == 5
    assert (c["n_prejuizo"] + c["n_coberto"] + c["n_revertido"]
            + c["n_pendente"]) == c["casos"]


def test_cobertura_nao_arredonda_para_cem_com_pendente():
    """"100% conferido" com um caso faltando é a mentira mais cara aqui."""
    c = consolidar(_mes())
    assert c["cobertura_pct"] < 100


def test_mes_sem_pendencia_fecha_em_cem():
    c = consolidar([row(total=-10.0, claim_id=1), row(total=0.0, claim_id=2)])
    assert c["cobertura_pct"] == 100.0


def test_claim_repetido_nao_infla_o_prejuizo():
    """Um claim tem várias chaves de estado; o JOIN devolve uma linha por
    chave. Sem dedup o prejuízo do chefe sai dobrado."""
    c = consolidar([row(total=-100.0, claim_id=9), row(total=-100.0, claim_id=9)])
    assert c["prejuizo"] == pytest.approx(-100.0)


def test_dois_claims_no_mesmo_pedido_contam_dinheiro_uma_vez():
    """`meli_page_saldos` é POR PEDIDO. Julho tem um pedido com dois claims
    fechados (2000017031981690, -144,15): somar por claim contaria o mesmo
    dinheiro duas vezes e inflaria o prejuízo do mês em R$ 144,15."""
    c = consolidar([row(total=-144.15, claim_id=1, order_id=777),
                    row(total=-144.15, claim_id=2, order_id=777)])
    assert c["prejuizo"] == pytest.approx(-144.15)


def test_dois_claims_no_mesmo_pedido_continuam_sendo_dois_casos():
    """O SAC atendeu duas vezes. Esconder isso apagaria trabalho feito — o
    que se conta uma vez é o dinheiro, não o atendimento."""
    c = consolidar([row(total=-144.15, claim_id=1, order_id=777),
                    row(total=-144.15, claim_id=2, order_id=777)])
    assert c["casos"] == 2


def test_pedidos_diferentes_com_mesmo_valor_somam_os_dois():
    """Guarda contra deduplicar por valor: dois pedidos distintos podem ter
    exatamente o mesmo prejuízo."""
    c = consolidar([row(total=-144.15, claim_id=1, order_id=777),
                    row(total=-144.15, claim_id=2, order_id=778)])
    assert c["prejuizo"] == pytest.approx(-288.30)


def test_mes_vazio_nao_explode():
    c = consolidar([])
    assert c["casos"] == 0 and c["prejuizo"] == 0.0


def test_divergencias_sao_contadas():
    linhas = _mes() + [row(total=-97.44, claim_id=6, produto=100.0,
                           envios=-148.05, canc=0.0, parc=0.0)]
    assert consolidar(linhas)["n_divergentes"] >= 1


# --- saída para conferência -----------------------------------------------

def test_linha_traz_o_que_o_chefe_pergunta():
    r = row(total=-284.74, produto=786.66, tarifa=-86.53, envios=-210.75,
            canc=-774.12, parc=0.0)
    l = linha_csv(r)
    assert str(r["order_id"]) in l
    assert "NR0001" in l
    assert "prejuizo" in l


def test_valor_em_formato_brasileiro():
    assert fmt_valor(-284.74) == "-284,74"


def test_valor_ausente_nao_vira_zero():
    assert fmt_valor(None) == ""
