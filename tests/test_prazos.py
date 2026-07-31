"""D4 -- prazo por etapa, com alerta quando estoura.

Cada etapa tem um relogio diferente, e tratar todas igual esconde o que
corre risco:

  claim / recontact -> a bola esta com a gente. ~2 dias corridos para
      responder no ML; passar disso penaliza reputacao.
  produto entregue  -> o pacote ja esta no galpao. 48h para fechar o caso,
      senao fica dinheiro parado (medido em 31/07: 7 casos, R$ 847,83).
  dispute           -> quem decide e o Mercado Livre. NAO existe prazo nosso,
      e inventar um seria mentir para a Maria.

As funcoes sao puras: recebem a linha e o "agora", devolvem estado. Nenhuma
depende do relogio real, entao o teste nao fica instavel.
"""
from datetime import datetime, timedelta, timezone

import pytest

from slack_notify import (
    HORAS_FECHAR_APOS_ENTREGA,
    HORAS_RESPONDER_CLAIM,
    horas_restantes,
    situacao_prazo,
    texto_prazo_curto,
)

AGORA = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def _row(horas_atras=1, **over):
    base = {
        "claim_status": "opened",
        "claim_stage": "claim",
        "date_created": (AGORA - timedelta(hours=horas_atras)).isoformat(),
        "tracking_status": None,
    }
    base.update(over)
    return base


# --- claim / recontact: 2 dias para responder ------------------------------

def test_claim_recem_aberto_esta_ok():
    assert situacao_prazo(_row(horas_atras=2), AGORA) == "ok"


def test_claim_perto_do_limite_fica_apertado():
    # 40h de 48h -> menos de 12h restantes
    assert situacao_prazo(_row(horas_atras=40), AGORA) == "apertado"


def test_claim_passou_de_dois_dias_estourou():
    assert situacao_prazo(_row(horas_atras=60), AGORA) == "estourado"


def test_recontato_usa_o_mesmo_relogio():
    assert situacao_prazo(_row(horas_atras=60, claim_stage="recontact"),
                          AGORA) == "estourado"


# --- disputa: nao existe prazo nosso ---------------------------------------

def test_disputa_nao_tem_prazo_nosso():
    """Inventar prazo onde quem decide e o ML seria mentir para a Maria."""
    assert situacao_prazo(_row(horas_atras=500, claim_stage="dispute"),
                          AGORA) == "sem_prazo"


def test_caso_fechado_nao_tem_prazo():
    assert situacao_prazo(_row(claim_status="closed"), AGORA) == "sem_prazo"


# --- produto entregue: 48h para fechar -------------------------------------

def test_produto_entregue_recente_esta_ok():
    r = _row(horas_atras=2, claim_stage="dispute", tracking_status="delivered")
    assert situacao_prazo(r, AGORA) == "ok"


def test_produto_entregue_ha_mais_de_48h_estourou():
    """Produto no galpao e caso aberto = dinheiro parado."""
    r = _row(horas_atras=72, claim_stage="dispute", tracking_status="delivered")
    assert situacao_prazo(r, AGORA) == "estourado"


def test_produto_entregue_vence_a_regra_da_disputa():
    """Mesmo sem prazo do ML, ter o produto na mao cria prazo nosso."""
    r = _row(horas_atras=72, claim_stage="dispute", tracking_status="delivered")
    assert situacao_prazo(r, AGORA) != "sem_prazo"


# --- robustez --------------------------------------------------------------

def test_sem_data_de_abertura_nao_quebra():
    assert situacao_prazo(_row(date_created=None), AGORA) in (
        "ok", "apertado", "estourado", "sem_prazo")


def test_data_invalida_nao_quebra():
    assert situacao_prazo(_row(date_created="não é data"), AGORA) is not None


def test_data_sem_fuso_e_tratada_como_utc():
    r = _row(date_created="2026-07-31T10:00:00")
    assert situacao_prazo(r, AGORA) == "ok"


# --- horas restantes -------------------------------------------------------

def test_horas_restantes_conta_para_baixo():
    assert horas_restantes(_row(horas_atras=10), AGORA) == pytest.approx(38, abs=1)


def test_horas_restantes_fica_negativo_quando_estoura():
    assert horas_restantes(_row(horas_atras=60), AGORA) < 0


def test_sem_prazo_devolve_none():
    assert horas_restantes(_row(claim_stage="dispute"), AGORA) is None


# --- texto para o Canvas ---------------------------------------------------

# --- legibilidade do atraso ------------------------------------------------
# A primeira versao imprimiu "prazo venceu ha 14180h" -- 1,6 ano em horas.
# Numero ilegivel nao informa: assusta e e ignorado.

def test_atraso_de_horas_aparece_em_horas():
    t = texto_prazo_curto(_row(horas_atras=60), AGORA)
    assert "12h" in t


def test_atraso_de_dias_aparece_em_dias():
    t = texto_prazo_curto(_row(horas_atras=48 + 72), AGORA)
    assert "3 dias" in t
    # não pode sobrar a contagem em horas (72h) junto — "há" é palavra, não unidade
    assert "72h" not in t and "120h" not in t


def test_atraso_de_meses_aparece_em_meses():
    t = texto_prazo_curto(_row(horas_atras=48 + 24 * 90), AGORA)
    assert "meses" in t


def test_atraso_gigante_nao_vira_numero_absurdo():
    """14180h nao diz nada a ninguem."""
    t = texto_prazo_curto(_row(horas_atras=48 + 24 * 590), AGORA)
    assert "14" not in t or "meses" in t
    assert len(t) < 90


def test_texto_de_estourado_e_inequivoco():
    t = texto_prazo_curto(_row(horas_atras=60), AGORA)
    assert "atras" in t.lower() or "venceu" in t.lower() or "estour" in t.lower()


def test_texto_apertado_diz_quanto_falta():
    t = texto_prazo_curto(_row(horas_atras=40), AGORA)
    assert "h" in t


def test_texto_sem_prazo_e_vazio():
    """Sem prazo nao inventa marca -- o quadro ja diz que o ML esta decidindo."""
    assert texto_prazo_curto(_row(claim_stage="dispute"), AGORA) == ""


def test_constantes_declaradas():
    assert HORAS_RESPONDER_CLAIM == 48
    assert HORAS_FECHAR_APOS_ENTREGA == 48
