"""Disputa aberta não tem prazo — mas tem idade, e a idade é medível.

MEDIÇÃO NA API DO ML, 03/08/2026, quatro claims abertas:
o Mercado Livre **não expõe prazo** para disputa. Não há `due_date`, não há
`expiration`; `/expected_resolution`, `/actions` e `/resolution` devolvem 400.
Só o estágio `claim` traz `available_actions`, e no caso medido todas vinham
com `mandatory: False`. A coluna `ml_mandatory_due` está preenchida em 6 de
18.166 linhas e em ZERO das 33 abertas.

Ou seja: 27 dos 33 casos abertos (82%) aparecem no Quadro sem relógio nenhum,
e não há relógio do ML para colocar ali. Inventar um seria o mesmo defeito de
inventar qualquer outro número.

O que existe e é honesto é a NOSSA história (759 disputas fechadas em 2026):

    estágio      mediana   p90     maior já fechado
    dispute      11,8 d    42,0 d      344,6 d
    claim         6,9 d    27,5 d      105,7 d
    recontact    10,7 d    19,1 d       29,8 d

Com isso dá para dizer duas coisas verdadeiras: há quanto tempo o caso está
aberto, e se ele já passou do tempo que 90% dos casos iguais levaram.

E aparece um terceiro grupo: três casos em `claim` abertos há 595, 563 e 364
dias — além do maior `claim` que já conseguimos fechar (105,7). Não são
atrasados: são abandonados, e ficam ocupando o quadro da Maria para sempre.
"""
import pytest

from slack_notify import (
    DIAS_P90,
    MAIOR_JA_FECHADO,
    idade_em_dias,
    parece_abandonado,
    passou_do_tipico,
    texto_idade,
)


def _caso(estagio="dispute", dias=1.0):
    from datetime import datetime, timedelta, timezone
    criado = datetime.now(timezone.utc) - timedelta(days=dias)
    return {"claim_stage": estagio, "claim_status": "opened",
            "date_created": criado.isoformat()}


# --- idade -----------------------------------------------------------------

def test_idade_vem_da_data_de_criacao():
    assert 2.9 < idade_em_dias(_caso(dias=3)) < 3.1


def test_sem_data_de_criacao_nao_inventa_idade():
    """Melhor não dizer nada do que dizer um número que não medimos."""
    assert idade_em_dias({"claim_stage": "dispute"}) is None


def test_data_lixo_nao_quebra_o_quadro():
    assert idade_em_dias({"date_created": "sem data"}) is None


# --- passou do típico ------------------------------------------------------

def test_disputa_nova_nao_e_atrasada():
    assert passou_do_tipico(_caso("dispute", dias=5)) is False


def test_disputa_alem_do_p90_e_atrasada():
    """42,0 dias é o p90 medido em 759 disputas fechadas em 2026."""
    assert passou_do_tipico(_caso("dispute", dias=45)) is True


def test_cada_estagio_tem_a_sua_regua():
    """27,5 dias já é fora do normal para `claim`, mas não para `dispute`."""
    assert passou_do_tipico(_caso("claim", dias=30)) is True
    assert passou_do_tipico(_caso("dispute", dias=30)) is False


def test_estagio_desconhecido_nao_acusa():
    assert passou_do_tipico(_caso("qualquer_coisa", dias=999)) is False


def test_sem_idade_nao_acusa():
    assert passou_do_tipico({"claim_stage": "dispute"}) is False


# --- abandonados -----------------------------------------------------------

def test_caso_dentro_do_ja_visto_nao_e_abandonado():
    assert parece_abandonado(_caso("claim", dias=100)) is False


def test_os_tres_casos_reais_de_2024_2025():
    """595, 563 e 364 dias em `claim`. O maior `claim` que já fechamos levou
    105,7 dias — esses três estão além de tudo que a operação já resolveu."""
    for dias in (595.7, 562.9, 363.7):
        assert parece_abandonado(_caso("claim", dias=dias)) is True


def test_disputa_longa_mas_dentro_do_historico_nao_e_abandonada():
    """Já fechamos disputa de 344,6 dias — 300 ainda é plausível."""
    assert parece_abandonado(_caso("dispute", dias=300)) is False


def test_abandonado_tambem_passou_do_tipico():
    """As duas leituras não podem se contradizer."""
    c = _caso("claim", dias=595)
    assert passou_do_tipico(c) and parece_abandonado(c)


# --- texto -----------------------------------------------------------------

def test_texto_de_horas_para_caso_novo():
    assert "h" in texto_idade(_caso(dias=0.5))


def test_texto_de_dias():
    assert "dia" in texto_idade(_caso(dias=5))


def test_texto_de_meses_para_caso_muito_antigo():
    """"aberto há 595 dias" faz o leitor parar para converter."""
    t = texto_idade(_caso(dias=595))
    assert "ano" in t or "mês" in t or "mes" in t


def test_sem_data_o_texto_e_vazio_e_nao_um_traco_misterioso():
    assert texto_idade({"claim_stage": "dispute"}) == ""


# --- as constantes são medidas, não escolhidas -----------------------------

def test_as_reguas_cobrem_os_tres_estagios_reais():
    for estagio in ("dispute", "claim", "recontact"):
        assert estagio in DIAS_P90
        assert estagio in MAIOR_JA_FECHADO


# --- o quadro usa isso -----------------------------------------------------

def _linha(estagio, dias, oid=2000017501621056, titulo="Compressor"):
    c = _caso(estagio, dias)
    c.update({"order_id": oid, "item_title": titulo, "claim_status": "opened",
              "item_sku": "NR1", "reason_label": "x"})
    return c


def test_quadro_conta_os_lentos_em_aguardando():
    from slack_notify import montar_canvas_quadro
    md = montar_canvas_quadro([_linha("dispute", 60)], "03/08/2026")
    assert "90%" in md


def test_quadro_nao_inventa_lentidao_quando_nao_ha():
    from slack_notify import montar_canvas_quadro
    md = montar_canvas_quadro([_linha("dispute", 3)], "03/08/2026")
    assert "90%" not in md


def test_abandonado_ganha_bloco_proprio_com_link():
    """Os 3 casos de 595/563/364 dias ficavam no quadro para sempre sem que
    nada distinguisse eles de um caso de ontem."""
    from slack_notify import montar_canvas_quadro
    md = montar_canvas_quadro([_linha("claim", 595)], "03/08/2026")
    assert "Parados" in md
    assert "2000017501621056" in md


def test_abandonado_nao_e_contado_duas_vezes_como_lento():
    """Ele já tem bloco próprio; contá-lo também em 'passou do típico' faria
    o mesmo caso aparecer em duas contagens."""
    from slack_notify import montar_canvas_quadro
    md = montar_canvas_quadro([_linha("claim", 595)], "03/08/2026")
    assert "90% dos casos" not in md


def test_parado_sai_de_a_fazer():
    """DEFEITO REAL (03/08): os 3 zumbis apareciam em "A Fazer" E em
    "Parados" — o mesmo pedido duas vezes no mesmo Canvas, que é exatamente o
    que a auditoria proíbe. Pior: "6 com prazo VENCIDO" quando só 3 eram de
    verdade; os zumbis de 2024 empurravam os casos de agosto para baixo.

    "A Fazer" é o que se resolve respondendo. Caso de 1,6 ano não se resolve
    respondendo."""
    from slack_notify import montar_canvas_quadro
    md = montar_canvas_quadro([_linha("claim", 595, oid=1111111111),
                               _linha("claim", 2, oid=2222222222)],
                              "03/08/2026")
    a_fazer = md.split("##")[1]
    assert "1111111111" not in a_fazer, "zumbi voltou para A Fazer"
    assert "2222222222" in a_fazer
    assert "A Fazer — 1" in md


def test_o_pedido_parado_aparece_uma_vez_so_no_canvas():
    from slack_notify import montar_canvas_quadro
    md = montar_canvas_quadro([_linha("claim", 595, oid=1111111111)],
                              "03/08/2026")
    assert md.count("1111111111") == 2, "id aparece no texto e no link, uma vez só"


def test_parado_tambem_sai_de_aguardando():
    from slack_notify import montar_canvas_quadro
    linha = _linha("dispute", 400, oid=3333333333)
    md = montar_canvas_quadro([linha], "03/08/2026")
    assert "Aguardando — 0" in md
    assert "Parados — 1" in md


def test_quadro_sem_parados_nao_mostra_o_bloco():
    from slack_notify import montar_canvas_quadro
    md = montar_canvas_quadro([_linha("dispute", 2)], "03/08/2026")
    assert "Parados" not in md


def test_o_maior_ja_fechado_e_sempre_maior_que_o_p90():
    """Se o teto ficar abaixo do p90, todo caso atrasado viraria abandonado
    e a distinção — que muda a ação — desaparece."""
    for estagio, p90 in DIAS_P90.items():
        assert MAIOR_JA_FECHADO[estagio] > p90
