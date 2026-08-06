"""O fluxo que a Thayná desenhou — a máquina de estados do caso de SAC.

O desenho dela (06/08/2026) é uma escada, não uma lista:

    ① recebido → ② estoque | garantia
                → ③ mediação | whatsapp | sem argumento
                → ④ reembolsado | recusado
                → ⑤ finalizar

Com **data e hora em cada marcação**, campo de observação, e botão de
encaminhar ao supervisor.

Por que máquina de estados e não "cinco botões sempre visíveis": o card do
pós-venda do Meli mostra à Maria **só o que ela pode fazer agora**. Botão que
não faz sentido no estado atual é convite a erro — e erro aqui é dinheiro
marcado como reembolsado que não foi reembolsado.

Três invariantes que estes testes travam:

**1. Ação inválida falha alto.** Marcar "reembolsado" num caso que ainda está
a caminho tem que levantar, não passar calado. Um `pass` aqui vira número
errado no cofrinho e no balanço do mês.

**2. O estado sai da timeline, não de uma coluna.** A Thayná pediu data e hora
em cada marcação. Se o estado morasse numa coluna sobrescrita, a hora de cada
passo se perderia — e é justamente ela que responde "por que esse caso
demorou 9 dias?".

**3. O cofrinho só conta o que fechou.** Caso em mediação não é positivo nem
negativo: é indefinido. Contar antes da hora infla o número que vai para o
Gabriel.
"""
from datetime import datetime, timezone

import pytest

from sac_fluxo import (
    ESTADO_INICIAL,
    acoes_de,
    aplicar,
    cofrinho,
    eh_terminal,
    estado_de,
    linha_da_timeline,
    rotulo_do_estado,
)


def ev(etapa, quando="2026-08-06T12:14:00-03:00", quem="Maria", observacao=None):
    return {"etapa": etapa, "quando": quando, "quem": quem,
            "observacao": observacao}


# --- a escada, degrau por degrau ------------------------------------------

def test_comeca_a_caminho():
    assert ESTADO_INICIAL == "a_caminho"


def test_recebi_leva_a_recebido():
    assert aplicar("a_caminho", "recebi") == "recebido"


def test_recebido_vai_para_estoque():
    assert aplicar("recebido", "estoque") == "no_estoque"


def test_recebido_vai_para_garantia():
    assert aplicar("recebido", "garantia") == "em_garantia"


def test_do_estoque_abre_mediacao():
    assert aplicar("no_estoque", "mediacao") == "mediacao"


def test_da_garantia_tambem_abre_whatsapp():
    """A Thayná desenhou os dois caminhos chegando nas mesmas três saídas."""
    assert aplicar("em_garantia", "whatsapp") == "whatsapp"


def test_sem_argumento_e_uma_saida_valida():
    assert aplicar("no_estoque", "sem_argumento") == "sem_argumento"


def test_mediacao_termina_em_reembolso():
    assert aplicar("mediacao", "reembolsado") == "reembolsado"


def test_whatsapp_pode_terminar_recusado():
    assert aplicar("whatsapp", "recusado") == "recusado"


def test_finalizar_encerra():
    assert aplicar("reembolsado", "finalizar") == "finalizado"


# --- ação inválida falha ALTO ---------------------------------------------

def test_reembolsar_o_que_nem_chegou_levanta():
    """Sem isso, um clique fora de ordem vira dinheiro errado no balanço —
    calado, que é o pior jeito de errar."""
    with pytest.raises(ValueError):
        aplicar("a_caminho", "reembolsado")


def test_acao_inexistente_levanta():
    with pytest.raises(ValueError):
        aplicar("recebido", "teletransportar")


def test_caso_finalizado_nao_aceita_mais_nada():
    with pytest.raises(ValueError):
        aplicar("finalizado", "recebi")


def test_finalizado_e_terminal():
    assert eh_terminal("finalizado")
    assert not eh_terminal("recebido")


# --- observação e supervisor não movem o caso -----------------------------

def test_observacao_nao_muda_o_estado():
    """Anotar não é decidir. Se a observação avançasse o caso, a Maria
    perderia o degrau em que estava só por escrever um bilhete."""
    assert aplicar("recebido", "observacao") == "recebido"


def test_supervisor_nao_muda_o_estado():
    assert aplicar("mediacao", "supervisor") == "mediacao"


def test_observacao_vale_ate_no_final():
    assert aplicar("finalizado", "observacao") == "finalizado"


# --- os botões que aparecem em cada degrau --------------------------------

def test_a_caminho_so_oferece_receber():
    ids = [a["id"] for a in acoes_de("a_caminho")]
    assert "recebi" in ids
    assert "reembolsado" not in ids


def test_recebido_oferece_estoque_e_garantia():
    ids = [a["id"] for a in acoes_de("recebido")]
    assert "estoque" in ids and "garantia" in ids


def test_todo_estado_aberto_oferece_supervisor():
    """O botão de encaminhar é do desenho dela, e serve exatamente quando o
    caso travou — ou seja, em qualquer degrau."""
    for e in ("a_caminho", "recebido", "no_estoque", "mediacao"):
        assert "supervisor" in [a["id"] for a in acoes_de(e)]


def test_finalizado_nao_oferece_botao_de_acao():
    assert acoes_de("finalizado") == []


def test_todo_botao_tem_rotulo_em_portugues_claro():
    """A Maria não lê `sem_argumento`. Ela lê "Sem argumento"."""
    for e in ("a_caminho", "recebido", "no_estoque", "mediacao"):
        for a in acoes_de(e):
            assert a["rotulo"] and not a["rotulo"].islower()
            assert "_" not in a["rotulo"]


def test_nenhum_degrau_passa_de_cinco_botoes():
    """Cinco é o que cabe na largura do Slack sem quebrar feio — e é o limite
    do que alguém decide sem reler."""
    for e in ("a_caminho", "recebido", "no_estoque", "em_garantia",
              "mediacao", "whatsapp", "sem_argumento", "reembolsado"):
        assert len(acoes_de(e)) <= 5, e


# --- o estado sai da timeline ---------------------------------------------

def test_timeline_vazia_e_o_estado_inicial():
    assert estado_de([]) == "a_caminho"


def test_estado_e_o_ultimo_degrau_marcado():
    t = [ev("recebi"), ev("estoque"), ev("mediacao")]
    assert estado_de(t) == "mediacao"


def test_observacao_no_meio_nao_desfaz_o_progresso():
    """Escrever um bilhete depois de abrir mediação não devolve o caso para
    "recebido"."""
    t = [ev("recebi"), ev("estoque"), ev("mediacao"), ev("observacao")]
    assert estado_de(t) == "mediacao"


def test_timeline_fora_de_ordem_nao_inventa_estado():
    """Se um evento impossível entrou no banco, o estado para no último
    degrau válido — não pula nem apaga."""
    t = [ev("recebi"), ev("reembolsado")]
    assert estado_de(t) == "recebido"


# --- a linha que a Maria lê no card ---------------------------------------

def test_linha_tem_data_e_hora():
    """A Thayná pediu data e hora em cada marcação, com todas as letras."""
    l = linha_da_timeline(ev("recebi"))
    assert "06/08" in l and "12:14" in l


def test_linha_diz_quem_marcou():
    assert "Maria" in linha_da_timeline(ev("recebi"))


def test_linha_usa_o_nome_humano_da_etapa():
    assert "ecebi" in linha_da_timeline(ev("recebi")).lower()


def test_linha_mostra_a_observacao():
    l = linha_da_timeline(ev("observacao", observacao="cliente sumiu"))
    assert "cliente sumiu" in l


def test_linha_sem_quem_nao_escreve_none():
    assert "None" not in linha_da_timeline(ev("recebi", quem=None))


def test_rotulo_do_estado_e_legivel():
    assert rotulo_do_estado("no_estoque").lower().startswith("no estoque")
    assert "_" not in rotulo_do_estado("sem_argumento")


# --- cofrinho: só conta o que fechou --------------------------------------

def test_reembolsado_e_negativo():
    """Dinheiro que saiu."""
    assert cofrinho([ev("recebi"), ev("estoque"), ev("mediacao"),
                     ev("reembolsado")]) == "negativo"


def test_recusado_e_positivo():
    """Venda que ficou de pé."""
    assert cofrinho([ev("recebi"), ev("estoque"), ev("mediacao"),
                     ev("recusado")]) == "positivo"


def test_caso_em_andamento_nao_entra_no_cofrinho():
    """Mediação aberta não é ganho nem perda. Contar antes da hora infla o
    número que vai para o Gabriel."""
    assert cofrinho([ev("recebi"), ev("estoque"), ev("mediacao")]) is None


def test_caso_finalizado_mantem_o_sinal_do_desfecho():
    t = [ev("recebi"), ev("estoque"), ev("mediacao"), ev("recusado"),
         ev("finalizar")]
    assert cofrinho(t) == "positivo"
