"""Telemetria de tempo -- quanto demora cada etapa e cada passagem de bastão.

Objetivo declarado pelo negócio: sair de "acho que demora" para número. É a
base da medição por setor que vem depois.

As funções são puras: recebem os registros e devolvem as métricas. Nenhuma
toca o relógio real, então o teste não fica instável.
"""
from datetime import datetime, timedelta, timezone

import pytest

from telemetria import (
    duracao_dias,
    horas_entre,
    percentil,
    resumo_etapas,
    tempo_de_reacao,
)

AGORA = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


# --- duração ---------------------------------------------------------------

def test_duracao_em_dias():
    ini = AGORA - timedelta(days=3)
    assert duracao_dias(ini, AGORA) == pytest.approx(3.0)


def test_duracao_aceita_texto_iso():
    assert duracao_dias("2026-07-28T12:00:00+00:00", AGORA) == pytest.approx(3.0)


def test_duracao_sem_fuso_assume_utc():
    assert duracao_dias("2026-07-28T12:00:00", AGORA) == pytest.approx(3.0)


def test_duracao_com_data_invalida_e_none():
    assert duracao_dias("não é data", AGORA) is None


def test_duracao_negativa_e_none():
    """Fim antes do início é dado corrompido, não duração de -3 dias."""
    assert duracao_dias(AGORA, AGORA - timedelta(days=3)) is None


def test_horas_entre():
    assert horas_entre(AGORA - timedelta(hours=5), AGORA) == pytest.approx(5.0)


# --- percentil -------------------------------------------------------------

def test_percentil_mediana():
    assert percentil([1, 2, 3, 4, 5], 50) == 3


def test_percentil_p90_pega_a_cauda():
    """A média esconde o caso que demorou muito; o p90 é quem denuncia."""
    assert percentil([1, 1, 1, 1, 100], 90) >= 1


def test_percentil_de_lista_vazia_e_none():
    assert percentil([], 50) is None


def test_percentil_de_um_elemento():
    assert percentil([7], 50) == 7


# --- resumo por etapa ------------------------------------------------------

def _caso(stage, dias, status="closed"):
    return {"claim_stage": stage, "claim_status": status,
            "date_created": (AGORA - timedelta(days=dias)).isoformat(),
            "date_updated": AGORA.isoformat()}


def test_resumo_agrupa_por_etapa():
    r = resumo_etapas([_caso("claim", 2), _caso("claim", 4),
                       _caso("dispute", 30)])
    assert r["claim"]["casos"] == 2
    assert r["dispute"]["casos"] == 1


def test_resumo_traz_media_e_p90():
    r = resumo_etapas([_caso("claim", 2), _caso("claim", 4)])
    assert r["claim"]["media_dias"] == pytest.approx(3.0, abs=0.1)
    assert r["claim"]["p90_dias"] is not None


def test_resumo_ignora_caso_sem_data_valida():
    caso = _caso("claim", 2)
    caso["date_created"] = None
    r = resumo_etapas([caso, _caso("claim", 4)])
    assert r["claim"]["casos"] == 1


def test_resumo_vazio_nao_quebra():
    assert resumo_etapas([]) == {}


def test_resumo_so_conta_caso_fechado():
    """Caso aberto ainda está correndo — incluir baixaria a média de mentira."""
    r = resumo_etapas([_caso("claim", 2, status="opened"),
                       _caso("claim", 4)])
    assert r["claim"]["casos"] == 1


# --- tempo de reação (handoff) ---------------------------------------------

def test_tempo_de_reacao_mede_abertura_ate_aviso():
    pares = [{"abriu": AGORA - timedelta(hours=3), "avisou": AGORA}]
    r = tempo_de_reacao(pares)
    assert r["media_horas"] == pytest.approx(3.0)


def test_tempo_de_reacao_descarta_aviso_antes_da_abertura():
    """Avisar antes de existir é dado corrompido, não reação instantânea."""
    pares = [{"abriu": AGORA, "avisou": AGORA - timedelta(hours=3)},
             {"abriu": AGORA - timedelta(hours=2), "avisou": AGORA}]
    r = tempo_de_reacao(pares)
    assert r["casos"] == 1


def test_tempo_de_reacao_vazio_nao_divide_por_zero():
    r = tempo_de_reacao([])
    assert r["casos"] == 0 and r["media_horas"] is None
