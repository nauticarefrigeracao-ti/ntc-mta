"""Testes das funcoes puras de slack_notify.py -- TDD.

Cada funcao aqui nao faz I/O (sem rede, sem banco) -- os cenarios usam dados
reais observados no Neon (categorias, campos de tracking, ausencia de prazo
oficial, saldo financeiro por pedido) como base para as asserções, nao
suposicoes sobre a documentacao oficial do Mercado Livre.
"""
from datetime import datetime, timedelta, timezone

from slack_notify import (
    bloco_financeiro,
    bloco_tracking,
    categorizar,
    chave_estado,
    deve_notificar,
    eh_atualizacao,
    montar_mensagem,
    prazo_estimado,
)


def _row(**over):
    base = {
        "claim_id": 111,
        "order_id": 2000012345678,
        "claim_type": "mediations",
        "claim_status": "opened",
        "claim_stage": "claim",
        "reason_label": "O comprador se arrependeu",
        "item_title": "Placa Condensadora Ar Electrolux",
        "item_sku": "A12538601",
        "order_total": 1380.97,
        "date_created": datetime.now(timezone.utc) - timedelta(hours=10),
        "return_id": None,
        "return_status": None,
        "return_tracking_status": None,
        "return_tracking_number": None,
        "tracking_number": None,
    }
    base.update(over)
    return base


# --- categorizar --------------------------------------------------------

def test_categoriza_reclamacao_direta():
    assert categorizar(_row(claim_type="mediations", claim_stage="claim")) == "Reclamação direta"


def test_categoriza_mediacao():
    assert categorizar(_row(claim_type="mediations", claim_stage="dispute")) == "Mediação do ML"


def test_categoriza_recontato():
    assert categorizar(_row(claim_type="mediations", claim_stage="recontact")) == "Recontato"


def test_categoriza_cancelamento_compra():
    assert "Cancelamento" in categorizar(_row(claim_type="cancel_purchase", claim_stage="none"))


def test_categoriza_cancelamento_venda():
    assert "Cancelamento" in categorizar(_row(claim_type="cancel_sale", claim_stage="none"))


def test_categoriza_devolucao():
    assert categorizar(_row(claim_type="returns", claim_stage="claim")) == "Devolução"


def test_categoriza_tipo_desconhecido_mostra_valor_raw():
    resultado = categorizar(_row(claim_type="algo_novo_da_api", claim_stage="x"))
    assert "algo_novo_da_api" in resultado


def test_categoriza_etapa_de_mediacao_desconhecida_mostra_valor_raw():
    resultado = categorizar(_row(claim_type="mediations", claim_stage="etapa_nova"))
    assert "etapa_nova" in resultado


# --- bloco_tracking ------------------------------------------------------

def test_tracking_ausente_quando_sem_return_id():
    assert bloco_tracking(_row(return_id=None)) is None


def test_tracking_presente_e_humanizado():
    linha = bloco_tracking(_row(return_id=987, return_tracking_status="shipped"))
    assert "Em transporte" in linha


def test_tracking_inclui_numero_quando_disponivel():
    linha = bloco_tracking(_row(return_id=987, return_tracking_status="delivered",
                                 return_tracking_number="BR123456789"))
    assert "BR123456789" in linha and "Entregue" in linha


def test_tracking_status_desconhecido_mostra_valor_raw_em_vez_de_inventar():
    linha = bloco_tracking(_row(return_id=987, return_tracking_status="algo_novo"))
    assert "algo_novo" in linha


# --- prazo_estimado -------------------------------------------------------

def test_prazo_ausente_quando_processo_fechado():
    assert prazo_estimado(_row(claim_status="closed")) is None


def test_prazo_estimado_para_reclamacao_direta_deixa_claro_que_e_estimativa():
    agora = datetime.now(timezone.utc)
    texto = prazo_estimado(_row(claim_status="opened", claim_stage="claim",
                                 date_created=agora - timedelta(hours=5)), agora)
    assert "estimado" in texto.lower() or "estimativa" in texto.lower()


def test_prazo_estourado_quando_passou_de_2_dias():
    agora = datetime.now(timezone.utc)
    texto = prazo_estimado(_row(claim_status="opened", claim_stage="claim",
                                 date_created=agora - timedelta(days=5)), agora)
    assert "ESTOURADO" in texto


def test_prazo_para_mediacao_explica_que_nao_ha_prazo_fixo_do_vendedor():
    texto = prazo_estimado(_row(claim_status="opened", claim_stage="dispute"))
    assert "não há prazo fixo" in texto.lower() or "sem prazo fixo" in texto.lower()


def test_prazo_para_recontato_pede_resposta_rapida():
    texto = prazo_estimado(_row(claim_status="opened", claim_stage="recontact"))
    assert texto is not None


# --- bloco_financeiro ------------------------------------------------------

def test_financeiro_mostra_valor_quando_disponivel():
    texto = bloco_financeiro(_row(order_total=1380.97), saldo=None)
    assert "1.380,97" in texto


def test_financeiro_avisa_quando_valor_ainda_nao_sincronizado():
    texto = bloco_financeiro(_row(order_total=0), saldo=None)
    assert "ainda não sincronizado" in texto


def test_financeiro_processo_aberto_nunca_afirma_desfecho():
    texto = bloco_financeiro(_row(claim_status="opened"), saldo=250.0)
    assert "em andamento" in texto.lower()
    assert "250" not in texto


def test_processo_fechado_positivo_explica_motivo():
    texto = bloco_financeiro(_row(claim_status="closed"), saldo=138.10)
    assert "indenizou" in texto.lower() or "creditou" in texto.lower()
    assert "138,10" in texto


def test_processo_fechado_zero_explica_protecao_ao_vendedor():
    texto = bloco_financeiro(_row(claim_status="closed"), saldo=0.0)
    assert "proteção ao vendedor" in texto.lower()


def test_processo_fechado_negativo_explica_prejuizo():
    texto = bloco_financeiro(_row(claim_status="closed"), saldo=-164.12)
    assert "prejuizo confirmado" in texto.lower().replace("í","i")
    assert "164,12" in texto


def test_processo_fechado_sem_saldo_ainda_diz_conciliacao_pendente():
    texto = bloco_financeiro(_row(claim_status="closed"), saldo=None)
    assert "conciliação financeira pendente" in texto.lower()


# --- estado / re-notificacao ------------------------------------------------

def test_chave_estado_combina_status_e_etapa():
    assert chave_estado(_row(claim_status="opened", claim_stage="claim")) == "opened:claim"


def test_deve_notificar_quando_chave_e_nova():
    assert deve_notificar(set(), "opened:claim") is True


def test_nao_deve_notificar_quando_chave_ja_existe():
    assert deve_notificar({"opened:claim"}, "opened:claim") is False


def test_deve_notificar_quando_muda_de_etapa():
    assert deve_notificar({"opened:claim"}, "opened:dispute") is True


def test_nao_eh_atualizacao_na_primeira_notificacao():
    assert eh_atualizacao(set()) is False


def test_eh_atualizacao_quando_ja_notificou_antes():
    assert eh_atualizacao({"opened:claim"}) is True


# --- montar_mensagem (integracao das funcoes puras) ------------------------

def test_montar_mensagem_novo_processo_tem_cabecalho_correto():
    texto = montar_mensagem(_row())
    assert "Novo processo" in texto


def test_montar_mensagem_atualizacao_tem_cabecalho_correto():
    texto = montar_mensagem(_row(), atualizacao=True)
    assert "Atualização de estado" in texto


def test_montar_mensagem_inclui_categoria_motivo_e_valor():
    texto = montar_mensagem(_row())
    assert "Reclamação direta" in texto
    assert "O comprador se arrependeu" in texto
    assert "1.380,97" in texto


def test_montar_mensagem_inclui_link_do_pedido():
    texto = montar_mensagem(_row(order_id=2000012345678))
    assert "2000012345678" in texto


def test_montar_mensagem_devolucao_fechada_inclui_tracking_e_saldo():
    row = _row(claim_type="returns", claim_stage="none", claim_status="closed",
               return_id=55, return_tracking_status="delivered",
               return_tracking_number="BR999")
    texto = montar_mensagem(row, saldo=-50.0)
    assert "Entregue" in texto
    assert "BR999" in texto
    assert "prejuizo confirmado" in texto.lower().replace("í","i")
