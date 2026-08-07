"""O push criou run? — o detector do run que nunca nasceu.

Em 06/08/2026 o GitHub teve incidente crítico de 10h42 e, no texto oficial,
"many push and pull request events are not triggering workflow runs". Quatro
commits foram para `main` e nenhum rodou o CI.

Ninguém percebeu por quase um dia. **Um run que não nasce não aparece em
lugar nenhum** — não há vermelho, não há aviso, só ausência. E ausência
parece sucesso, que é o pior modo de falha que existe.
"""
import pytest

from scripts.conferir_ci import contar_runs, diagnostico


def test_run_criado_e_ok():
    assert contar_runs({"total_count": 2}) == 2
    ok, msg = diagnostico("abc1234def", 2)
    assert ok and "abc1234" in msg


def test_zero_run_alerta():
    ok, msg = diagnostico("abc1234def", 0)
    assert not ok
    assert "NAO criou run" in msg


def test_resposta_estranha_conta_como_zero():
    """Errar para o lado de "não rodou" é o certo: um alerta a mais custa uma
    conferida; um a menos custa código sem teste em produção."""
    assert contar_runs(None) == 0
    assert contar_runs({}) == 0
    assert contar_runs({"total_count": "nao é número"}) == 0
    assert contar_runs({"outra_coisa": 3}) == 0


def test_alerta_manda_olhar_o_status_do_github():
    """A causa mais provável não é nossa. Sem esse ponteiro, alguém vai caçar
    defeito no próprio código por horas."""
    _, msg = diagnostico("abc", 0)
    assert "githubstatus" in msg


def test_alerta_diz_como_disparar_na_mao():
    _, msg = diagnostico("abc", 0)
    assert "gh workflow run" in msg


def test_alerta_diz_a_consequencia():
    """Número sem consequência não move ninguém a agir."""
    _, msg = diagnostico("abc", 0)
    assert "SEM teste" in msg
