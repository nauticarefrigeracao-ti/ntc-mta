"""Canvas do Quadro do SAC -- o cockpit fixo da Maria.

Por que Canvas e nao mensagem
-----------------------------
Mensagem some no rolar do canal. Medido em 31/07: dos casos que a Maria ve,
apenas 8% exigem acao dela (3 de 40) -- e 76% das notificacoes de 7 dias eram
so informativas. Um mural trata tudo igual e afoga o que importa.

O Canvas fica fixo no topo do #sac, sempre no mesmo lugar, e e reescrito a
cada ciclo. Ela abre e ve "o que eu faco agora", nao "o que aconteceu".

As funcoes aqui sao puras: recebem as linhas e devolvem markdown. Nenhuma
chamada de rede nos testes.
"""
import pytest

from slack_notify import (
    montar_canvas_quadro,
    resumo_uma_linha,
)


def _row(**over):
    base = {"claim_status": "opened", "claim_stage": "claim",
            "order_id": 2000012345678901, "reason_label": "Produto não funciona",
            "item_title": "Motor Compressor Embraco 1/5", "item_sku": "NR1058-1",
            "order_total": 499.0}
    base.update(over)
    return base


# --- estrutura do canvas ---------------------------------------------------

def test_tem_as_tres_colunas():
    md = montar_canvas_quadro([_row()], "31/07")
    assert "A Fazer" in md
    assert "Aguardando" in md
    assert "Feito" in md


def test_a_fazer_lista_o_caso_com_sku_e_link():
    md = montar_canvas_quadro([_row(item_sku="NR5461", order_id=999)], "31/07")
    assert "NR5461" in md
    assert "mercadolivre.com.br/vendas/999/detalhe" in md


def test_a_fazer_mostra_o_valor_em_jogo():
    """O chefe pergunta 'quanto tem em risco' — o numero fica na cara."""
    md = montar_canvas_quadro([_row(order_total=499.0)], "31/07")
    assert "499,00" in md


def test_traduz_o_motivo():
    md = montar_canvas_quadro([_row(reason_label="PDD9952")], "31/07")
    assert "PDD9952" not in md
    assert "Problema com o produto" in md


# --- o que separa cockpit de mural -----------------------------------------

def test_aguardando_nao_lista_caso_a_caso():
    """37 casos em disputa viram CONTADOR, nao 37 linhas -- senao o Canvas
    vira o mesmo mural que ele veio substituir."""
    rows = [_row(claim_stage="dispute") for _ in range(37)]
    md = montar_canvas_quadro(rows, "31/07")
    assert "37" in md
    assert md.count("mercadolivre.com.br") <= 1


def test_sem_nada_a_fazer_diz_isso_com_todas_as_letras():
    md = montar_canvas_quadro([_row(claim_stage="dispute")], "31/07")
    assert "A Fazer" in md
    assert "0" in md
    assert "nada" in md.lower() or "livre" in md.lower()


def test_ordena_a_fazer_do_mais_antigo_para_o_mais_novo():
    """O que espera ha mais tempo corre mais risco de estourar prazo."""
    rows = [
        _row(order_id=111, date_created="2026-07-30T10:00:00-03:00"),
        _row(order_id=222, date_created="2026-07-25T10:00:00-03:00"),
    ]
    md = montar_canvas_quadro(rows, "31/07")
    assert md.index("222") < md.index("111")


def test_caso_sem_data_nao_quebra_a_ordenacao():
    rows = [_row(order_id=111, date_created=None), _row(order_id=222)]
    md = montar_canvas_quadro(rows, "31/07")
    assert "111" in md and "222" in md


# --- feito / saldo ---------------------------------------------------------

def test_feito_conta_os_encerrados():
    rows = [_row(claim_status="closed"), _row(claim_status="closed")]
    md = montar_canvas_quadro(rows, "31/07")
    assert "2" in md


def test_saldo_do_dia_aparece_quando_informado():
    md = montar_canvas_quadro([_row()], "31/07", saldo_dia=-1234.56)
    assert "1.234,56" in md


def test_sem_saldo_nao_inventa_numero():
    md = montar_canvas_quadro([_row()], "31/07")
    assert "R$ 0,00" not in md


# --- resumo de uma linha (para o topo do canal) ----------------------------

def test_resumo_diz_quantos_pedem_acao():
    assert "1" in resumo_uma_linha([_row()])


def test_resumo_de_lista_vazia_nao_quebra():
    assert resumo_uma_linha([])


# --- produto ja chegou (item 3 do roadmap de agosto) ----------------------
# Medido em 31/07: 7 casos abertos cujo produto o ML ja marcou como entregue
# (R$ 847,83). Tres deles em `claim` -- a Maria pode fechar hoje. Sem essa
# marca, ela nao tem como saber que o pacote esta na mao dela.

def test_produto_entregue_ganha_marca():
    md = montar_canvas_quadro(
        [_row(tracking_status="delivered")], "31/07")
    assert "chegou" in md.lower()


def test_produto_a_caminho_nao_ganha_marca():
    md = montar_canvas_quadro([_row(tracking_status="shipped")], "31/07")
    assert "chegou" not in md.lower()


def test_sem_rastreio_nao_ganha_marca():
    md = montar_canvas_quadro([_row(tracking_status=None)], "31/07")
    assert "chegou" not in md.lower()


def test_produto_que_chegou_vem_antes_de_caso_sem_urgencia():
    """Produto na mao fecha o caso E o dinheiro -- vem antes de um caso com
    prazo ainda folgado.

    Ordem completa (ver _ordem_por_idade): prazo vencido > prazo apertado >
    produto no galpão > mais antigo. D4 colocou prazo acima de tudo, porque
    prazo vencido já está custando reputação ou dinheiro parado.
    """
    from datetime import datetime, timedelta, timezone
    agora = datetime.now(timezone.utc)
    rows = [
        # aberto há 1h: prazo folgado, sem produto
        _row(order_id=111, tracking_status=None,
             date_created=(agora - timedelta(hours=1)).isoformat()),
        # aberto há 2h: prazo folgado também, mas o produto já chegou
        _row(order_id=222, tracking_status="delivered",
             date_created=(agora - timedelta(hours=2)).isoformat()),
    ]
    md = montar_canvas_quadro(rows, "31/07")
    assert md.index("222") < md.index("111")


def test_prazo_vencido_vem_antes_de_produto_que_chegou():
    """D4: o que já venceu custa agora; o produto no galpão pode esperar
    algumas horas a mais."""
    from datetime import datetime, timedelta, timezone
    agora = datetime.now(timezone.utc)
    rows = [
        _row(order_id=333, tracking_status="delivered",
             date_created=(agora - timedelta(hours=2)).isoformat()),
        _row(order_id=444, tracking_status=None,
             date_created=(agora - timedelta(hours=100)).isoformat()),
    ]
    md = montar_canvas_quadro(rows, "31/07")
    assert md.index("444") < md.index("333")


def test_marca_aparece_tambem_em_disputa_no_contador():
    """Caso em disputa com produto entregue continua fora de A Fazer, mas o
    Aguardando precisa dizer quantos ja tem o produto aqui."""
    rows = [_row(claim_stage="dispute", tracking_status="delivered")]
    md = montar_canvas_quadro(rows, "31/07")
    assert "Aguardando" in md
    assert "1" in md


def test_markdown_valido_sem_none():
    """None vazando para o markdown vira 'None' na tela da Maria."""
    md = montar_canvas_quadro(
        [_row(item_title=None, item_sku=None, reason_label=None,
              order_total=None)], "31/07")
    assert "None" not in md
