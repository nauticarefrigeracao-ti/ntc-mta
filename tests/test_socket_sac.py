"""O listener que faz o botão virar ação — Socket Mode.

Sem este processo no ar, botão é decoração: a Maria clica, nada acontece, e
ela volta a usar o Meli. Pior que não ter botão.

O que o Slack manda quando alguém clica, e o que este arquivo trava:

**1. Ack em 3 segundos ou o Slack reenvia.** Se o ack só sair depois de gravar
no banco e reescrever o card, o Slack considera perdido e manda de novo — e
a mesma marcação entra duas vezes na linha do tempo. O ack vem primeiro,
sempre.

**2. O clique chega sem contexto.** Só `action_id` e `value` carregam o que
pusermos neles. Se o `claim_id` não vier no `value`, o listener não sabe qual
caso avançar — e não pode adivinhar.

**3. Nome de gente, não ID.** `U0BH1234` na linha do tempo não responde "quem
marcou isso". A Thayná pediu quem, e quem é "Maria".
"""
import json

import pytest

from socket_sac import (
    interpretar,
    modal_de_observacao,
    nome_de,
    resposta_de_ack,
)


def envelope_de_clique(action_id="sac_recebi", value="5552858975",
                       envelope_id="env-1"):
    return {
        "envelope_id": envelope_id,
        "type": "interactive",
        "payload": {
            "type": "block_actions",
            "user": {"id": "U0BH1", "username": "maria",
                     "name": "maria"},
            "channel": {"id": "C0BHE11PZ5M"},
            "message": {"ts": "1754500000.123456"},
            "trigger_id": "trig-1",
            "actions": [{"action_id": action_id, "value": value,
                         "type": "button"}],
        },
    }


# --- ack primeiro, sempre -------------------------------------------------

def test_ack_devolve_o_envelope_id():
    """Sem o envelope_id de volta, o Slack reenvia o clique e a marcação
    entra duas vezes."""
    assert resposta_de_ack("env-1") == {"envelope_id": "env-1"}


def test_ack_e_serializavel():
    json.dumps(resposta_de_ack("env-1"))


# --- interpretar o clique -------------------------------------------------

def test_clique_vira_acao():
    e = interpretar(envelope_de_clique())
    assert e["acao"] == "recebi"
    assert e["claim_id"] == 5552858975


def test_traz_onde_atualizar_o_card():
    """Sem channel e ts não dá para reescrever o card no lugar — viraria
    mensagem nova a cada clique."""
    e = interpretar(envelope_de_clique())
    assert e["channel"] == "C0BHE11PZ5M"
    assert e["ts"] == "1754500000.123456"


def test_traz_quem_clicou():
    assert interpretar(envelope_de_clique())["quem"] == "maria"


def test_traz_o_trigger_para_abrir_modal():
    assert interpretar(envelope_de_clique())["trigger_id"] == "trig-1"


def test_prefixo_sac_e_obrigatorio():
    """Outros apps e outros botões vivem no mesmo canal. Reagir a tudo é
    avançar caso por clique alheio."""
    assert interpretar(envelope_de_clique(action_id="outra_coisa")) is None


def test_evento_que_nao_e_clique_e_ignorado():
    assert interpretar({"envelope_id": "x", "type": "hello"}) is None


def test_envelope_sem_payload_nao_explode():
    assert interpretar({"envelope_id": "x", "type": "interactive"}) is None


def test_value_invalido_nao_vira_claim_zero():
    """`claim_id` inventado grava marcação em caso que não existe."""
    assert interpretar(envelope_de_clique(value="")) is None
    assert interpretar(envelope_de_clique(value="abc")) is None


def test_envelope_id_vem_junto():
    assert interpretar(envelope_de_clique())["envelope_id"] == "env-1"


# --- nome de gente, não ID ------------------------------------------------

def test_display_name_ganha_do_username():
    u = {"id": "U1", "username": "maria",
         "profile": {"display_name": "Maria Souza"}}
    assert nome_de(u) == "Maria Souza"


def test_sem_display_name_usa_username():
    assert nome_de({"id": "U1", "username": "maria"}) == "maria"


def test_sem_nome_nenhum_devolve_o_id():
    """Melhor um ID do que um None na linha do tempo da Thayná."""
    assert nome_de({"id": "U1"}) == "U1"


def test_usuario_vazio_nao_vira_none():
    assert nome_de({}) is not None


# --- o modal de observação ------------------------------------------------

def test_modal_carrega_o_caso_no_metadata():
    """O modal volta num envelope separado, sem o card por perto. Sem o
    claim/channel/ts no private_metadata, a observação não sabe onde pousar."""
    m = modal_de_observacao(5552858975, "C1", "1754500000.1")
    meta = json.loads(m["private_metadata"])
    assert meta["claim_id"] == 5552858975
    assert meta["channel"] == "C1" and meta["ts"] == "1754500000.1"


def test_modal_tem_campo_de_texto():
    m = modal_de_observacao(1, "C1", "1.1")
    tipos = [b["element"]["type"] for b in m["blocks"] if "element" in b]
    assert "plain_text_input" in tipos


def test_modal_aceita_texto_longo():
    """Observação de SAC é relato, não etiqueta."""
    m = modal_de_observacao(1, "C1", "1.1")
    el = [b["element"] for b in m["blocks"] if "element" in b][0]
    assert el.get("multiline") is True


def test_modal_tem_titulo_curto():
    """O Slack recusa title acima de 24 caracteres — e recusa calado, o modal
    simplesmente não abre."""
    m = modal_de_observacao(1, "C1", "1.1")
    assert len(m["title"]["text"]) <= 24
    assert len(m["submit"]["text"]) <= 24


# --- o cofrinho reage ao desfecho, e só a ele ------------------------------
#
# Redesenhar o placar a cada "recebi" seria uma chamada ao Slack por clique
# sem nada mudar no número — e o rate limit de app não-Marketplace é de 1
# requisição por minuto por método. Mas um cofrinho que não mexe quando a
# Maria fecha o caso também não é um cofrinho: ela clica e não vê nada
# acontecer.

from socket_sac import deve_atualizar_cofrinho


def test_recusado_mexe_no_placar():
    assert deve_atualizar_cofrinho("recusado")


def test_reembolsado_mexe_no_placar():
    assert deve_atualizar_cofrinho("reembolsado")


def test_receber_nao_mexe_no_placar():
    assert not deve_atualizar_cofrinho("recebi")


def test_observacao_nao_mexe_no_placar():
    assert not deve_atualizar_cofrinho("observacao")


def test_finalizar_nao_conta_de_novo():
    """O dinheiro já foi contado em recusado/reembolsado. Contar no
    "finalizar" dobraria o caso no placar do mês."""
    assert not deve_atualizar_cofrinho("finalizar")


# --- quem clicou, para avisar só a essa pessoa -----------------------------
#
# Verificado na thread do card após o QA: sete avisos "⚠️ <@> 'recebi' não é
# uma ação possível" — menção vazia, porque `aplicar_clique` lia
# `evento["user_id"]` que `interpretar` nunca preencheu.
#
# Dois defeitos num: a menção não menciona ninguém, e o aviso vira mensagem
# pública na thread. Recusa de clique é assunto de quem clicou; publicar para
# o canal inteiro transforma um deslize em constrangimento, e enche a thread
# do card de ruído que a Maria vai ter que rolar.

def test_interpretar_traz_o_id_de_quem_clicou():
    assert interpretar(envelope_de_clique())["user_id"] == "U0BH1"


def test_sem_usuario_nao_inventa_id():
    e = dict(envelope_de_clique())
    e["payload"] = dict(e["payload"]); e["payload"].pop("user")
    assert interpretar(e)["user_id"] is None


# --- o listener tem que sobreviver ao próprio Slack ------------------------
#
# O Socket Mode não é uma conexão eterna: o Slack manda `disconnect` com
# `reason: refresh_requested` a cada ~1h e fecha o socket. É o mecanismo
# normal de rebalanceamento dele, não um erro.
#
# A primeira versão tratava isso como fim: o `async for` acabava, `escutar()`
# retornava 0 e o processo saía com SUCESSO. O listener morreria sozinho
# depois de uma hora, sem ninguém desligar nada, e o job ficaria VERDE no
# painel — que é o pior jeito de morrer. A Maria clicaria e veria "problemas
# de conexão", igual ao que aconteceu no ensaio do modal.

from socket_sac import deve_reconectar, eh_encerramento_limpo


def test_disconnect_do_slack_pede_reconexao():
    assert deve_reconectar({"type": "disconnect",
                            "reason": "refresh_requested"})


def test_disconnect_por_warning_tambem_reconecta():
    assert deve_reconectar({"type": "disconnect", "reason": "link_disabled"})


def test_evento_normal_nao_pede_reconexao():
    assert not deve_reconectar({"type": "events_api"})
    assert not deve_reconectar({"type": "hello"})


def test_socket_fechado_e_encerramento_limpo_so_no_fim_do_prazo():
    """Fim do prazo do job é saída planejada. Queda no meio é reconexão."""
    assert eh_encerramento_limpo(prazo_vencido=True)
    assert not eh_encerramento_limpo(prazo_vencido=False)


# --- envelope repetido não vira marcação repetida --------------------------
#
# O Slack reentrega envelope não confirmado, e com dois listeners rodando em
# sobreposição (que é como se consegue cobertura contínua no Actions) a mesma
# devolução pode chegar duas vezes. Sem chave, a linha do tempo da Thayná
# ganharia "Recebi o produto" duplicado — e uma observação apareceria duas
# vezes, porque anotar é sempre uma ação válida.

def test_envelope_id_viaja_com_o_evento():
    assert interpretar(envelope_de_clique(envelope_id="env-42"))[
        "envelope_id"] == "env-42"


# --- redundância ativa-ativa, como o Slack manda --------------------------
#
# Documentação oficial (docs.slack.dev/apis/events-api/using-socket-mode):
#
#   "Socket Mode allows your app to maintain up to 10 open WebSocket
#    connections at the same time."
#   "When multiple connections are active, each payload may be sent to ANY
#    of the connections."
#   "If you'd like to gracefully restart your app's services, you can use
#    multiple connections for temporary active-active redundancy."
#
# A primeira versão fechava a conexão ao receber `disconnect` e SÓ ENTÃO
# abria outra. Entre o fechar e o abrir cabe um `apps.connections.open`
# inteiro — e o Slack refresca "once every few hours", então era um buraco
# por conexão por hora, todo dia, para sempre.
#
# Não dá para provar aqui que nada se perde no buraco: a doc NÃO diz o que
# acontece com um payload quando há zero conexões abertas. É justamente por
# ser indocumentado que a gente não deixa o buraco existir.

from socket_sac import CONEXOES


def test_mantemos_mais_de_uma_conexao():
    """Uma só significa que todo refresh do Slack é um buraco."""
    assert CONEXOES >= 2


def test_respeitamos_o_teto_de_dez_do_slack():
    """Acima de 10 o Slack recusa — e recusar conexão é ficar com menos
    redundância do que se pediu, não com mais."""
    assert CONEXOES <= 10


def test_nao_exageramos_na_redundancia():
    """Cada conexão é um `apps.connections.open` e um socket vivo. Duas
    cobrem o refresh; dez seriam custo sem cobertura extra."""
    assert CONEXOES <= 3


# --- a coleta mora no listener, não num cron ------------------------------
#
# Medido em 07/08/2026: o card mostrava "a caminho" enquanto o Mercado Livre
# já dizia "entregue" há 2h40. Não era defeito de dado — era atraso, porque
# nenhum agendamento rodava a coleta. Ela só acontecia quando o dev a
# executava na mão. Esse é o defeito de independência, não de qualidade.
#
# A coleta vai para dentro do listener porque ele é o único processo que já
# fica 24/7 de pé e já tem o token do ML. Um agendamento separado seria mais
# uma coisa para cair calada — e foi exatamente assim que a tabela `orders`
# ficou 13 dias congelada em julho.

from socket_sac import COLETA_MIN


def test_a_coleta_roda_com_frequencia_util():
    """Acima de 15 min a Maria trabalha com informação de outro turno."""
    assert COLETA_MIN <= 15


def test_a_coleta_nao_martela_a_api():
    """Abaixo de 5 min são 288 ciclos/dia sem ganho — o estado de uma
    devolução não muda a cada 3 minutos."""
    assert COLETA_MIN >= 5
