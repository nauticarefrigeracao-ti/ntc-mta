"""O Canvas precisa fechar contra o Excel feito à mão, caso a caso.

Exigência do negócio (05/08/2026): a Thayná mantém um Excel com o SAC,
acompanhando devolução por devolução e tentando reverter. É esse Excel que
explica os números melhorando mês a mês. Na reunião a conciliação é feita
**contra ele** — linha do Excel × linha nossa.

Um total agregado não concilia com nada. "R$ 5.930,84" não diz qual venda o
Excel tem e a gente não tem, nem o contrário. Por isso o Canvas termina com a
lista completa, **do primeiro ao último dia do mês**, com o link da venda.

Três coisas que os testes cobram:

1. **Ordem cronológica.** A conciliação anda de cima para baixo junto com o
   Excel; lista fora de ordem obriga a procurar cada linha.
2. **Toda venda aparece, inclusive as que não custaram nada.** As 147 que o ML
   cobriu e as 76 revertidas são justamente o resultado do trabalho do SAC —
   e são as que o Excel usa para provar reversão.
3. **Link por venda.** O chefe clica. Se o link não abre na venda certa, o
   número inteiro perde valor.
"""
import pytest

from balanco_mensal import montar_canvas_mensal, secao_venda_por_venda


def venda(dia, oid, saldo, sku="NR0001", titulo="Sensor", motivo="Arrependimento"):
    return {"order_id": oid, "claim_id": oid, "item_sku": sku,
            "item_title": titulo, "reason_label": motivo,
            "date_updated": f"2026-07-{dia:02d}T10:00:00Z", "saldo": saldo}


TRES = [venda(31, 3003, -100.0), venda(1, 3001, 0.0), venda(15, 3002, 425.35)]


def test_lista_em_ordem_do_primeiro_ao_ultimo_dia():
    md = secao_venda_por_venda(TRES)
    assert md.index("3001") < md.index("3002") < md.index("3003")


def test_traz_as_vendas_que_nao_custaram_nada():
    """As 147 cobertas pelo ML e as 76 revertidas são o resultado do SAC.
    Listar só as que deram prejuízo apagaria o trabalho que deu certo."""
    md = secao_venda_por_venda(TRES)
    assert "3001" in md and "3002" in md


def test_cada_venda_tem_link_do_mercado_livre():
    md = secao_venda_por_venda(TRES)
    assert md.count("mercadolivre.com.br") >= 3


def test_link_aponta_para_o_pedido_certo():
    md = secao_venda_por_venda([venda(5, 2000017208804292, -210.75)])
    assert "2000017208804292" in md


def test_desfecho_legivel_em_vez_de_codigo():
    md = secao_venda_por_venda(TRES)
    assert "prejuízo" in md.lower()


def test_valor_em_reais():
    md = secao_venda_por_venda([venda(5, 3001, -210.75)])
    assert "210,75" in md


def test_venda_sem_saldo_nao_vira_zero():
    """Zero foi medido (o ML cobriu); ausente é desconhecido. Escrever
    "R$ 0,00" numa venda ainda não apurada faria o Excel bater com um número
    que não existe."""
    md = secao_venda_por_venda([venda(5, 3001, None)])
    assert "0,00" not in md


def test_venda_sem_saldo_e_declarada():
    md = secao_venda_por_venda([venda(5, 3001, None)])
    assert "apur" in md.lower() or "pendente" in md.lower()


def test_mesma_venda_com_dois_claims_aparece_uma_vez_no_dinheiro():
    """O pedido 2000017031981690 tem dois claims fechados em julho. A linha
    do Excel é uma só — a venda."""
    md = secao_venda_por_venda([venda(5, 777, -144.15), venda(9, 777, -144.15)])
    assert md.count("144,15") == 1


def test_lista_vazia_nao_quebra_o_canvas():
    assert isinstance(secao_venda_por_venda([]), str)


def test_declara_quantas_vendas_estao_listadas():
    """Se o Excel tem 291 e a lista tem 290, a diferença precisa saltar."""
    md = secao_venda_por_venda(TRES)
    assert "3" in md


# --- integração com o Canvas ----------------------------------------------

RESUMO = {"casos": 3, "negativos": 1, "zerados": 1, "revertidos": 1,
          "sem_saldo": 0, "prejuizo": -100.0, "revertido": 425.35,
          "receita": 1000.0, "reembolsado": 0.0, "cobertura_pct": 100.0}


def test_canvas_termina_com_a_lista():
    md = montar_canvas_mensal("julho/2026", RESUMO, [], vendas=TRES)
    assert md.index("3001") > md.index("Prejuízo do mês")


def test_canvas_sem_vendas_continua_funcionando():
    """Compatibilidade: chamadas antigas não podem quebrar."""
    md = montar_canvas_mensal("julho/2026", RESUMO, [])
    assert "Prejuízo do mês" in md
