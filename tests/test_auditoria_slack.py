"""Auditoria dos canais: duplicidade e lacuna, nas mensagens E nos Canvas.

Por que existe: o chefe abre o Slack e confere. Já aconteceu de tudo aqui —
o fechamento publicado duas vezes no #sac-fechamento, uma aba de Canvas vazia
no #sac que quase levou o Canvas certo junto na limpeza, e o mesmo caso
contado duas vezes inflando o prejuízo em 45%.

Três perguntas, e nenhuma delas é "o job rodou sem erro":

  DUPLICIDADE  o mesmo caso aparece duas vezes no canal ou no Canvas?
  LACUNA       um caso que devia estar publicado não está?
  DIVERGENCIA  o que está publicado bate com o estado atual no banco?

As funções aqui são puras: recebem o que foi LIDO do Slack e o que o banco
diz, e devolvem os achados. A leitura real vive no auditoria_slack.py.
"""
import pytest

from auditoria_slack import (
    duplicidades,
    estado_publicado_no_canvas,
    ids_no_texto,
    lacunas,
    divergencias_de_estado,
    resumir_auditoria,
)

# Recorte real do Canvas do #sac (montar_canvas_quadro): só "A Fazer" lista
# caso a caso; "Aguardando" e "Feito" são contadores.
CANVAS = """# 🗂️ Quadro do SAC — 03/08/2026

## 🔴 A Fazer — 2
**1 com prazo VENCIDO** — comece por aqui.

- **Compressor Embraco 1/3+**
  ⏰ venceu · SKU ABC-1 · _produto com defeito_ · R$ 503,17
  [Abrir a venda 2000017501621056](https://www.mercadolivre.com.br/vendas/2000017501621056/detalhe)
- **Válvula de expansão**
  SKU DEF-2 · _arrependimento_ · R$ 274,42
  [Abrir a venda 2000017520404366](https://www.mercadolivre.com.br/vendas/2000017520404366/detalhe)

## 🟡 Aguardando — 37
_O Mercado Livre está arbitrando._

## 🟢 Feito — 12
"""


# --- extração de ids -------------------------------------------------------

def test_extrai_order_id_de_16_digitos():
    assert ids_no_texto("pedido 2000017501621056 em disputa") == {2000017501621056}


def test_extrai_de_link_do_mercado_livre():
    txt = "https://www.mercadolivre.com.br/vendas/2000017501621056/detalhe"
    assert 2000017501621056 in ids_no_texto(txt)


def test_extrai_pedido_antigo_de_10_digitos():
    """Medido na API em 30/07: 10 dígitos é pedido legítimo, não lixo."""
    assert 5462527754 in ids_no_texto("pedido 5462527754")


def test_ignora_numero_que_nao_e_pedido():
    """R$ 1.234,56 e '48 horas' não podem virar order_id fantasma."""
    achados = ids_no_texto("R$ 1.234,56 — responder em 48 horas (3 casos)")
    assert achados == set()


def test_nao_confunde_shipment_de_11_digitos_com_pedido():
    """11 dígitos é shipment: o link não abre. Se aparecer publicado, é
    achado, não id válido — por isso não entra como pedido."""
    assert 47536582431 not in ids_no_texto("envio 47536582431")


# --- duplicidade -----------------------------------------------------------

def test_sem_repeticao_nao_ha_achado():
    assert duplicidades([1, 2, 3]) == []


def test_id_repetido_e_apontado_com_a_contagem():
    d = duplicidades([1, 2, 2, 3, 2])
    assert d == [(2, 3)]


def test_varias_duplicatas_saem_da_pior_para_a_menor():
    """Quem lê o relatório trata a mais grave primeiro."""
    assert duplicidades([1, 1, 2, 2, 2]) == [(2, 3), (1, 2)]


def test_o_caso_real_do_fechamento_publicado_duas_vezes():
    """31/07: o resumo do dia foi ao #sac-fechamento duas vezes."""
    assert duplicidades([555, 555]) == [(555, 2)]


# --- lacuna ----------------------------------------------------------------

def test_tudo_publicado_nao_gera_lacuna():
    assert lacunas(devidos={1, 2}, publicados={1, 2}) == []


def test_o_que_devia_estar_e_nao_esta_vira_lacuna():
    assert lacunas(devidos={1, 2, 3}, publicados={1}) == [2, 3]


def test_publicado_a_mais_nao_e_lacuna():
    """Publicar algo que o banco não cobra não é buraco — pode ser caso
    antigo que saiu do recorte. Lacuna é falta, não sobra."""
    assert lacunas(devidos={1}, publicados={1, 99}) == []


def test_lacuna_sai_ordenada():
    assert lacunas(devidos={9, 3, 7}, publicados=set()) == [3, 7, 9]


# --- divergência de estado -------------------------------------------------

def test_estado_igual_nao_diverge():
    publicado = {1: "closed"}
    banco = {1: "closed"}
    assert divergencias_de_estado(publicado, banco) == []


def test_caso_fechado_no_ml_e_aberto_no_canvas_diverge():
    """O quadro da Maria mostrando 'A Fazer' o que o ML já encerrou é o
    defeito mais caro: ela trabalha o que não existe mais."""
    d = divergencias_de_estado({1: "opened"}, {1: "closed"})
    assert d == [(1, "opened", "closed")]


def test_caso_publicado_que_sumiu_do_banco_nao_e_divergencia_de_estado():
    """Sem estado no banco não há com o que comparar — isso é outro achado
    (id inexistente), não divergência."""
    assert divergencias_de_estado({1: "opened"}, {}) == []


# --- estado publicado no Canvas -------------------------------------------
# O Canvas é FOTOGRAFIA de estado, não histórico. Se ele mostra em "A Fazer"
# um caso que o ML já encerrou, a Maria trabalha o que não existe mais — e é
# justamente o que o chefe vê quando abre o quadro.

def test_le_os_pedidos_listados_em_a_fazer():
    e = estado_publicado_no_canvas(CANVAS)
    assert e == {2000017501621056: "a_fazer", 2000017520404366: "a_fazer"}


def test_contadores_nao_viram_pedido():
    """'Aguardando — 37' não pode virar o pedido 37."""
    e = estado_publicado_no_canvas(CANVAS)
    assert 37 not in e and 12 not in e


def test_canvas_sem_a_fazer_nao_publica_estado_nenhum():
    vazio = "# 🗂️ Quadro do SAC — 03/08\n\n## 🔴 A Fazer — 0\n_Nada pendente._\n"
    assert estado_publicado_no_canvas(vazio) == {}


def test_canvas_vazio_nao_explode():
    assert estado_publicado_no_canvas("") == {}


def test_caso_ja_encerrado_ainda_em_a_fazer_e_divergencia():
    """O defeito que o chefe pega: quadro desatualizado."""
    publicado = estado_publicado_no_canvas(CANVAS)
    banco = {2000017501621056: "feito", 2000017520404366: "a_fazer"}
    assert divergencias_de_estado(publicado, banco) == [
        (2000017501621056, "a_fazer", "feito")]


# --- o que É devido no canal -----------------------------------------------
# Terceira vez que esta classe de erro aparece. A auditoria acusou 3 lacunas
# no #sac (2000017357006052, 2000017582555852, 2000017686941586). Nenhuma era
# lacuna: os três são `opened/dispute` e, pela regra D3, disputa fica no
# Canvas — não vira mensagem. `slack_notificados` registra CASO PROCESSADO
# (é a chave de deduplicação), não MENSAGEM PUBLICADA. Tratar uma coisa como
# a outra transforma comportamento correto em acusação.

def test_devido_no_canal_e_o_que_a_regra_D3_manda_publicar():
    from auditoria_slack import devido_no_canal
    fechado = {"claim_status": "closed", "claim_stage": "dispute"}
    assert devido_no_canal(fechado) is True


def test_disputa_aberta_nao_e_devida_no_canal():
    """Vai para o Canvas. Cobrar mensagem dela inventa lacuna."""
    from auditoria_slack import devido_no_canal
    aberto = {"claim_status": "opened", "claim_stage": "dispute"}
    assert devido_no_canal(aberto) is False


def test_o_caso_real_das_tres_lacunas_falsas():
    from auditoria_slack import devido_no_canal
    for oid in (2000017357006052, 2000017582555852, 2000017686941586):
        linha = {"order_id": oid, "claim_status": "opened",
                 "claim_stage": "dispute"}
        assert devido_no_canal(linha) is False, f"{oid} voltou a ser acusado"


def test_devido_usa_a_MESMA_regra_do_notificador():
    """Se a auditoria tiver a própria cópia da regra, ela audita a opinião
    dela, não o sistema."""
    import auditoria_slack
    import slack_notify
    assert auditoria_slack.devido_no_canal is slack_notify.deve_notificar_no_canal


# --- a quem cobrar lacuna --------------------------------------------------
# Primeira execução real (03/08/2026) acusou 72 lacunas no #sac-fechamento.
# Nenhuma era verdadeira: aquele canal é o PLACAR do chefe — números
# agregados, nunca pedido individual. Cobrar dele a publicação caso a caso é
# a mesma acusação injusta que o primeiro validador fez contra 36 de 40 casos
# corretos. Lacuna só faz sentido onde a publicação por caso é a função.

def test_o_canal_por_caso_e_cobrado_de_lacuna():
    from auditoria_slack import cobra_lacuna
    assert cobra_lacuna("#sac") is True


def test_o_canal_de_placar_nao_e_cobrado_de_lacuna():
    from auditoria_slack import cobra_lacuna
    assert cobra_lacuna("#sac-fechamento") is False


def test_canal_de_teste_e_cobrado_como_o_de_producao():
    """#sac-teste espelha o #sac; se lá falta, aqui também vai faltar."""
    from auditoria_slack import cobra_lacuna
    assert cobra_lacuna("#sac-teste") is True


# --- resumo ----------------------------------------------------------------

def test_auditoria_limpa_diz_que_esta_limpa():
    r = resumir_auditoria(canal="#sac", duplicados=[], faltando=[],
                          divergentes=[], lidos=50)
    assert r["ok"] is True
    assert "50" in r["texto"]


def test_uma_duplicidade_reprova_a_auditoria():
    r = resumir_auditoria(canal="#sac", duplicados=[(2, 3)], faltando=[],
                          divergentes=[], lidos=50)
    assert r["ok"] is False
    assert "2" in r["texto"]


def test_o_resumo_diz_o_canal():
    r = resumir_auditoria(canal="#sac-fechamento", duplicados=[], faltando=[],
                          divergentes=[], lidos=1)
    assert "#sac-fechamento" in r["texto"]


def test_canal_sem_nada_lido_nao_passa_como_limpo():
    """Ler zero mensagens e reportar 'sem divergência' foi exatamente como um
    validador deu OK numa tela de login. Ausência de dado não é aprovação."""
    r = resumir_auditoria(canal="#sac", duplicados=[], faltando=[],
                          divergentes=[], lidos=0)
    assert r["ok"] is False
    assert "nada" in r["texto"].lower() or "zero" in r["texto"].lower()
