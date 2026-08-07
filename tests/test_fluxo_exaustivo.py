"""Varredura exaustiva do fluxo — todos os caminhos, todas as combinações.

Os outros testes checam os degraus que a gente lembrou de escrever. Este
varre o grafo inteiro por construção: **toda** combinação estado × ação,
**todo** caminho possível do começo ao fim. O que estiver errado no desenho
aparece aqui sem alguém precisar ter previsto o caso.

Por que isso importa numa ferramenta que a Maria vai usar sozinha: um estado
sem saída trava um caso para sempre e ninguém descobre até o cliente ligar.
Um estado inalcançável é código morto que dá falsa sensação de cobertura. Uma
ação que funciona onde não devia é dinheiro marcado errado.

A varredura fecha quatro classes:

    1. estado × ação   — as 110 combinações, uma a uma
    2. caminhos        — os 12 percursos completos até "finalizado"
    3. propriedades    — sem beco sem saída, sem estado órfão, sem ciclo
    4. caos            — clique duplo, fora de ordem, lixo, corrida
"""
import itertools

import pytest

import sac_fluxo
from sac_fluxo import (
    ACOES,
    ESTADO_INICIAL,
    ESTADOS,
    acoes_de,
    aplicar,
    cofrinho,
    eh_terminal,
    estado_de,
    linha_da_timeline,
    rotulo_do_estado,
)

# O desenho da Thayná, escrito à mão aqui de propósito: se `_ESCADA` mudar
# sem alguém mexer nesta tabela, o teste acusa. Uma tabela derivada do código
# concordaria com qualquer bug.
PERMITIDO = {
    "a_caminho":     {"recebi"},
    "recebido":      {"estoque", "garantia"},
    "no_estoque":    {"mediacao", "sem_argumento"},
    "em_garantia":   {"mediacao", "sem_argumento"},
    "mediacao":      {"reembolsado", "recusado"},
    "sem_argumento": {"reembolsado", "recusado"},
    "reembolsado":   {"finalizar"},
    # O WhatsApp é SEGUNDA TENTATIVA: só existe depois da recusa.
    "recusado":      {"whatsapp", "finalizar"},
    # A segunda tentativa também termina em DECISÃO, não em "finalizar"
    # direto. E o desfecho é "sem acordo", não "recusado" de novo — voltar
    # criaria ciclo, e ciclo contaria o mesmo dinheiro duas vezes.
    "whatsapp":      {"reembolsado", "sem_acordo"},
    "sem_acordo":    {"finalizar"},
    "finalizado":    set(),
}

NEUTRAS = {"observacao", "supervisor"}


def ev(etapa, quando="2026-08-06T12:00:00-03:00"):
    return {"etapa": etapa, "quando": quando, "quem": "Maria",
            "observacao": None}


# --- 1. as 110 combinações estado × ação ----------------------------------

def test_o_grafo_tem_os_estados_que_ela_desenhou():
    assert set(ESTADOS) == set(PERMITIDO)


def test_o_grafo_tem_as_acoes_que_ela_desenhou():
    esperadas = set().union(*PERMITIDO.values()) | NEUTRAS
    assert set(ACOES) == esperadas


@pytest.mark.parametrize("estado,acao", list(itertools.product(
    sorted(PERMITIDO), sorted(set().union(*PERMITIDO.values()) | NEUTRAS))))
def test_cada_combinacao_estado_acao(estado, acao):
    """Uma linha por combinação: ou avança para onde deve, ou levanta.

    O meio-termo — passar calado sem mudar nada — é o que produz dinheiro
    marcado errado sem ninguém perceber.
    """
    if acao in NEUTRAS:
        assert aplicar(estado, acao) == estado
    elif acao in PERMITIDO[estado]:
        assert aplicar(estado, acao) in ESTADOS
    else:
        with pytest.raises(ValueError):
            aplicar(estado, acao)


@pytest.mark.parametrize("estado", sorted(PERMITIDO))
def test_os_botoes_batem_com_o_permitido(estado):
    """O que o card oferece é exatamente o que o fluxo aceita. Botão que
    aparece e não funciona é pior que botão ausente."""
    ids = {a["id"] for a in acoes_de(estado)}
    if eh_terminal(estado):
        assert ids == set()
    else:
        assert ids == PERMITIDO[estado] | NEUTRAS


@pytest.mark.parametrize("estado", sorted(PERMITIDO))
def test_todo_estado_tem_rotulo_legivel(estado):
    r = rotulo_do_estado(estado)
    assert r and "_" not in r and r != estado


# --- 2. todos os caminhos completos ---------------------------------------

def todos_os_caminhos(estado=ESTADO_INICIAL, visitados=()):
    """Enumera cada percurso possível até um estado sem saída."""
    saidas = sorted(PERMITIDO[estado])
    if not saidas:
        yield list(visitados)
        return
    for acao in saidas:
        yield from todos_os_caminhos(aplicar(estado, acao),
                                     tuple(visitados) + (acao,))


CAMINHOS = list(todos_os_caminhos())


def test_o_desenho_tem_dezesseis_caminhos():
    """2 (estoque|garantia) × 2 (mediação|sem argumento) × 4 desfechos:
    reembolsado→fim, recusado→fim, recusado→whatsapp→reembolsado→fim, e
    recusado→whatsapp→sem_acordo→fim. Se esse número mudar, o fluxo mudou —
    e mudar o fluxo da Maria sem avisar é o que quebra treinamento."""
    assert len(CAMINHOS) == 16


@pytest.mark.parametrize("caminho", CAMINHOS, ids=lambda c: "→".join(c))
def test_todo_caminho_chega_ao_fim(caminho):
    estado = ESTADO_INICIAL
    for acao in caminho:
        estado = aplicar(estado, acao)
    assert estado == "finalizado"


@pytest.mark.parametrize("caminho", CAMINHOS, ids=lambda c: "→".join(c))
def test_todo_caminho_reconstroi_o_mesmo_estado_pela_timeline(caminho):
    """`estado_de` (que lê o banco) tem que concordar com `aplicar` (que
    valida o clique). Divergir aqui é o card mostrar um degrau e o botão
    aceitar outro."""
    assert estado_de([ev(a) for a in caminho]) == "finalizado"


@pytest.mark.parametrize("caminho", CAMINHOS, ids=lambda c: "→".join(c))
def test_todo_caminho_fecha_um_dos_dois_cofrinhos(caminho):
    """Nenhum percurso pode terminar sem sinal: caso fechado que não conta
    para lado nenhum é dinheiro que some do placar."""
    assert cofrinho([ev(a) for a in caminho]) in ("positivo", "negativo")


@pytest.mark.parametrize("caminho", CAMINHOS, ids=lambda c: "→".join(c))
def test_o_sinal_do_cofrinho_segue_o_desfecho(caminho):
    esperado = "negativo" if "reembolsado" in caminho else "positivo"
    assert cofrinho([ev(a) for a in caminho]) == esperado


@pytest.mark.parametrize("caminho", CAMINHOS, ids=lambda c: "→".join(c))
def test_caminho_com_observacao_em_todo_degrau_nao_muda_nada(caminho):
    """A Maria pode anotar a qualquer momento. Se isso mexesse no percurso,
    ela perderia o degrau só por escrever."""
    t = []
    for acao in caminho:
        t += [ev("observacao"), ev(acao), ev("supervisor")]
    assert estado_de(t) == "finalizado"
    assert cofrinho(t) == ("negativo" if "reembolsado" in caminho
                           else "positivo")


# --- 3. propriedades do grafo ---------------------------------------------

def test_nenhum_estado_e_beco_sem_saida():
    """Estado sem saída que não seja o final trava um caso para sempre — e
    ninguém descobre até o cliente ligar."""
    for e in ESTADOS:
        if not eh_terminal(e):
            assert PERMITIDO[e], f"{e} não tem saída"


def test_todo_estado_e_alcancavel_do_inicio():
    """Estado órfão é código morto que dá falsa sensação de cobertura."""
    vistos, fila = {ESTADO_INICIAL}, [ESTADO_INICIAL]
    while fila:
        atual = fila.pop()
        for acao in PERMITIDO[atual]:
            prox = aplicar(atual, acao)
            if prox not in vistos:
                vistos.add(prox)
                fila.append(prox)
    assert vistos == set(ESTADOS)


def test_o_fluxo_nao_tem_ciclo():
    """Um ciclo permitiria fechar o mesmo caso duas vezes — e contar o
    dinheiro duas vezes no cofrinho."""
    for caminho in CAMINHOS:
        estados = [ESTADO_INICIAL]
        for acao in caminho:
            estados.append(aplicar(estados[-1], acao))
        assert len(estados) == len(set(estados)), estados


def test_so_existe_um_estado_terminal():
    sem_saida = [e for e in ESTADOS if not PERMITIDO[e]]
    assert sem_saida == ["finalizado"]


def test_o_caminho_curto_fecha_em_cinco_cliques():
    """O percurso comum — recebi, destino, tratativa, desfecho, finalizar."""
    assert min(len(c) for c in CAMINHOS) == 5


def test_a_segunda_tentativa_custa_no_maximo_dois_cliques_a_mais():
    """Só o retry do WhatsApp passa de cinco, e por decisão explícita. Se
    outro caminho crescer, o desenho inchou sem ninguém decidir."""
    assert max(len(c) for c in CAMINHOS) == 7
    longos = [c for c in CAMINHOS if len(c) > 5]
    assert all("whatsapp" in c for c in longos)


# --- 4. caos --------------------------------------------------------------

@pytest.mark.parametrize("caminho", CAMINHOS, ids=lambda c: "→".join(c))
def test_clique_duplo_em_todo_degrau_e_recusado(caminho):
    """Slack reentrega envelope não confirmado, e dedo escorrega. O segundo
    clique tem que bater na parede, não avançar mais um degrau."""
    estado = ESTADO_INICIAL
    for acao in caminho:
        estado = aplicar(estado, acao)
        with pytest.raises(ValueError):
            aplicar(estado, acao)


def test_marcacao_duplicada_no_banco_nao_conta_duas_vezes():
    """Se a reentrega escapar e gravar duas linhas, o estado tem que ficar
    onde está — e não pular um degrau."""
    t = [ev("recebi"), ev("recebi"), ev("estoque"), ev("estoque")]
    assert estado_de(t) == "no_estoque"


def test_card_velho_aberto_em_outra_aba_nao_desfaz_o_progresso():
    """Alguém deixa o Slack aberto de manhã, o caso avança, e à tarde clica
    no botão antigo. O clique é recusado; o caso não volta atrás."""
    t = [ev("recebi"), ev("estoque"), ev("mediacao")]
    estado = estado_de(t)
    with pytest.raises(ValueError):
        aplicar(estado, "recebi")
    assert estado_de(t) == "mediacao"


@pytest.mark.parametrize("lixo", [
    "", "  ", "RECEBI", "recebi ", "reembolsar", "drop table sac_timeline",
    "observação", "recebi;estoque", "🧨", "None", "null", "0",
])
def test_acao_que_nao_existe_nunca_avanca(lixo):
    """Nada que não esteja no desenho pode mover o caso — nem por acidente
    de maiúscula, espaço ou acento."""
    with pytest.raises(ValueError):
        aplicar("recebido", lixo)


@pytest.mark.parametrize("lixo", ["", "  ", "RECEBI", "🧨", "reembolsar"])
def test_evento_lixo_no_banco_e_ignorado_sem_derrubar(lixo):
    """Linha estranha no banco não pode derrubar o card de todo mundo."""
    t = [ev("recebi"), ev(lixo), ev("estoque")]
    assert estado_de(t) == "no_estoque"


def test_timeline_gigante_nao_explode():
    t = [ev("observacao")] * 5000 + [ev("recebi")]
    assert estado_de(t) == "recebido"


def test_timeline_com_evento_sem_campo_nenhum():
    assert estado_de([{}, ev("recebi")]) == "recebido"


def test_timeline_com_none_no_meio():
    assert estado_de([ev("recebi"), {"etapa": None}, ev("estoque")]) == "no_estoque"


def test_marcacao_sem_data_nao_derruba_a_linha_do_tempo():
    """Data ausente vira "sem data" — a marcação continua visível, porque
    sumir com ela é pior do que exibi-la sem hora."""
    l = linha_da_timeline({"etapa": "recebi", "quando": None, "quem": "Maria"})
    assert "sem data" in l and "None" not in l


def test_marcacao_com_data_quebrada_nao_derruba():
    l = linha_da_timeline({"etapa": "recebi", "quando": "ontem",
                           "quem": "Maria"})
    assert "Maria" in l and "None" not in l


def test_observacao_gigante_nao_quebra_a_linha():
    l = linha_da_timeline({"etapa": "observacao", "quando": None,
                           "quem": "Maria", "observacao": "x" * 5000})
    assert "x" * 100 in l


def test_observacao_com_markdown_do_slack_nao_some():
    """Cliente escreve `*urgente*` e a Maria precisa ler `*urgente*`."""
    l = linha_da_timeline({"etapa": "observacao", "quando": None,
                           "quem": "M", "observacao": "*urgente* <@U1>"})
    assert "*urgente*" in l


def test_ordem_de_chegada_manda_e_nao_a_ordem_alfabetica():
    """Duas marcações no mesmo segundo: quem decide é a ordem em que vieram
    do banco (`ORDER BY quando, id`), não o acaso."""
    mesmo = "2026-08-06T12:00:00-03:00"
    t = [ev("recebi", mesmo), ev("garantia", mesmo)]
    assert estado_de(t) == "em_garantia"


def test_reconstrucao_e_deterministica():
    """Mesma timeline, mesmo estado, sempre. Sem isso, o card mostra uma
    coisa agora e outra no próximo ciclo."""
    t = [ev(a) for a in CAMINHOS[0]]
    assert len({estado_de(t) for _ in range(50)}) == 1
