"""O balanço mensal não pode depender de alguém lembrar de rodar.

O que o chefe pediu é um balanço que esteja lá todo mês. Publicar uma vez, no
dia 1, tem um defeito conhecido: a apuração de saldo do Mercado Livre chega
DEPOIS do fechamento do caso. Em julho/2026, 72 dos 292 casos encerrados
(25%) ainda não tinham saldo no dia 3 de agosto — um Canvas publicado no dia
1 congelaria o mês num número parcial e errado.

Por isso a publicação é diária e idempotente: o Canvas do mês é um só
(chaveado por `canal:AAAA-MM`) e vai sendo corrigido conforme os saldos
entram. Quem abrir vê sempre o melhor número disponível, com a cobertura
declarada em cima.

`meses_a_publicar` decide quais meses reabrir a cada rodada.
"""
from datetime import datetime, timezone

import pytest

from balanco_mensal import meses_a_publicar


def _em(ano, mes, dia=15):
    return datetime(ano, mes, dia, tzinfo=timezone.utc)


def test_publica_o_mes_corrente_e_o_anterior():
    """O corrente porque o chefe quer ver o mês andando; o anterior porque é
    justamente ele que ainda está recebendo saldo atrasado."""
    assert meses_a_publicar(_em(2026, 8), quantos=2) == [(2026, 8), (2026, 7)]


def test_atravessa_a_virada_do_ano():
    """Janeiro tem que reabrir dezembro do ano anterior, não o mês 0."""
    assert meses_a_publicar(_em(2026, 1), quantos=2) == [(2026, 1), (2025, 12)]


def test_tres_meses_para_tras():
    assert meses_a_publicar(_em(2026, 2), quantos=3) == [
        (2026, 2), (2026, 1), (2025, 12)]


def test_um_mes_so_e_o_corrente():
    assert meses_a_publicar(_em(2026, 8), quantos=1) == [(2026, 8)]


def test_quantos_invalido_e_erro_e_nao_lista_vazia():
    """Rodada que publica zero meses sairia verde sem fazer nada — o tipo de
    silêncio que já escondeu job quebrado por semanas aqui."""
    with pytest.raises(ValueError):
        meses_a_publicar(_em(2026, 8), quantos=0)


def test_a_ordem_e_do_mais_recente_para_o_mais_antigo():
    """Se a rodada falhar no meio, o mês que o chefe abre primeiro já foi."""
    meses = meses_a_publicar(_em(2026, 8), quantos=4)
    assert meses == sorted(meses, reverse=True)
