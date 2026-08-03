"""O balanço não pode dizer que devolução deu lucro.

O que a primeira versão ia publicar para a diretoria, em julho/2026:

    🟢 Saldo do mês: R$ 15.826,44

Vinha de `prejuizo + revertido` = −6.102,43 + 21.928,87. Mas medindo o que é
um `total > 0` na página do Mercado Livre (03/08/2026):

    pedido …126735890 | produto 1.380,97 | cancelamentos 0,00 | TOTAL +1.164,12
    pedido …208804292 | produto   952,80 | cancelamentos −857,52 | TOTAL −210,75

`total > 0` com `cancelamentos = 0` é a venda que FICOU DE PÉ: houve
reclamação, não houve devolução, o dinheiro entrou normalmente. Isso é
receita de venda, não ganho do SAC. Somar com o prejuízo e chamar de "saldo
do mês" diz ao chefe que a área de devoluções é lucrativa — e ele decide em
cima disso.

O número honesto é o PREJUÍZO: R$ 6.102,43 em 67 casos, com o contexto de
que 224 dos 292 (77%) não custaram nada.

Também travado aqui: cobertura de 291/292 = 99,66% não pode ser exibida como
"100%" enquanto falta um.
"""
import pytest

from balanco_mensal import (
    fmt_cobertura,
    montar_canvas_mensal,
    resumir_mes,
)

JULHO = {
    "casos": 292, "negativos": 67, "zerados": 147, "revertidos": 77,
    "sem_saldo": 1, "prejuizo": -6102.43, "revertido": 21928.87,
    "saldo": 15826.44, "receita": 92582.71, "reembolsado": 36790.14,
    "cobertura_pct": 99.7,
}


def _canvas(**mudancas):
    r = dict(JULHO)
    r.update(mudancas)
    return montar_canvas_mensal("julho/2026", r, [])


# --- o titular ------------------------------------------------------------

def test_o_numero_do_topo_e_o_prejuizo():
    txt = _canvas()
    topo = txt.split("##")[1]
    assert "6.102,43" in topo


def test_o_topo_nao_apresenta_o_mes_como_positivo():
    """15.826,44 no topo diria que devolução deu lucro."""
    topo = _canvas().split("##")[1]
    assert "15.826,44" not in topo
    assert "🟢" not in topo


def test_venda_que_ficou_de_pe_nao_e_chamada_de_revertida_a_favor():
    """`total > 0` com cancelamentos zero é receita normal de venda. Chamar
    de 'revertido a favor' sugere dinheiro ganho na disputa."""
    txt = _canvas()
    assert "revertido a favor" not in txt.lower()
    assert "sem devolução" in txt.lower() or "ficou de pé" in txt.lower()


def test_os_tres_desfechos_continuam_visiveis():
    """Corrigir o titular não pode apagar a informação."""
    txt = _canvas()
    assert "67" in txt and "147" in txt and "77" in txt


def test_mes_sem_prejuizo_nenhum_nao_finge_perda():
    txt = _canvas(negativos=0, prejuizo=0.0)
    assert "nenhum caso custou" in txt.lower() or "sem prejuízo" in txt.lower()


# --- cobertura ------------------------------------------------------------

def test_cobertura_incompleta_nunca_vira_cem_por_cento():
    """291 de 292 é 99,66%. Arredondar para 100% enquanto falta um é uma
    mentira pequena — e é sempre a pequena que alguém confere."""
    assert fmt_cobertura(99.66, faltando=1) != "100%"


def test_cobertura_realmente_completa_pode_dizer_cem():
    assert fmt_cobertura(100.0, faltando=0) == "100%"


def test_o_texto_nao_se_contradiz_sobre_o_que_falta():
    """A versão anterior escrevia '100% dos casos ... os outros 1 entram
    quando a conciliação fechar' — as duas frases na mesma linha."""
    txt = _canvas(cobertura_pct=99.66, sem_saldo=1)
    bloco = txt.lower().split("confiança do número")[1]
    assert not ("100%" in bloco and "outros 1" in bloco)


# --- histórico ------------------------------------------------------------

def test_historico_declara_a_cobertura_de_cada_mes():
    """Janeiro tinha 57% de cobertura e julho tem 99,7%. Comparar os dois
    como tendência lê artefato de coleta como piora do negócio."""
    hist = [{"mes": "2026-06", "casos": 205, "prejuizo": -3319.90,
             "cobertura_pct": 98.0},
            {"mes": "2026-01", "casos": 305, "prejuizo": -1522.14,
             "cobertura_pct": 57.0}]
    txt = montar_canvas_mensal("julho/2026", JULHO, hist)
    assert "57" in txt


def test_comparacao_com_mes_de_cobertura_baixa_vem_com_ressalva():
    hist = [{"mes": "2026-06", "casos": 205, "prejuizo": -3319.90,
             "cobertura_pct": 40.0}]
    txt = montar_canvas_mensal("julho/2026", JULHO, hist)
    assert "parcial" in txt.lower() or "incompleto" in txt.lower()


# --- resumo continua consistente -----------------------------------------

def test_resumir_mes_nao_deixou_de_separar_os_desfechos():
    casos = [{"claim_id": 1, "saldo": -100.0, "order_total": 500.0},
             {"claim_id": 2, "saldo": 300.0, "order_total": 300.0},
             {"claim_id": 3, "saldo": 0.0, "order_total": 200.0}]
    r = resumir_mes(casos)
    assert (r["negativos"], r["zerados"], r["revertidos"]) == (1, 1, 1)
    assert r["prejuizo"] == -100.0
