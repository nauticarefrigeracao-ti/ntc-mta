"""Os dois cofrinhos — o positivo e o negativo, acumulando no mês.

A Thayná desenhou dois potes no canto da folha: um que enche quando a venda
fica de pé, outro quando o dinheiro sai. Diário, somando ao longo do mês.

**O risco desta feature é confundir com o balanço.** Já existe um número de
dinheiro do SAC: o prejuízo conciliado com o extrato do Mercado Livre — julho
fechou em R$ 5.930,84, defendido caso a caso. Se o cofrinho publicar outro
número no mesmo Slack sem dizer o que é, a primeira pergunta do Gabriel vai
ser "então qual dos dois é o certo?" — e aí os dois perdem valor.

São coisas diferentes, de propósito:

    cofrinho  = valor da VENDA EM DISPUTA, marcado pela Maria quando ela
                fecha o caso. É o placar do dia dela, sai na hora.
    balanço   = PREJUÍZO REAL, batido com o extrato do ML. Chega dias depois,
                já descontando tarifa, frete e o que o ML cobriu.

Por isso um teste aqui exige que a própria mensagem diga isso. Sem essa
frase, a feature nasce criando dúvida sobre um número que custou uma semana
para ficar de pé.

Três invariantes de dado:

**1. O dia é o do desfecho, não o da abertura.** Um caso aberto em 28/07 e
recusado em 06/08 é do cofrinho de agosto. Somar pela abertura joga dinheiro
de agosto no mês de julho, que já foi apresentado e fechado.

**2. 23h não vira amanhã.** A marcação é `TIMESTAMPTZ`; lida como UTC, uma
recusa às 23h30 de 06/08 cai em 07/08. O mesmo defeito que já comeu um dia da
previsão de entrega.

**3. Caso sem valor não vale zero.** Somar zero calado faz o cofrinho mentir
para baixo e ninguém descobre. Ele aparece separado, para alguém olhar.
"""
from datetime import date

import pytest

from cofrinho import (
    acumular,
    blocos_do_cofrinho,
    dia_do_desfecho,
    valor_em_jogo,
)

AGOSTO = (2026, 8)
HOJE = date(2026, 8, 6)


def marca(etapa, quando, quem="Maria"):
    return {"etapa": etapa, "quando": quando, "quem": quem, "observacao": None}


def caso(claim_id=1, valor=509.89, timeline=None):
    return {"claim_id": claim_id, "valor": valor,
            "item_title": "Compressor Embraco", "timeline": timeline or []}


FECHADO_A_FAVOR = [
    marca("recebi", "2026-08-05T10:00:00-03:00"),
    marca("estoque", "2026-08-05T10:05:00-03:00"),
    marca("mediacao", "2026-08-06T09:00:00-03:00"),
    marca("recusado", "2026-08-06T14:20:00-03:00"),
]

FECHADO_CONTRA = [
    marca("recebi", "2026-08-04T10:00:00-03:00"),
    marca("garantia", "2026-08-04T11:00:00-03:00"),
    marca("whatsapp", "2026-08-05T09:00:00-03:00"),
    marca("reembolsado", "2026-08-06T16:00:00-03:00"),
]


# --- o que entra em cada pote ---------------------------------------------

def test_recusado_enche_o_cofrinho_positivo():
    """Venda que ficou de pé."""
    r = acumular([caso(timeline=FECHADO_A_FAVOR)], *AGOSTO)
    assert r["positivo"] == pytest.approx(509.89)
    assert r["negativo"] == 0


def test_reembolsado_enche_o_negativo():
    """Dinheiro que saiu."""
    r = acumular([caso(timeline=FECHADO_CONTRA)], *AGOSTO)
    assert r["negativo"] == pytest.approx(509.89)
    assert r["positivo"] == 0


def test_caso_aberto_nao_entra_em_nenhum():
    """Mediação em curso não é ganho nem perda. Contar antes da hora infla o
    placar que vai para o Gabriel."""
    t = [marca("recebi", "2026-08-06T10:00:00-03:00"),
         marca("estoque", "2026-08-06T10:01:00-03:00"),
         marca("mediacao", "2026-08-06T11:00:00-03:00")]
    r = acumular([caso(timeline=t)], *AGOSTO)
    assert r["positivo"] == 0 and r["negativo"] == 0


def test_caso_sem_marcacao_nenhuma_nao_entra():
    assert acumular([caso(timeline=[])], *AGOSTO)["saldo"] == 0


def test_saldo_e_o_que_sobra():
    r = acumular([caso(1, 509.89, FECHADO_A_FAVOR),
                  caso(2, 200.00, FECHADO_CONTRA)], *AGOSTO)
    assert r["saldo"] == pytest.approx(309.89)


def test_conta_quantos_casos_em_cada_pote():
    r = acumular([caso(1, 100.0, FECHADO_A_FAVOR),
                  caso(2, 100.0, FECHADO_A_FAVOR),
                  caso(3, 100.0, FECHADO_CONTRA)], *AGOSTO)
    assert r["n_positivo"] == 2 and r["n_negativo"] == 1


# --- 1. o dia é o do desfecho ---------------------------------------------

def test_dia_do_desfecho_e_o_da_ultima_marcacao_que_decide():
    assert dia_do_desfecho(FECHADO_A_FAVOR) == date(2026, 8, 6)


def test_caso_aberto_em_julho_e_fechado_em_agosto_e_de_agosto():
    """Somar pela abertura joga dinheiro de agosto em julho — mês que já foi
    apresentado e fechado."""
    t = [marca("recebi", "2026-07-28T10:00:00-03:00"),
         marca("estoque", "2026-07-28T10:05:00-03:00"),
         marca("sem_argumento", "2026-07-29T10:00:00-03:00"),
         marca("recusado", "2026-08-03T15:00:00-03:00")]
    assert dia_do_desfecho(t) == date(2026, 8, 3)
    assert acumular([caso(timeline=t)], *AGOSTO)["n_positivo"] == 1


def test_desfecho_de_julho_nao_entra_no_cofrinho_de_agosto():
    t = [marca("recebi", "2026-07-20T10:00:00-03:00"),
         marca("estoque", "2026-07-20T10:05:00-03:00"),
         marca("mediacao", "2026-07-21T10:00:00-03:00"),
         marca("recusado", "2026-07-22T10:00:00-03:00")]
    assert acumular([caso(timeline=t)], *AGOSTO)["n_positivo"] == 0


def test_finalizar_depois_nao_muda_o_dia_do_desfecho():
    """"Finalizar" é o arquivamento; quem decide o dinheiro é recusado ou
    reembolsado."""
    t = FECHADO_A_FAVOR + [marca("finalizar", "2026-08-10T09:00:00-03:00")]
    assert dia_do_desfecho(t) == date(2026, 8, 6)


def test_sem_desfecho_nao_tem_dia():
    assert dia_do_desfecho([marca("recebi", "2026-08-06T10:00:00-03:00")]) is None


# --- 2. 23h não vira amanhã -----------------------------------------------

def test_marcacao_do_fim_do_dia_fica_no_dia():
    """Lida como UTC, uma recusa às 23h30 de 06/08 cairia em 07/08 — o mesmo
    defeito que já comeu um dia da previsão de entrega."""
    t = [marca("recebi", "2026-08-06T09:00:00-03:00"),
         marca("estoque", "2026-08-06T09:05:00-03:00"),
         marca("mediacao", "2026-08-06T10:00:00-03:00"),
         marca("recusado", "2026-08-06T23:30:00-03:00")]
    assert dia_do_desfecho(t) == date(2026, 8, 6)


def test_marcacao_em_utc_e_trazida_para_o_horario_de_brasilia():
    """O banco grava TIMESTAMPTZ e devolve em UTC. 02:00Z de 07/08 é 23:00 de
    06/08 aqui — e é no dia da Maria que o cofrinho conta."""
    t = [marca("recebi", "2026-08-06T12:00:00+00:00"),
         marca("estoque", "2026-08-06T12:05:00+00:00"),
         marca("mediacao", "2026-08-06T13:00:00+00:00"),
         marca("recusado", "2026-08-07T02:00:00+00:00")]
    assert dia_do_desfecho(t) == date(2026, 8, 6)


# --- 3. caso sem valor não vale zero --------------------------------------

def test_valor_zero_nao_e_valor():
    """Somar zero calado faz o cofrinho mentir para baixo."""
    assert valor_em_jogo({"valor": 0}) is None
    assert valor_em_jogo({"valor": None}) is None


def test_valor_valido_passa():
    assert valor_em_jogo({"valor": 509.89}) == pytest.approx(509.89)


def test_caso_sem_valor_aparece_separado():
    r = acumular([caso(valor=None, timeline=FECHADO_A_FAVOR)], *AGOSTO)
    assert r["sem_valor"] == [1]
    assert r["positivo"] == 0


def test_caso_sem_valor_nao_inflaciona_a_contagem():
    """Se contasse no n_positivo, o placar diria 1 caso e R$ 0,00 — e alguém
    concluiria que a venda valia nada."""
    r = acumular([caso(valor=None, timeline=FECHADO_A_FAVOR)], *AGOSTO)
    assert r["n_positivo"] == 0


# --- por dia, acumulando --------------------------------------------------

def test_separa_por_dia():
    r = acumular([caso(1, 100.0, FECHADO_A_FAVOR),
                  caso(2, 50.0, FECHADO_CONTRA)], *AGOSTO)
    dia = r["por_dia"][date(2026, 8, 6)]
    assert dia["positivo"] == pytest.approx(100.0)
    assert dia["negativo"] == pytest.approx(50.0)


def test_dias_diferentes_nao_se_misturam():
    t3 = [marca("recebi", "2026-08-03T09:00:00-03:00"),
          marca("estoque", "2026-08-03T09:05:00-03:00"),
          marca("mediacao", "2026-08-03T10:00:00-03:00"),
          marca("recusado", "2026-08-03T11:00:00-03:00")]
    r = acumular([caso(1, 100.0, FECHADO_A_FAVOR), caso(2, 70.0, t3)], *AGOSTO)
    assert r["por_dia"][date(2026, 8, 3)]["positivo"] == pytest.approx(70.0)
    assert r["por_dia"][date(2026, 8, 6)]["positivo"] == pytest.approx(100.0)


def test_mes_vazio_nao_explode():
    r = acumular([], *AGOSTO)
    assert r["saldo"] == 0 and r["por_dia"] == {}


# --- a mensagem que vai para o Slack --------------------------------------

def test_mensagem_mostra_os_dois_potes():
    txt = str(blocos_do_cofrinho(
        acumular([caso(1, 509.89, FECHADO_A_FAVOR),
                  caso(2, 200.0, FECHADO_CONTRA)], *AGOSTO), HOJE))
    assert "509,89" in txt and "200,00" in txt


def test_mensagem_mostra_o_saldo():
    txt = str(blocos_do_cofrinho(
        acumular([caso(1, 509.89, FECHADO_A_FAVOR)], *AGOSTO), HOJE))
    assert "509,89" in txt


def test_mensagem_diz_o_dia_de_hoje():
    txt = str(blocos_do_cofrinho(
        acumular([caso(1, 509.89, FECHADO_A_FAVOR)], *AGOSTO), HOJE))
    assert "06/08" in txt


def test_mensagem_separa_o_cofrinho_do_balanco():
    """A frase que impede a pergunta "então qual dos dois números é o certo?".
    Sem ela, esta feature nasce jogando dúvida sobre o balanço conciliado."""
    txt = str(blocos_do_cofrinho(acumular([], *AGOSTO), HOJE)).lower()
    assert "fechamento" in txt
    assert "extrato" in txt or "conciliad" in txt


def test_mes_sem_movimento_nao_finge_placar():
    txt = str(blocos_do_cofrinho(acumular([], *AGOSTO), HOJE)).lower()
    assert "ainda" in txt or "nenhum" in txt


def test_casos_sem_valor_sao_denunciados_na_mensagem():
    r = acumular([caso(valor=None, timeline=FECHADO_A_FAVOR)], *AGOSTO)
    assert "sem valor" in str(blocos_do_cofrinho(r, HOJE)).lower()


def test_mensagem_cabe_no_limite_do_slack():
    casos = []
    for i in range(60):
        d = f"2026-08-{(i % 28) + 1:02d}"
        casos.append(caso(i, 10.0, [
            marca("recebi", f"{d}T09:00:00-03:00"),
            marca("estoque", f"{d}T09:05:00-03:00"),
            marca("mediacao", f"{d}T10:00:00-03:00"),
            marca("recusado", f"{d}T11:00:00-03:00")]))
    assert len(blocos_do_cofrinho(acumular(casos, *AGOSTO), HOJE)) <= 50


# --- o placar de treino também se identifica -------------------------------
#
# Medido no QA dos 12 caminhos (06/08/2026): fechar os casos no #sac-teste
# fez o cofrinho DAQUELE canal publicar "seguramos R$ 2.131,33". O número é
# correto para o canal — é assim que a Maria vê o pote mexer enquanto treina
# — mas sem selo é um print pronto para ser levado a uma reunião como se
# fosse dinheiro de verdade. O card já avisa; o placar tem que avisar também.

def test_cofrinho_de_ensaio_avisa():
    txt = str(blocos_do_cofrinho(acumular([caso(1, 509.89, FECHADO_A_FAVOR)],
                                          *AGOSTO), HOJE, ensaio=True))
    assert "ensaio" in txt.lower() or "treino" in txt.lower()


def test_cofrinho_de_ensaio_diz_que_nao_e_dinheiro():
    txt = str(blocos_do_cofrinho(acumular([], *AGOSTO), HOJE,
                                 ensaio=True)).lower()
    assert "não é dinheiro" in txt or "nao e dinheiro" in txt


def test_cofrinho_oficial_nao_tem_selo():
    txt = str(blocos_do_cofrinho(acumular([], *AGOSTO), HOJE, ensaio=False))
    assert "ensaio" not in txt.lower()


# --- o Quadro precisa mostrar o trabalho da Maria, não só a fila ----------
#
# Pedido do Lucas em 07/08/2026: "o quadro do SAC tem que refletir isso aí —
# cofrinho, casos resolvidos nos dias, os desfechos. Daí tem que atualizar.
# O quadro ficará como registro e visualização do trabalho da Maria".
#
# Hoje o Quadro só mostra FILA: o que falta fazer. Quem trabalhou o dia
# inteiro e fechou seis casos vê a mesma tela de quem não fez nada — só que
# com menos itens. Falta o outro lado: o que foi FEITO.
#
# E o carimbo de frescor: sem ele, ninguém sabe se está olhando dado de agora
# ou de ontem. Foi exatamente assim que a tabela `orders` ficou 13 dias
# congelada sem ninguém perceber.

from cofrinho import linha_de_desfechos, texto_de_frescor


def test_desfechos_do_dia_aparecem():
    r = acumular([caso(1, 509.89, FECHADO_A_FAVOR),
                  caso(2, 200.0, FECHADO_CONTRA)], *AGOSTO)
    t = linha_de_desfechos(r, HOJE)
    assert "509,89" in t and "200,00" in t


def test_dia_sem_desfecho_diz_isso_sem_parecer_erro():
    """Dia sem caso fechado é normal — não pode parecer sistema quebrado."""
    t = linha_de_desfechos(acumular([], *AGOSTO), HOJE).lower()
    assert "nenhum" in t or "ainda" in t


def test_desfechos_dizem_quantos_casos():
    r = acumular([caso(1, 100.0, FECHADO_A_FAVOR),
                  caso(2, 100.0, FECHADO_A_FAVOR)], *AGOSTO)
    assert "2" in linha_de_desfechos(r, HOJE)


def test_frescor_diz_ha_quanto_tempo():
    """"Atualizado 07/08" não responde a pergunta. "Há 4 minutos" responde."""
    assert "4 minuto" in texto_de_frescor(4 * 60)


def test_frescor_recente_tranquiliza():
    t = texto_de_frescor(30).lower()
    assert "agora" in t


def test_frescor_velho_avisa():
    """Acima de meia hora a Maria pode estar agindo sobre informação de outro
    turno — e precisa saber disso antes de agir, não depois."""
    t = texto_de_frescor(3 * 3600)
    assert "⚠" in t or "atras" in t.lower() or "atrás" in t.lower()


def test_frescor_sem_dado_nao_mente():
    """"Atualizado agora" quando não se sabe é a pior das respostas."""
    assert "?" in texto_de_frescor(None) or "não" in texto_de_frescor(None)
