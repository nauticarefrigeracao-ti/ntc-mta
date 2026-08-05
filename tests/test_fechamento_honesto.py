"""Dois números que a diretoria leria errado na reunião de 05/08/2026.

**1. "Fechamento de agosto" no dia 5.**
`meses_a_publicar` incluía o mês corrente. Em 05/08 o canal #sac-fechamento
tinha DOIS Canvas mensais: julho e agosto. Agosto com 5 dias corridos não é
fechamento de mês -- o fechamento diário do dia anterior o próprio canal já
faz. Para quem abre o canal, dois balanços mensais lado a lado levantam a
pergunta que derruba o resto: "então qual dos dois vale?".

**2. "saldo R$ 425,35" com 0 casos de prejuízo.**
O `saldo_dia` soma prejuízo com revertido. Revertido é *receita de venda que
ficou de pé* -- houve reclamação, não houve devolução. Somar os dois produz
manchete positiva num painel cujo assunto é perda: em 31/07 o canal publicou
"saldo R$ 425,35 — 0 com prejuízo", que o chefe lê como "devolução deu lucro".

O mesmo defeito já tinha sido corrigido no Canvas mensal em 03/08 e sobreviveu
aqui, na mensagem diária, que é a que a diretoria lê todo dia.
"""
from datetime import datetime, timezone

import pytest

from balanco_mensal import meses_a_publicar
from slack_notify import montar_fechamento

UTC = timezone.utc


def caso(saldo, oid="2000017000000001", claim="1"):
    return {"claim_id": claim, "order_id": oid, "saldo": saldo,
            "item_title": "Sensor", "item_sku": "NR0001"}


# --- 1. mês corrente não é fechamento -------------------------------------

def test_nao_publica_o_mes_corrente():
    """Em 05/08 o mês de agosto tem 5 dias. Não há fechamento a fechar."""
    assert (2026, 8) not in meses_a_publicar(datetime(2026, 8, 5, tzinfo=UTC))


def test_publica_o_mes_que_acabou():
    assert (2026, 7) in meses_a_publicar(datetime(2026, 8, 5, tzinfo=UTC))


def test_reabre_meses_anteriores():
    """O saldo do ML chega depois do caso encerrar: em 03/08 julho ainda
    tinha 25% dos casos sem saldo. Publicar uma vez só congelaria o mês num
    número parcial."""
    m = meses_a_publicar(datetime(2026, 8, 5, tzinfo=UTC), quantos=2)
    assert m == [(2026, 7), (2026, 6)]


def test_vira_o_ano_sem_mes_zero():
    m = meses_a_publicar(datetime(2026, 1, 10, tzinfo=UTC), quantos=2)
    assert m == [(2025, 12), (2025, 11)]


def test_primeiro_dia_do_mes_publica_o_anterior():
    """01/08 é o dia certo para o fechamento de julho."""
    assert meses_a_publicar(datetime(2026, 8, 1, tzinfo=UTC))[0] == (2026, 7)


def test_ultimo_dia_do_mes_ainda_nao_fecha_o_proprio_mes():
    """31/07 23h ainda pode receber caso. Fechar ali publicaria número que
    muda depois -- e o chefe leria como final."""
    assert (2026, 7) not in meses_a_publicar(datetime(2026, 7, 31, tzinfo=UTC))


def test_quantos_zero_falha_alto():
    with pytest.raises(ValueError):
        meses_a_publicar(datetime(2026, 8, 5, tzinfo=UTC), quantos=0)


# --- 2. manchete do dia é prejuízo, não "saldo" ---------------------------

def test_manchete_do_dia_nao_soma_receita_com_prejuizo():
    """425,35 de venda que ficou de pé + 0 de prejuízo não é 'saldo 425,35'
    num painel de perdas."""
    texto, _ = montar_fechamento([caso(425.35, claim="a")], "31/07/2026")
    assert "saldo R$ 425,35" not in texto


def test_manchete_do_dia_mostra_o_prejuizo():
    texto, _ = montar_fechamento(
        [caso(-77.25, claim="a"), caso(425.35, claim="b")], "04/08/2026")
    assert "77,25" in texto


def test_dia_sem_prejuizo_diz_isso_com_todas_as_letras():
    texto, _ = montar_fechamento([caso(425.35, claim="a")], "31/07/2026")
    assert "sem prejuízo" in texto.lower()


def test_receita_revertida_continua_visivel():
    """Reverter venda é o trabalho do SAC: sumir com o número apagaria o
    resultado da Thayná e da equipe."""
    _, blocks = montar_fechamento([caso(425.35, claim="a")], "31/07/2026")
    corpo = str(blocks)
    assert "425,35" in corpo
    assert "evert" in corpo  # "Revertido"


def test_bloco_do_topo_nao_chama_receita_de_saldo():
    _, blocks = montar_fechamento([caso(425.35, claim="a")], "31/07/2026")
    topo = str(blocks[1])
    assert "Saldo do dia" not in topo


def test_prejuizo_do_dia_continua_negativo_no_texto():
    texto, _ = montar_fechamento([caso(-77.25, claim="a")], "04/08/2026")
    assert "-" in texto or "−" in texto


def test_dia_vazio_nao_muda():
    texto, _ = montar_fechamento([], "02/08/2026")
    assert "nenhum processo" in texto.lower()


def test_duplicata_de_claim_nao_infla():
    """Um claim tem várias chaves de estado; o JOIN devolve uma linha por
    chave. Sem dedup o prejuízo do chefe sai dobrado."""
    texto, _ = montar_fechamento(
        [caso(-50.0, claim="x"), caso(-50.0, claim="x")], "04/08/2026")
    assert "100,00" not in texto
