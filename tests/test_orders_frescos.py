"""`orders` velha é falha silenciosa — a bateria precisa gritar.

O QUE ACONTECEU (23/07 a 04/08/2026, 13 dias)
---------------------------------------------
A tabela `orders` congelou. O `ml_live_poll.py` rodava na máquina do Lucas e
morreu; nenhum workflow do GitHub tocava `orders` (o sync cuidava de claims e
CMV). Ninguém percebeu porque nada ficou vermelho.

A cadeia inteira desceu junto, em silêncio:
  - 47 de 86 devoluções de 24/07+ (55%) sem pedido em `orders`;
  - o coletor de saldos parte `FROM orders` → nunca enfileirou os novos;
  - o motor de estimativa parte `FROM orders` → idem;
  - sem saldo, o Slack rotulava "conciliação financeira pendente" enquanto a
    página do Mercado Livre já mostrava a venda fechada com o valor;
  - os painéis de margem somavam vendas pela metade.

`checar_coleta_saldos` já existia e cobrava a coleta. Faltava o irmão dela: a
tabela-raiz da fila também precisa de vigia. Sem isso, consertar `orders` uma
vez não impede que ela congele de novo — e a segunda vez também passaria
despercebida.

O limite é 2 dias, não 7: `orders` é alimentada pelo CI a cada 2h. Dois dias
sem venda nova já é anormal, e o custo de descobrir tarde é a cadeia inteira.
"""
import pytest

from confianca import checar_orders_frescos


def test_orders_de_hoje_nao_gera_achado():
    assert checar_orders_frescos(dias_desde_ultima_venda=0) is None


def test_orders_de_ontem_nao_gera_achado():
    """Fim de semana com pouca venda é normal."""
    assert checar_orders_frescos(dias_desde_ultima_venda=1) is None


def test_no_limite_ainda_passa():
    assert checar_orders_frescos(dias_desde_ultima_venda=2) is None


def test_tres_dias_gera_quebra():
    a = checar_orders_frescos(dias_desde_ultima_venda=3)
    assert a is not None and a.severidade == "quebra"
    assert "3" in a.evidencia


def test_o_caso_real_de_treze_dias():
    a = checar_orders_frescos(dias_desde_ultima_venda=13)
    assert a is not None and a.severidade == "quebra"
    assert "13" in a.evidencia


def test_o_achado_diz_a_acao_e_nao_so_o_problema():
    """Invariante que grita sem dizer o que fazer vira ruído ignorado."""
    a = checar_orders_frescos(dias_desde_ultima_venda=13)
    assert a.acao and len(a.acao) > 10
    assert "sync" in a.acao.lower() or "orders" in a.acao.lower()


def test_o_achado_explica_a_cadeia_que_cai_junto():
    """Quem lê o alerta precisa entender que não é só uma tabela atrasada."""
    a = checar_orders_frescos(dias_desde_ultima_venda=13)
    texto = (a.resumo + " " + a.evidencia).lower()
    assert "saldo" in texto or "fila" in texto or "cadeia" in texto


def test_tabela_vazia_ou_ilegivel_gera_achado():
    """None não pode virar 'tudo bem' — é justamente o caso mais grave."""
    a = checar_orders_frescos(dias_desde_ultima_venda=None)
    assert a is not None and a.severidade == "quebra"


def test_entra_na_bateria_geral():
    """Invariante que existe mas ninguém chama não protege nada."""
    import inspect

    import confianca
    fonte = inspect.getsource(confianca)
    chamadas = fonte.count("checar_orders_frescos")
    assert chamadas >= 2, "definida mas nunca chamada pela bateria"
