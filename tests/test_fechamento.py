"""R7 -- fechamento diario: o placar do chefe.

Pedido do Gabriel: um canal de fechamento que diga, do dia, quanto fechou
NEGATIVO (prejuizo), ZERO (a Protecao ao Vendedor cobriu) e REVERTIDO (o ML
indenizou acima do custo), mais o SALDO do dia. Sem ruido operacional.
"""
import json

from slack_notify import classificar_desfecho, montar_fechamento


def _c(saldo, **over):
    base = {"order_id": 2000012345678, "item_title": "Motor X", "item_sku": "NR1", "saldo": saldo}
    base.update(over)
    return base


def _txt(blocks) -> str:
    return json.dumps(blocks, ensure_ascii=False)


# --- classificar_desfecho --------------------------------------------------

def test_saldo_negativo_e_prejuizo():
    assert classificar_desfecho(-120.5) == "negativo"


def test_saldo_zero_e_coberto():
    assert classificar_desfecho(0) == "zero"


def test_saldo_positivo_e_revertido():
    assert classificar_desfecho(397.48) == "revertido"


def test_saldo_ausente_fica_pendente_de_conciliacao():
    # R8: saldo zero != zerado. Sem conciliacao, nao afirmamos "ML cobriu".
    assert classificar_desfecho(None) == "pendente"


# --- montar_fechamento -----------------------------------------------------

def test_dia_sem_fechamento_diz_que_foi_zerado():
    texto, blocks = montar_fechamento([], "29/07/2026")
    corpo = _txt(blocks)
    assert "29/07/2026" in corpo
    assert "Nenhum processo" in corpo or "zerado" in corpo.lower()


def test_conta_as_tres_categorias():
    rows = [_c(-100.0), _c(-50.0), _c(0.0), _c(397.48)]
    _, blocks = montar_fechamento(rows, "29/07/2026")
    corpo = _txt(blocks)
    assert "Prejuízo" in corpo and "2" in corpo
    assert "ML cobriu" in corpo
    assert "Revertido" in corpo


def test_saldo_do_dia_e_a_soma():
    rows = [_c(-100.0), _c(-50.0), _c(0.0), _c(200.0)]
    _, blocks = montar_fechamento(rows, "29/07/2026")
    # -100 -50 +0 +200 = +50
    assert "50,00" in _txt(blocks)


def test_saldo_negativo_do_dia_aparece_com_sinal():
    rows = [_c(-300.0), _c(100.0)]
    _, blocks = montar_fechamento(rows, "29/07/2026")
    corpo = _txt(blocks)
    assert "200,00" in corpo
    assert "-" in corpo


def test_lista_os_prejuizos_com_produto_e_valor():
    rows = [_c(-1380.97, item_title="Placa Condensadora", item_sku="A125", order_id=999)]
    _, blocks = montar_fechamento(rows, "29/07/2026")
    corpo = _txt(blocks)
    assert "Placa Condensadora" in corpo
    assert "1.380,97" in corpo
    assert "vendas/999/detalhe" in corpo


def test_pendente_de_conciliacao_nao_entra_no_saldo():
    # sem saldo conciliado nao da para afirmar resultado -- nao pode
    # contaminar o numero que o chefe le
    rows = [_c(-100.0), _c(None)]
    _, blocks = montar_fechamento(rows, "29/07/2026")
    corpo = _txt(blocks)
    assert "100,00" in corpo
    assert "conciliação" in corpo.lower() or "pendente" in corpo.lower()


def test_fallback_de_texto_traz_o_saldo_para_a_notificacao_do_celular():
    texto, _ = montar_fechamento([_c(-100.0)], "29/07/2026")
    assert "29/07/2026" in texto
    assert "100,00" in texto


def test_fechamento_nao_usa_botao_interativo():
    _, blocks = montar_fechamento([_c(-100.0)], "29/07/2026")
    for b in blocks:
        assert b.get("type") != "actions"
        assert (b.get("accessory") or {}).get("type") != "button"


def test_prejuizos_vem_do_maior_para_o_menor():
    rows = [_c(-10.0, item_title="Pequeno"), _c(-900.0, item_title="Grande"), _c(-50.0, item_title="Medio")]
    _, blocks = montar_fechamento(rows, "29/07/2026")
    corpo = _txt(blocks)
    assert corpo.index("Grande") < corpo.index("Medio") < corpo.index("Pequeno")
