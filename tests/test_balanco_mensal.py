"""Balanço MENSAL para a diretoria — Canvas no #sac-fechamento.

Consolida o mês a partir do banco (mesma fonte do fechamento diário), nunca
somando o texto das mensagens já publicadas: mensagem é apresentação, banco é
fato. Se uma mensagem falhou ou foi editada, somar texto propaga o erro.

A regra que atravessa tudo aqui: **cobertura declarada**. Em julho/2026, 72
dos 292 casos fechados (25%) ainda não tinham saldo apurado. Publicar
"prejuízo do mês: R$ 4.508" sem dizer que um quarto da base está fora seria
apresentar um número incompleto como se fosse fechado.
"""
import pytest

from balanco_mensal import (
    montar_canvas_mensal,
    nome_do_mes,
    periodo_do_mes,
    resumir_mes,
    variacao,
)


def _caso(saldo=None, receita=100.0, reembolso=0.0):
    return {"saldo": saldo, "order_total": receita,
            "amount_refunded": reembolso}


# --- período ---------------------------------------------------------------

def test_periodo_cobre_o_mes_inteiro():
    ini, fim = periodo_do_mes(2026, 7)
    assert ini.day == 1 and ini.month == 7
    assert fim.month == 8 and fim.day == 1


def test_periodo_de_dezembro_vira_janeiro_do_ano_seguinte():
    ini, fim = periodo_do_mes(2026, 12)
    assert fim.year == 2027 and fim.month == 1


def test_nome_do_mes_em_portugues():
    assert nome_do_mes(2026, 7) == "julho/2026"


# --- consolidação ----------------------------------------------------------

def test_conta_por_desfecho():
    r = resumir_mes([_caso(-10), _caso(0), _caso(5), _caso(None)])
    assert r["negativos"] == 1
    assert r["zerados"] == 1
    assert r["revertidos"] == 1
    assert r["sem_saldo"] == 1


def test_soma_prejuizo_e_revertido_separados():
    r = resumir_mes([_caso(-10), _caso(-5), _caso(20)])
    assert r["prejuizo"] == pytest.approx(-15)
    assert r["revertido"] == pytest.approx(20)


def test_saldo_e_a_soma_dos_dois():
    r = resumir_mes([_caso(-10), _caso(30)])
    assert r["saldo"] == pytest.approx(20)


def test_receita_soma_o_valor_das_vendas():
    r = resumir_mes([_caso(receita=100), _caso(receita=250)])
    assert r["receita"] == pytest.approx(350)


def test_reembolso_do_ml_e_somado():
    r = resumir_mes([_caso(reembolso=40), _caso(reembolso=60)])
    assert r["reembolsado"] == pytest.approx(100)


def test_cobertura_declara_quanto_do_mes_tem_saldo():
    """25% sem saldo em julho: o numero e parcial e precisa dizer isso."""
    r = resumir_mes([_caso(-10), _caso(0), _caso(None), _caso(None)])
    assert r["cobertura_pct"] == pytest.approx(50.0)


def test_mes_vazio_nao_divide_por_zero():
    r = resumir_mes([])
    assert r["casos"] == 0 and r["cobertura_pct"] == 0.0


def test_caso_duplicado_conta_uma_vez():
    """O mesmo claim com duas linhas inflaria o prejuizo do chefe -- foi o
    defeito que ja apareceu no fechamento diario."""
    a = {"claim_id": 1, "saldo": -10, "order_total": 100}
    r = resumir_mes([a, dict(a)])
    assert r["casos"] == 1


# --- variação mês a mês ----------------------------------------------------

def test_variacao_percentual():
    assert variacao(120, 100) == pytest.approx(20.0)


def test_variacao_com_base_zero_e_none():
    assert variacao(50, 0) is None


def test_variacao_negativa():
    assert variacao(80, 100) == pytest.approx(-20.0)


# --- canvas ----------------------------------------------------------------

def _resumo(**over):
    base = {"casos": 292, "negativos": 51, "zerados": 104, "revertidos": 65,
            "sem_saldo": 72, "receita": 92582.71, "prejuizo": -4508.46,
            "revertido": 19937.29, "saldo": 15428.83, "reembolsado": 36790.14,
            "cobertura_pct": 75.3}
    base.update(over)
    return base


def test_canvas_tem_o_mes_no_titulo():
    md = montar_canvas_mensal("julho/2026", _resumo(), [])
    assert "julho/2026" in md


def test_canvas_mostra_os_kpis_pedidos():
    md = montar_canvas_mensal("julho/2026", _resumo(), [])
    for esperado in ("92.582,71", "4.508,46", "19.937,29", "36.790,14"):
        assert esperado in md


def test_canvas_declara_a_cobertura():
    """Sem isso, um numero parcial parece fechado."""
    md = montar_canvas_mensal("julho/2026", _resumo(), [])
    assert "75" in md and ("cobertura" in md.lower() or "apurad" in md.lower())


def test_canvas_avisa_quando_cobertura_e_baixa():
    md = montar_canvas_mensal("julho/2026", _resumo(cobertura_pct=40.0), [])
    assert "parcial" in md.lower() or "incompleto" in md.lower()


def test_canvas_mostra_historico_quando_existe():
    hist = [{"mes": "2026-06", "prejuizo": -3319.90, "casos": 205},
            {"mes": "2026-05", "prejuizo": -3443.04, "casos": 241}]
    md = montar_canvas_mensal("julho/2026", _resumo(), hist)
    assert "2026-06" in md and "3.319,90" in md


def test_canvas_sem_historico_nao_quebra():
    assert montar_canvas_mensal("julho/2026", _resumo(), [])


def test_canvas_de_mes_vazio_diz_isso():
    md = montar_canvas_mensal("agosto/2026", _resumo(casos=0), [])
    assert "nenhum" in md.lower() or "sem caso" in md.lower()


def test_canvas_nao_vaza_none():
    md = montar_canvas_mensal("julho/2026", _resumo(prejuizo=None,
                                                    receita=None), [])
    assert "None" not in md
