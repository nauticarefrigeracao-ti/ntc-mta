"""O card "A caminho" — a tela que a Maria vai usar todo dia.

A Thayná desenhou em 06/08/2026 e a exigência dela é clara: **parecer com o
que a Maria já usa no Mercado Livre**. Interface nova é treinamento novo; o
card do pós-venda ela já sabe ler de cabeça.

Campos que o desenho e os prints pedem, nesta ordem de leitura:

    #pedido (clicável)  ·  produto  ·  valor  ·  unidades
    depósito ou Full    ·  motivo pela plataforma
    previsão de chegada ·  estado do envio  ·  rastreio

Três coisas que os testes travam:

**1. LOJA e FULL não se misturam.** Medido: das 30 devoluções com previsão, 19
vão para o galpão do ML em Cajamar e nunca passam pela Praia Grande. Perguntar
"já chegou?" para elas ensina a Maria a ignorar a pergunta — e aí ela perde as
11 que importam.

**2. Atrasado vem primeiro.** Previsão de ontem que não chegou é mais urgente
que a de hoje, não menos. Se a lista ordenar por data crescente sem destacar,
o atrasado afunda no meio.

**3. Valor é o da venda inteira.** `order_total` ignora quantidade — publicamos
R$ 659,00 onde o ML mostra R$ 1.318,00. O card usa o valor que fecha com a
tela do Meli, senão a Maria confere e não bate.
"""
from datetime import date

import pytest

from card_a_caminho import (
    linha_do_card,
    montar_blocos,
    ordenar_para_a_maria,
    rotulo_de_prazo,
    separar_por_destino,
)

HOJE = date(2026, 8, 6)


def caso(**kw):
    base = {
        "order_id": 2000017696047312,
        "item_title": "Termostato Geladeira Consul Crd36 Crm38",
        "item_sku": "NR1460",
        "unidades": 1,
        "valor": 181.0,
        "reason_label": "O comprador se arrependeu",
        "return_destino": "loja",
        "return_estimated_delivery": "2026-08-06T00:00:00.000-03:00",
        "return_status": "label_generated",
        "return_tracking_number": "AP273556466BR",
        "return_transportadora": "Devolução padrão",
    }
    base.update(kw)
    return base


# --- prazo: atrasado, hoje, futuro ----------------------------------------

def test_hoje_e_hoje():
    assert rotulo_de_prazo(caso(), HOJE) == "hoje"


def test_ontem_e_atrasado():
    c = caso(return_estimated_delivery="2026-08-05T00:00:00.000-03:00")
    assert rotulo_de_prazo(c, HOJE) == "atrasado"


def test_amanha_e_futuro():
    c = caso(return_estimated_delivery="2026-08-07T00:00:00.000-03:00")
    assert rotulo_de_prazo(c, HOJE) == "futuro"


def test_sem_previsao_e_sem_data():
    """Sem data não é "hoje". É desconhecido, e tem que aparecer como tal —
    senão a Maria procura um pacote que ninguém sabe quando vem."""
    assert rotulo_de_prazo(caso(return_estimated_delivery=None), HOJE) == "sem_data"


def test_fim_do_dia_nao_vira_amanha():
    c = caso(return_estimated_delivery="2026-08-06T23:00:00.000-03:00")
    assert rotulo_de_prazo(c, HOJE) == "hoje"


# --- separação loja x full ------------------------------------------------

def test_loja_e_full_nao_se_misturam():
    grupos = separar_por_destino([caso(), caso(return_destino="full")])
    assert len(grupos["loja"]) == 1 and len(grupos["full"]) == 1


def test_destino_desconhecido_tem_grupo_proprio():
    """Chutar "loja" faria o Slack cobrar confirmação de pacote que talvez
    nunca chegue."""
    grupos = separar_por_destino([caso(return_destino=None)])
    assert len(grupos["desconhecido"]) == 1
    assert not grupos["loja"]


def test_lista_vazia_nao_quebra():
    grupos = separar_por_destino([])
    assert grupos["loja"] == [] and grupos["full"] == []


# --- ordem: o atrasado primeiro -------------------------------------------

def test_atrasado_vem_antes_de_hoje():
    atrasado = caso(order_id=1, return_estimated_delivery="2026-08-04T00:00:00-03:00")
    hoje = caso(order_id=2, return_estimated_delivery="2026-08-06T00:00:00-03:00")
    ordem = [c["order_id"] for c in ordenar_para_a_maria([hoje, atrasado], HOJE)]
    assert ordem == [1, 2]


def test_mais_atrasado_vem_primeiro():
    a = caso(order_id=1, return_estimated_delivery="2026-08-01T00:00:00-03:00")
    b = caso(order_id=2, return_estimated_delivery="2026-08-04T00:00:00-03:00")
    ordem = [c["order_id"] for c in ordenar_para_a_maria([b, a], HOJE)]
    assert ordem == [1, 2]


def test_sem_data_vai_para_o_fim():
    """Não some — mas não ocupa o topo, que é dos que têm prazo estourado."""
    com = caso(order_id=1)
    sem = caso(order_id=2, return_estimated_delivery=None)
    ordem = [c["order_id"] for c in ordenar_para_a_maria([sem, com], HOJE)]
    assert ordem == [1, 2]


# --- a linha do card ------------------------------------------------------

def test_card_tem_o_pedido_clicavel():
    t = linha_do_card(caso(), HOJE)
    assert "2000017696047312" in t
    assert "mercadolivre.com.br/vendas/2000017696047312/detalhe" in t


def test_card_tem_produto_e_sku():
    t = linha_do_card(caso(), HOJE)
    assert "Termostato" in t and "NR1460" in t


def test_card_tem_valor_em_reais():
    assert "181,00" in linha_do_card(caso(), HOJE)


def test_card_mostra_unidades_quando_mais_de_uma():
    """2 unidades a R$ 659 é uma venda de R$ 1.318. Esconder a quantidade faz
    a Maria conferir no Meli e não bater."""
    t = linha_do_card(caso(unidades=2, valor=1318.0), HOJE)
    assert "2 unidades" in t


def test_uma_unidade_nao_polui_o_card():
    assert "1 unidade" in linha_do_card(caso(), HOJE)


def test_card_tem_o_motivo_da_plataforma():
    assert "arrependeu" in linha_do_card(caso(), HOJE)


def test_card_tem_a_previsao():
    assert "06/08" in linha_do_card(caso(), HOJE)


def test_card_tem_rastreio():
    assert "AP273556466BR" in linha_do_card(caso(), HOJE)


def test_card_sem_rastreio_nao_mostra_none():
    t = linha_do_card(caso(return_tracking_number=None), HOJE)
    assert "None" not in t


def test_atrasado_diz_de_quantos_dias():
    c = caso(return_estimated_delivery="2026-08-01T00:00:00-03:00")
    t = linha_do_card(c, HOJE)
    assert "5 dias" in t


def test_card_de_full_nao_pede_confirmacao():
    """A Maria não recebe esse pacote. Pedir confirmação seria pedir o
    impossível."""
    t = linha_do_card(caso(return_destino="full"), HOJE)
    assert "confirm" not in t.lower()


# --- os blocos do Slack ---------------------------------------------------

def test_blocos_separam_as_secoes():
    b = montar_blocos([caso(), caso(order_id=9, return_destino="full")], HOJE)
    texto = str(b)
    assert "loja" in texto.lower() or "Praia Grande" in texto
    assert "Full" in texto or "Mercado Livre tria" in texto


def test_blocos_dizem_quantas_sao():
    b = montar_blocos([caso(), caso(order_id=9)], HOJE)
    assert "2" in str(b)


def test_dia_sem_nada_diz_isso():
    b = montar_blocos([], HOJE)
    assert "nada" in str(b).lower() or "nenhuma" in str(b).lower()


def test_instrucao_de_confirmacao_aparece_so_na_secao_da_loja():
    """A Maria marca ✅ quando o pacote chega. Isso precisa estar escrito, ou
    ela não sabe que o sistema espera por ela."""
    b = montar_blocos([caso()], HOJE)
    assert "✅" in str(b)


# --- entregue não some: é quando a Maria mais precisa ver -----------------
#
# A primeira versão filtrava `return_status = 'delivered'` fora da lista, com
# a lógica de "já chegou, não está mais a caminho". Errado: o Mercado Livre
# dizer "entregue" é justamente o gatilho para a Maria conferir a caixa e
# confirmar. Sumir com esses tira da tela o caso mais urgente do dia.
#
# Medido: 2000017467759332 (Correia NR4498) estava `delivered`, destino loja,
# e aparecia no Quadro como "prazo venceu há 9 dias" — ou seja, chegou e
# ninguém tratou.

def test_entregue_continua_na_lista():
    c = caso(return_status="delivered")
    b = montar_blocos([c], HOJE)
    assert "2000017696047312" in str(b)


def test_entregue_e_destacado_para_confirmar():
    c = caso(return_status="delivered")
    t = linha_do_card(c, HOJE)
    assert "chegou" in t.lower() or "entregue" in t.lower()


def test_entregue_vem_antes_do_que_ainda_esta_vindo():
    """Caixa que já está no balcão tem prioridade sobre a que vem semana que
    vem."""
    entregue = caso(order_id=1, return_status="delivered",
                    return_estimated_delivery="2026-08-20T00:00:00-03:00")
    vindo = caso(order_id=2, return_status="shipped",
                 return_estimated_delivery="2026-08-07T00:00:00-03:00")
    ordem = [c["order_id"] for c in ordenar_para_a_maria([vindo, entregue], HOJE)]
    assert ordem == [1, 2]
