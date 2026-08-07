"""O card da Maria — um card por caso, com botão, no formato do pós-venda.

A primeira tentativa (06/08/2026) publicou uma **parede de texto**: 28 casos
numa mensagem só, sem botão, sem ação. O Lucas foi direto ao ponto: "não é
acionável, não tem botão, não tem porra nenhuma — é só mensagem, como já era
antes". Estava certo. Este arquivo existe para que isso não se repita.

Três defeitos de DADO que os prints do Meli expuseram, cada um com teste:

**1. O número impresso não é o que o Meli mostra.** Publicamos
`#2000017686941586` (o `order_id`); a tela do vendedor mostra
`#2000014291726681` — o `pack_id`. Medido na API: `/orders/2000017686941586`
devolve `pack_id: 2000014291726681`. O link até funciona (o ML redireciona),
mas o número que a Maria lê no Slack não bate com o que ela vê no Meli, e a
regra é essa: se não bate, o card perde serventia.

**2. "Atrasado 1 dia" para pacote que ninguém postou.** Medido: o shipment do
retorno estava `ready_to_ship / printed`, sem `tracking_method`, sem eventos.
O `estimated_delivery_time` é uma promessa feita quando a etiqueta nasceu, não
uma entrega em curso. Chamar isso de atraso manda a Maria procurar uma caixa
que o comprador ainda não despachou — e ensina a ignorar a palavra "atrasado".

**3. Caso de dezembro de 2024 na lista de "chega hoje".** O card publicou
"atrasado 588 dias". Isso não é uma devolução a caminho, é um caso parado. Ele
já tem lugar no Quadro ("Parados — revisar ou encerrar"); no card do dia ele
só rouba a atenção das 9 que importam.
"""
from datetime import date

import pytest

from card_maria import (
    LIMITE_PARADO_DIAS,
    blocos_do_card,
    esta_parado,
    numero_na_plataforma,
    separar_parados,
    situacao_do_envio,
    texto_de_alerta,
)

HOJE = date(2026, 8, 6)


def caso(**kw):
    base = {
        "order_id": 2000017686941586,
        "pack_id": 2000014291726681,
        "claim_id": 5552858975,
        "item_title": "Limpador Desincrustante Limpeza Ar Condicionado E Geladeira",
        "item_sku": "NR4321",
        "item_thumbnail": "http://http2.mlstatic.com/D_NQ_NP_123-O.webp",
        "unidades": 2,
        "valor": 37.80,
        "reason_label": "O comprador se arrependeu",
        "return_destino": "loja",
        "return_estimated_delivery": "2026-08-05T00:00:00.000-03:00",
        "return_status": "label_generated",
        "return_tracking_number": None,
        "return_transportadora": "Devolução padrão",
        "date_created": "2026-08-01T18:24:41.000-03:00",
    }
    base.update(kw)
    return base


# --- 1. o número que a Maria vê no Meli -----------------------------------

def test_numero_e_o_pack_quando_existe():
    """A tela do vendedor mostra o pack. Imprimir o order_id faz a Maria
    conferir e não achar."""
    assert numero_na_plataforma(caso()) == 2000014291726681


def test_sem_pack_o_numero_e_o_pedido():
    assert numero_na_plataforma(caso(pack_id=None)) == 2000017686941586


def test_pack_igual_ao_pedido_nao_duplica():
    c = caso(pack_id=2000017686941586)
    assert numero_na_plataforma(c) == 2000017686941586


def test_card_imprime_e_linka_o_mesmo_numero():
    """Número escrito e número do link têm que ser o mesmo — senão a
    conferência dá em nada."""
    txt = str(blocos_do_card(caso(), [], HOJE))
    assert "2000014291726681" in txt
    assert "2000017686941586" not in txt


# --- 2. "atrasado" só vale para pacote em trânsito ------------------------

def test_etiqueta_gerada_nao_e_atraso():
    """`ready_to_ship / printed`: o comprador nem postou. Quem está devendo
    não somos nós, e não há caixa nenhuma para a Maria procurar."""
    s = situacao_do_envio(caso(return_status="label_generated"), HOJE)
    assert "atrasad" not in s.lower()
    assert "post" in s.lower()


def test_pendente_tambem_nao_e_atraso():
    s = situacao_do_envio(caso(return_status="pending"), HOJE)
    assert "atrasad" not in s.lower()


def test_em_transito_com_prazo_vencido_e_atraso_de_verdade():
    """Aí sim: o pacote saiu, a transportadora estourou o prazo."""
    c = caso(return_status="shipped",
             return_estimated_delivery="2026-08-03T00:00:00-03:00")
    s = situacao_do_envio(c, HOJE)
    assert "atrasad" in s.lower() and "3 dias" in s


def test_entregue_pede_conferencia():
    s = situacao_do_envio(caso(return_status="delivered"), HOJE)
    assert "chegou" in s.lower()


def test_chega_hoje_aparece_como_hoje():
    c = caso(return_status="shipped",
             return_estimated_delivery="2026-08-06T00:00:00-03:00")
    assert "hoje" in situacao_do_envio(c, HOJE).lower()


def test_sem_previsao_nao_inventa_data():
    c = caso(return_status="shipped", return_estimated_delivery=None)
    s = situacao_do_envio(c, HOJE)
    assert "sem previsão" in s.lower()


# --- 3. caso de 2024 não é "a caminho" ------------------------------------

def test_caso_de_2024_esta_parado():
    """588 dias de "atraso" não é uma entrega a caminho — é um caso morto que
    rouba a atenção das que importam."""
    c = caso(return_status="shipped", date_created="2024-12-15T00:00:00-03:00",
             return_estimated_delivery="2024-12-26T00:00:00-03:00")
    assert esta_parado(c, HOJE)


def test_caso_da_semana_nao_esta_parado():
    assert not esta_parado(caso(), HOJE)


def test_limite_de_parado_e_explicito():
    """Número mágico escondido no meio do código vira discussão depois."""
    assert LIMITE_PARADO_DIAS >= 30


def test_separar_tira_o_parado_da_lista_do_dia():
    vivo = caso(claim_id=1)
    morto = caso(claim_id=2, return_status="shipped",
                 date_created="2024-12-15T00:00:00-03:00",
                 return_estimated_delivery="2024-12-26T00:00:00-03:00")
    ativos, parados = separar_parados([vivo, morto], HOJE)
    assert [c["claim_id"] for c in ativos] == [1]
    assert [c["claim_id"] for c in parados] == [2]


def test_parado_nao_some_calado():
    """Sumir com o caso é o mesmo erro de sumir com o `delivered`. Ele sai da
    lista do dia, mas alguém precisa saber que ele existe."""
    assert "parad" in texto_de_alerta(2).lower()
    assert "2" in texto_de_alerta(2)


def test_sem_parados_nao_polui_o_card():
    assert texto_de_alerta(0) is None


# --- o card em si: acionável, não parede de texto -------------------------

def test_card_tem_botao():
    """O ponto inteiro desta entrega. Sem `actions` é mensagem, não card."""
    tipos = [b["type"] for b in blocos_do_card(caso(), [], HOJE)]
    assert "actions" in tipos


def test_botao_carrega_o_caso_no_value():
    """O clique chega ao listener sem contexto nenhum além do que pusermos
    aqui. Sem o claim_id, o botão não sabe que caso avançar."""
    txt = str(blocos_do_card(caso(), [], HOJE))
    assert "5552858975" in txt


def test_botao_do_primeiro_degrau_e_receber():
    b = blocos_do_card(caso(), [], HOJE)
    acoes = [x for x in b if x["type"] == "actions"][0]
    assert any("Recebi" in e["text"]["text"] for e in acoes["elements"])


def test_card_avancado_troca_os_botoes():
    """Depois de "recebi", o que aparece é estoque/garantia — não "recebi" de
    novo."""
    t = [{"etapa": "recebi", "quando": "2026-08-06T09:00:00-03:00",
          "quem": "Maria"}]
    acoes = [x for x in blocos_do_card(caso(), t, HOJE)
             if x["type"] == "actions"][0]
    rotulos = " ".join(e["text"]["text"] for e in acoes["elements"])
    assert "Estoque" in rotulos and "Recebi" not in rotulos


def test_card_mostra_a_linha_do_tempo():
    """Data e hora em cada marcação — o pedido explícito da Thayná."""
    t = [{"etapa": "recebi", "quando": "2026-08-06T09:12:00-03:00",
          "quem": "Maria"}]
    txt = str(blocos_do_card(caso(), t, HOJE))
    assert "06/08" in txt and "09:12" in txt and "Maria" in txt


def test_card_finalizado_nao_tem_botao_de_acao():
    t = [{"etapa": "recebi"}, {"etapa": "estoque"}, {"etapa": "mediacao"},
         {"etapa": "recusado"}, {"etapa": "finalizar"}]
    tipos = [b["type"] for b in blocos_do_card(caso(), t, HOJE)]
    assert "actions" not in tipos


def test_card_tem_foto_do_produto():
    """O card do Meli tem a foto, e é por ela que a Maria reconhece a peça na
    bancada antes de ler o SKU."""
    txt = str(blocos_do_card(caso(), [], HOJE))
    assert "mlstatic.com" in txt


def test_foto_http_vira_https():
    """O Slack recusa imagem em http — cairia calada, e o card ficaria sem a
    foto sem ninguém entender por quê."""
    txt = str(blocos_do_card(caso(), [], HOJE))
    assert "http://http2" not in txt
    assert "https://http2" in txt


def test_card_sem_foto_nao_quebra():
    b = blocos_do_card(caso(item_thumbnail=None), [], HOJE)
    assert b and "None" not in str(b)


def test_card_tem_sku_unidades_e_valor():
    txt = str(blocos_do_card(caso(), [], HOJE))
    assert "NR4321" in txt and "2 unidades" in txt and "37,80" in txt


def test_card_tem_o_motivo():
    assert "arrepend" in str(blocos_do_card(caso(), [], HOJE)).lower()


def test_card_cabe_no_limite_do_slack():
    """50 blocos por mensagem. Estourar derruba a publicação inteira."""
    t = [{"etapa": "observacao", "observacao": f"nota {i}"} for i in range(40)]
    assert len(blocos_do_card(caso(), t, HOJE)) <= 50


# --- caso que fechou no Meli não pode ficar com card vivo ------------------
#
# Medido em 07/08/2026: o Slack tinha 8 cards e o Mercado Livre só 6 casos
# abertos. Dois haviam sido encerrados na plataforma, e os cards continuavam
# na tela com todos os botões — a Maria clicaria em "Recebi o produto" num
# caso que não existe mais.
#
# Apagar o card seria pior: some da tela e ninguém entende o que aconteceu
# com aquele pedido. Ele fica, dizendo que acabou.

from card_maria import blocos_encerrado


def test_card_encerrado_diz_que_acabou():
    txt = str(blocos_encerrado(5552858975, 2000014291726681))
    assert "encerrad" in txt.lower()


def test_card_encerrado_nao_tem_botao():
    """Botão que age sobre caso inexistente é o pior tipo de botão."""
    tipos = [b["type"] for b in blocos_encerrado(1, 2)]
    assert "actions" not in tipos


def test_card_encerrado_mantem_o_pedido_visivel():
    """Some da tela é pior que ficar: ninguém entende o que houve com o
    pedido."""
    assert "2000014291726681" in str(blocos_encerrado(1, 2000014291726681))


def test_card_encerrado_diz_onde_conferir():
    txt = str(blocos_encerrado(1, 2)).lower()
    assert "mercado livre" in txt


def test_card_mostra_viagem_do_pacote():
    hist = [
        {"date": "2026-07-29T10:08:55.437-04:00", "substatus": "printed", "status": "ready_to_ship"},
        {"date": "2026-08-03T10:53:42.000-04:00", "substatus": None, "status": "shipped"},
    ]
    txt = str(blocos_do_card(caso(return_historico=hist), [], HOJE))
    assert "Viagem do pacote" in txt
    assert "Despachado" in txt


def test_card_mostra_aviso_de_atraso_do_ml():
    atr = [{"type": "shipping_delayed", "date": "2026-08-07T05:15:14Z"}]
    txt = str(blocos_do_card(caso(return_atrasos=atr), [], HOJE))
    assert "Aviso do Mercado Livre" in txt
    assert "transportadora" in txt.lower()


def test_card_mostra_link_da_transportadora():
    c = caso(return_tracking_number="AP123456BR",
             return_carrier_url="https://rastreio.correios.com.br/AP123456BR")
    txt = str(blocos_do_card(c, [], HOJE))
    assert "<https://rastreio.correios.com.br/AP123456BR|AP123456BR>" in txt

