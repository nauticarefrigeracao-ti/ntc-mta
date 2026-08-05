"""25 mensagens no #sac com link que dá 404 na cara do chefe.

Conferência mensagem por mensagem contra a API do ML, 05/08/2026, paginada
(448 mensagens, 172 pedidos citados). 25 links não abrem. Causa medida:

    link publicado   /vendas/47386687921/detalhe   -> 404
    pedido real                2000017121756758

`47386687921` é o **shipment_id**, não o pedido. Os cinco primeiros conferidos
batem um a um com `ml_devolucoes.shipment_id`. O banco já tem o `order_id`
certo hoje — o erro ficou congelado no texto que foi publicado em julho.

Isso também explica as "26 lacunas" que a auditoria vinha acusando: as
mensagens existem, mas citam um número que não é o pedido, então nem a
auditoria nem a Maria conseguem ligar a mensagem ao caso.

Duas coisas que os testes protegem:

1. **Reescrever só o que está errado.** Trocar o número de uma mensagem certa
   seria pior que o defeito — a diretoria confere o histórico.
2. **Não inventar pedido.** Shipment sem pedido conhecido fica como está, com
   o problema declarado. Chutar um order_id põe link errado no lugar de link
   quebrado, e link errado ninguém percebe.
"""
import pytest

from reparar_links import (
    LINKS,
    extrair_shipments,
    precisa_reparo,
    reescrever_link,
)

MAPA = {"47386687921": "2000017121756758",
        "47500233824": "2000017364203014"}

MSG_QUEBRADA = (
    ":rotating_light: *Novo processo* — Cancelamento (arrependimento do comprador)\n"
    "*Produto* (SKU —)\n"
    "Motivo: _PNR9508_\n"
    ":arrow_right: <https://www.mercadolivre.com.br/vendas/47386687921/detalhe"
    "|Pedido 47386687921 — abrir a venda>"
)

MSG_BOA = (
    ":arrow_right: <https://www.mercadolivre.com.br/vendas/2000017121756758/detalhe"
    "|Abrir a venda 2000017121756758 no Mercado Livre>"
)


# --- detectar --------------------------------------------------------------

def test_id_de_11_digitos_precisa_de_reparo():
    assert precisa_reparo(MSG_QUEBRADA)


def test_pedido_de_16_digitos_nao_precisa():
    assert not precisa_reparo(MSG_BOA)


def test_pedido_antigo_de_10_digitos_nao_precisa():
    """Pedido antigo legítimo tem 10 dígitos. Presumir "id curto = shipment"
    já custou uma invariante que acusou 5.099 pedidos válidos — não se repete
    o erro na direção contrária."""
    msg = "<https://www.mercadolivre.com.br/vendas/2000010128/detalhe|x>"
    assert not precisa_reparo(msg)


def test_mensagem_sem_link_nao_precisa():
    assert not precisa_reparo("Fechamento 04/08/2026: sem prejuízo")


def test_extrai_o_shipment():
    assert extrair_shipments(MSG_QUEBRADA) == ["47386687921"]


def test_extrai_varios_sem_repetir():
    msg = MSG_QUEBRADA + "\n" + MSG_QUEBRADA
    assert extrair_shipments(msg) == ["47386687921"]


# --- reescrever ------------------------------------------------------------

def test_troca_o_shipment_pelo_pedido():
    novo = reescrever_link(MSG_QUEBRADA, MAPA)
    assert "2000017121756758" in novo
    assert "47386687921" not in novo


def test_troca_tambem_no_texto_visivel_do_link():
    """O rótulo dizia "Pedido 47386687921". Trocar só a URL deixaria a
    mensagem contradizendo o próprio link."""
    novo = reescrever_link(MSG_QUEBRADA, MAPA)
    assert "Pedido 47386687921" not in novo


def test_preserva_o_resto_da_mensagem():
    novo = reescrever_link(MSG_QUEBRADA, MAPA)
    assert "Cancelamento (arrependimento do comprador)" in novo
    assert "PNR9508" in novo


def test_shipment_desconhecido_nao_vira_chute():
    """Link errado é pior que link quebrado: ninguém percebe."""
    novo = reescrever_link(MSG_QUEBRADA, {})
    assert novo == MSG_QUEBRADA


def test_mensagem_certa_nao_e_tocada():
    assert reescrever_link(MSG_BOA, MAPA) == MSG_BOA


def test_reescrita_e_idempotente():
    uma = reescrever_link(MSG_QUEBRADA, MAPA)
    assert reescrever_link(uma, MAPA) == uma


def test_dominio_do_link_continua_o_mesmo():
    novo = reescrever_link(MSG_QUEBRADA, MAPA)
    assert "mercadolivre.com.br/vendas/" in novo


def test_regex_de_link_exige_o_caminho_de_venda():
    """Um número de 11 dígitos solto no texto (valor, tracking) não pode
    virar link reescrito."""
    assert not precisa_reparo("rastreio 47386687921 entregue")
    assert LINKS.search(MSG_QUEBRADA)


# --- correção em thread ---------------------------------------------------
#
# `chat.update` devolve `cant_update_message`: as mensagens de julho saíram
# pelo bot B0BHP9ZHZEX (NTC Painel) e o token de hoje é B0BKEBB6QKT (SAC
# Náutica). O Slack não deixa um app editar mensagem de outro, e isso não se
# resolve com escopo. A correção vai em thread, onde a pessoa clicou.

def test_correcao_traz_o_link_certo():
    from reparar_links import texto_da_correcao
    t = texto_da_correcao(["47386687921"], MAPA)
    assert "2000017121756758" in t
    assert "mercadolivre.com.br/vendas/" in t


def test_correcao_explica_sem_jargao():
    """Quem lê é a Maria e a diretoria."""
    from reparar_links import texto_da_correcao
    t = texto_da_correcao(["47386687921"], MAPA).lower()
    assert "não abre" in t or "nao abre" in t
    assert "shipment" not in t


def test_correcao_ignora_shipment_sem_pedido():
    from reparar_links import texto_da_correcao
    t = texto_da_correcao(["99999999999"], MAPA)
    assert "99999999999" not in t


def test_correcao_com_varios_pedidos_lista_todos():
    from reparar_links import texto_da_correcao
    t = texto_da_correcao(["47386687921", "47500233824"], MAPA)
    assert "2000017121756758" in t and "2000017364203014" in t
