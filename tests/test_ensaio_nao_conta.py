"""Ensaio não vira número — a marcação sabe em que canal nasceu.

O Lucas perguntou, antes de clicar: "isso não vai enviesar a análise? e se o
produto não tiver chegado?". Enviesa. E o defeito era meu, não dele.

O card do `#sac-teste` aponta para o **mesmo claim real** do `#sac`. Sem
separação, clicar em "Recebi o produto" para conferir se o botão funciona:

    - grava data, hora e nome numa tabela de produção;
    - avança o caso na tela da Maria, que passaria a ver como recebido um
      produto que ainda está com o comprador;
    - e se o ensaio chegasse até "Recusado", punha dinheiro que não existe no
      cofrinho do mês — o mesmo cofrinho que o Gabriel vai ler.

A saída não é "clicar e apagar depois": entre o clique e o apagar existe uma
janela em que o número está errado, e apagar à mão é exatamente o tipo de
passo que alguém esquece. A saída é a marcação carregar o canal em que
nasceu, e o dinheiro só olhar para o canal oficial.

Isso também compra uma coisa que a gente ia precisar de qualquer jeito: dá
para **treinar a Maria no `#sac-teste`** clicando à vontade, sem sujar nada.
"""
import pytest

import card_maria
import cofrinho
from card_maria import blocos_do_card
from cofrinho import acumular

OFICIAL = "C0BHE11PZ5M"   # #sac
ENSAIO = "C0BKDHUGJ01"    # #sac-teste


def marca(etapa, canal, quando="2026-08-06T14:00:00-03:00"):
    return {"etapa": etapa, "quando": quando, "quem": "Maria",
            "observacao": None, "channel_id": canal}


def caso(claim_id=1, valor=509.89, timeline=None):
    return {"claim_id": claim_id, "valor": valor, "timeline": timeline or []}


FECHADO = ["recebi", "estoque", "mediacao", "recusado"]


# --- o dinheiro só olha para o canal oficial ------------------------------

def test_ensaio_nao_entra_no_cofrinho():
    """Clicar até "Recusado" no #sac-teste não pode virar R$ 509,89 no placar
    que o Gabriel lê."""
    t = [marca(e, ENSAIO) for e in FECHADO]
    r = acumular([caso(timeline=t)], 2026, 8, canal_oficial=OFICIAL)
    assert r["positivo"] == 0 and r["n_positivo"] == 0


def test_marcacao_oficial_entra_normalmente():
    t = [marca(e, OFICIAL) for e in FECHADO]
    r = acumular([caso(timeline=t)], 2026, 8, canal_oficial=OFICIAL)
    assert r["positivo"] == pytest.approx(509.89)


def test_ensaio_no_meio_nao_contamina_o_desfecho_oficial():
    """Um clique de teste antes do caso real não pode mudar o valor nem o dia
    do desfecho de verdade."""
    t = ([marca(e, ENSAIO) for e in FECHADO]
         + [marca(e, OFICIAL) for e in FECHADO])
    r = acumular([caso(timeline=t)], 2026, 8, canal_oficial=OFICIAL)
    assert r["n_positivo"] == 1


def test_sem_canal_oficial_definido_nada_e_filtrado():
    """Chamada antiga, sem o parâmetro, continua somando tudo — mas quem
    publica de verdade sempre passa o canal."""
    t = [marca(e, ENSAIO) for e in FECHADO]
    assert acumular([caso(timeline=t)], 2026, 8)["n_positivo"] == 1


# --- o card de ensaio se identifica ---------------------------------------

def test_card_de_ensaio_avisa_que_e_ensaio():
    """Sem o selo, alguém abre o #sac-teste, vê um caso "Recusado" e leva o
    número para uma reunião."""
    txt = str(blocos_do_card(caso_ml(), [], HOJE, ensaio=True))
    assert "ensaio" in txt.lower() or "teste" in txt.lower()


def test_card_oficial_nao_tem_selo():
    """Poluir a tela da Maria com aviso que não vale para ela é ruído."""
    txt = str(blocos_do_card(caso_ml(), [], HOJE, ensaio=False))
    assert "ensaio" not in txt.lower()


def test_selo_diz_que_nao_conta_no_placar():
    txt = str(blocos_do_card(caso_ml(), [], HOJE, ensaio=True)).lower()
    assert "não conta" in txt or "nao conta" in txt


# --- gravar sem canal é erro, não silêncio --------------------------------

def test_registrar_sem_canal_levanta():
    """Marcação sem canal não sabe se é dinheiro ou ensaio. Deixar passar
    como oficial seria escolher o pior dos dois erros — calado."""
    with pytest.raises(ValueError):
        card_maria.registrar(None, 1, "recebi", "Maria", channel_id=None)


def test_registrar_com_canal_vazio_tambem_levanta():
    with pytest.raises(ValueError):
        card_maria.registrar(None, 1, "recebi", "Maria", channel_id="")


# --- fixtures -------------------------------------------------------------

from datetime import date

HOJE = date(2026, 8, 6)


def caso_ml():
    return {
        "order_id": 2000017696047312, "pack_id": None, "claim_id": 5553858964,
        "item_title": "Termostato Geladeira Consul", "item_sku": "NR1460",
        "item_thumbnail": None, "unidades": 1, "valor": 47.15,
        "reason_label": "O comprador se arrependeu",
        "return_destino": "loja", "return_status": "shipped",
        "return_estimated_delivery": "2026-08-06T00:00:00-03:00",
        "return_tracking_number": None, "return_transportadora": None,
        "date_created": "2026-08-03T10:00:00-03:00",
    }


# --- o selo tem que sobreviver ao caminho real do listener -----------------
#
# Verificado lendo a mensagem publicada (06/08/2026): o cofrinho do
# #sac-teste saiu SEM selo, mostrando "seguramos R$ 2.131,33".
#
# Causa: liguei o selo comparando o NOME do canal (`canal != "#sac"`), mas o
# listener chama `publicar(channel_id=...)` sem passar nome — então `canal`
# ficava no default "#sac" e a comparação nunca dava verdadeira. O teste
# anterior passava porque chamava `blocos_do_cofrinho(ensaio=True)` direto,
# pulando exatamente o trecho quebrado.

def test_selo_e_decidido_pelo_id_do_canal_nao_pelo_nome():
    assert cofrinho.eh_ensaio(ENSAIO, OFICIAL) is True
    assert cofrinho.eh_ensaio(OFICIAL, OFICIAL) is False


def test_sem_saber_o_oficial_nao_chuta_que_e_ensaio():
    """Marcar tudo como ensaio esconderia o placar de verdade."""
    assert cofrinho.eh_ensaio(ENSAIO, None) is False
