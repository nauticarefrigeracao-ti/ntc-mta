"""O canal onde o chefe acompanha o que está sendo feito, sem jargão.

Pedido em 05/08/2026, durante a reunião de conciliação: um lugar onde a
diretoria vê o que está em andamento, o que ficou pronto e quanto tempo levou
— em português de gente, não de programador.

Quem lê é o Gabriel e a Thayná. Eles não sabem (nem precisam saber) o que é
`shipment_id`, invariante ou paginação. Precisam saber: **o que melhorou pra
empresa, desde quando, e se já está no ar.**

Três regras que os testes garantem:

1. **Nada de jargão.** Palavra técnica no texto é falha de teste, não questão
   de estilo. Se o chefe precisa perguntar o que significa, a atualização não
   cumpriu o papel.
2. **Tempo em linguagem humana.** "2h30" e não "9000s"; "3 dias" e não
   "72:00:00".
3. **Toda entrega diz o que mudou para o negócio.** "Corrigido bug no
   agregador" não informa nada. "O prejuízo do mês estava R$ 144 maior do que
   é de verdade" informa.
"""
import pytest

from andamento import (
    JARGAO,
    fmt_duracao_humana,
    linha_de_item,
    montar_atualizacao,
)


def item(**kw):
    base = {"titulo": "Conferência de julho contra o Mercado Livre",
            "estado": "pronto",
            "ganho": "O prejuízo do mês estava R$ 144,15 maior do que é de "
                     "verdade; agora bate com o painel do Mercado Livre",
            "minutos": 150}
    base.update(kw)
    return base


# --- tempo em linguagem de gente ------------------------------------------

def test_minutos_viram_horas_e_minutos():
    assert fmt_duracao_humana(150) == "2h30"


def test_menos_de_uma_hora_fica_em_minutos():
    assert fmt_duracao_humana(45) == "45 minutos"


def test_um_minuto_no_singular():
    assert fmt_duracao_humana(1) == "1 minuto"


def test_mais_de_um_dia_vira_dias():
    assert fmt_duracao_humana(60 * 26) == "1 dia e 2h"


def test_zero_nao_vira_vazio():
    assert fmt_duracao_humana(0) == "menos de 1 minuto"


def test_duracao_desconhecida_e_declarada():
    """Sem tempo medido, "0" mentiria dizendo que foi instantâneo."""
    assert fmt_duracao_humana(None) == "em andamento"


# --- sem jargão ------------------------------------------------------------

def test_linha_pronta_nao_tem_jargao():
    texto = linha_de_item(item()).lower()
    for palavra in JARGAO:
        assert palavra not in texto


def test_atualizacao_inteira_nao_tem_jargao():
    texto = montar_atualizacao([item(), item(estado="fazendo", minutos=None)],
                               "05/08/2026").lower()
    for palavra in JARGAO:
        assert palavra not in texto, palavra


def test_a_lista_de_jargao_cobre_o_que_ja_vazou():
    """Palavras que já apareceram em mensagem lida pela diretoria."""
    for p in ("commit", "deploy", "bug", "api", "endpoint", "query"):
        assert p in JARGAO


# --- o que a linha precisa dizer ------------------------------------------

def test_linha_diz_o_ganho_para_o_negocio():
    assert "R$ 144,15" in linha_de_item(item())


def test_linha_diz_o_tempo():
    assert "2h30" in linha_de_item(item())


def test_item_em_andamento_nao_finge_estar_pronto():
    texto = linha_de_item(item(estado="fazendo", minutos=None)).lower()
    assert "pronto" not in texto


def test_item_pronto_e_marcado_como_no_ar():
    assert "no ar" in linha_de_item(item()).lower()


def test_item_sem_ganho_declarado_falha_alto():
    """"Ajustes internos" não é atualização, é ruído. Sem dizer o que mudou
    para a empresa, a mensagem não deve sair."""
    with pytest.raises(ValueError):
        linha_de_item(item(ganho=""))


# --- a mensagem ------------------------------------------------------------

def test_atualizacao_separa_pronto_de_andamento():
    texto = montar_atualizacao([item(), item(titulo="Outra", estado="fazendo",
                                             minutos=None)], "05/08/2026")
    assert texto.index("Pronto") < texto.index("Em andamento")


def test_atualizacao_sem_nada_pronto_nao_inventa_secao():
    texto = montar_atualizacao([item(estado="fazendo", minutos=None)],
                               "05/08/2026")
    assert "Pronto" not in texto


def test_atualizacao_vazia_diz_isso():
    texto = montar_atualizacao([], "05/08/2026")
    assert "nada" in texto.lower() or "sem" in texto.lower()


def test_atualizacao_traz_a_data():
    assert "05/08/2026" in montar_atualizacao([item()], "05/08/2026")
