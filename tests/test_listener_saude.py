"""A janela sem ninguém escutando — medida, não estimada.

Eu escrevi para o Lucas que "hoje a janela é de segundos e só na troca". Não
tinha medido nada. É exatamente o tipo de frase que a casa proíbe: premissa
sobre comportamento externo sem evidência.

O que se mede aqui: enquanto vivo, cada listener bate um pulso no banco. Uma
LACUNA é qualquer intervalo em que NENHUM listener bateu. Isso captura o que
importa e não depende de o processo conseguir avisar que morreu — processo
que leva SIGKILL não avisa nada; ele simplesmente para de bater.

Por que a lacuna é cara: o Slack não reenvia interação. Cada segundo sem
ninguém conectado é um clique que a Maria dá e some — e o que ela vê é
"Tivemos alguns problemas de conexão", que foi exatamente o que aconteceu no
ensaio do modal.

A conta tem que somar as batidas de TODAS as instâncias numa linha só. Com
runs sobrepostos, o listener A morrendo enquanto o B já bate não é lacuna
nenhuma — e é justamente essa sobreposição que a gente está comprando.
"""
from datetime import datetime, timedelta, timezone

import pytest

from listener_saude import TOLERANCIA_S, disponibilidade, lacunas, resumo

BRT = timezone(timedelta(hours=-3))


def t(minuto, segundo=0):
    return datetime(2026, 8, 6, 10, minuto, segundo, tzinfo=BRT)


def batidas(*pares):
    """(instancia, minuto) -> linhas como o banco devolve."""
    return [{"instancia": i, "quando": t(m)} for i, m in pares]


INICIO, FIM = t(0), t(10)


# --- o caso feliz e o caso vazio ------------------------------------------

def test_sem_batida_nenhuma_a_janela_inteira_e_lacuna():
    """Ninguém escutando o tempo todo. Se isso não aparecer como lacuna, o
    medidor está mentindo do jeito mais perigoso: para o lado bom."""
    ls = lacunas([], INICIO, FIM)
    assert len(ls) == 1
    assert ls[0]["segundos"] == pytest.approx(600)


def test_batidas_continuas_nao_tem_lacuna():
    b = batidas(*[("A", m) for m in range(11)])
    assert lacunas(b, INICIO, FIM) == []


def test_buraco_no_meio_vira_lacuna_com_duracao():
    b = batidas(("A", 0), ("A", 1), ("A", 2), ("A", 7), ("A", 8), ("A", 9),
                ("A", 10))
    ls = lacunas(b, INICIO, FIM)
    assert len(ls) == 1
    assert ls[0]["segundos"] == pytest.approx(300)


# --- a tolerância --------------------------------------------------------

def test_intervalo_dentro_da_tolerancia_nao_e_lacuna():
    """A batida é periódica; um atraso de rede entre dois pulsos não é
    ausência de listener. Contar isso como queda encheria o relatório de
    ruído e ninguém olharia o que importa."""
    seg = TOLERANCIA_S - 1
    b = [{"instancia": "A", "quando": INICIO},
         {"instancia": "A", "quando": INICIO + timedelta(seconds=seg)}]
    assert lacunas(b, INICIO, INICIO + timedelta(seconds=seg)) == []


def test_intervalo_acima_da_tolerancia_e_lacuna():
    seg = TOLERANCIA_S + 30
    b = [{"instancia": "A", "quando": INICIO},
         {"instancia": "A", "quando": INICIO + timedelta(seconds=seg)}]
    ls = lacunas(b, INICIO, INICIO + timedelta(seconds=seg))
    assert len(ls) == 1


def test_tolerancia_e_maior_que_o_intervalo_da_batida():
    """Se a tolerância fosse menor ou igual ao pulso, TODA batida normal
    apareceria como lacuna."""
    from listener_saude import INTERVALO_S
    assert TOLERANCIA_S > INTERVALO_S


# --- a sobreposição é o mecanismo, e a conta tem que enxergar isso --------

def test_dois_listeners_sobrepostos_nao_deixam_lacuna():
    """A morre no minuto 5, B já batia desde o 4. Cobertura contínua — é
    exatamente isso que os runs sobrepostos no Actions compram."""
    b = batidas(("A", 0), ("A", 1), ("A", 2), ("A", 3), ("A", 4), ("A", 5),
                ("B", 4), ("B", 5), ("B", 6), ("B", 7), ("B", 8), ("B", 9),
                ("B", 10))
    assert lacunas(b, INICIO, FIM) == []


def test_troca_sem_sobreposicao_deixa_lacuna():
    """A morre no 3, B só sobe no 7. Quatro minutos de cliques perdidos."""
    b = batidas(("A", 0), ("A", 1), ("A", 2), ("A", 3),
                ("B", 7), ("B", 8), ("B", 9), ("B", 10))
    ls = lacunas(b, INICIO, FIM)
    assert len(ls) == 1 and ls[0]["segundos"] == pytest.approx(240)


def test_batidas_fora_de_ordem_nao_inventam_lacuna():
    """O banco devolve ordenado, mas depender disso é frágil."""
    b = batidas(("B", 2), ("A", 0), ("B", 3), ("A", 1), ("A", 4))
    assert lacunas(b, INICIO, t(4)) == []


def test_duas_instancias_no_mesmo_instante_nao_duplicam_nada():
    b = batidas(("A", 0), ("B", 0), ("A", 1), ("B", 1))
    assert lacunas(b, INICIO, t(1)) == []


# --- as bordas da janela contam -------------------------------------------

def test_lacuna_no_comeco_da_janela_conta():
    """O relatório de 24h começa antes do primeiro run do dia. Ignorar a
    borda esconderia justamente a madrugada."""
    b = batidas(("A", 5), ("A", 6), ("A", 7), ("A", 8), ("A", 9), ("A", 10))
    ls = lacunas(b, INICIO, FIM)
    assert len(ls) == 1 and ls[0]["segundos"] == pytest.approx(300)


def test_lacuna_no_fim_da_janela_conta():
    """Listener que morreu e não voltou é o pior caso — e é o que some se a
    conta parar na última batida."""
    b = batidas(("A", 0), ("A", 1), ("A", 2))
    ls = lacunas(b, INICIO, FIM)
    assert len(ls) == 1 and ls[0]["segundos"] == pytest.approx(480)


def test_lacuna_no_comeco_e_no_fim_sao_duas():
    b = batidas(("A", 4), ("A", 5), ("A", 6))
    assert len(lacunas(b, INICIO, FIM)) == 2


# --- o resumo que vai para o relatório ------------------------------------

def test_resumo_soma_o_tempo_fora_do_ar():
    b = batidas(("A", 0), ("A", 1), ("A", 6), ("A", 7), ("A", 8), ("A", 9),
                ("A", 10))
    r = resumo(lacunas(b, INICIO, FIM), INICIO, FIM)
    assert r["n"] == 1
    assert r["total_s"] == pytest.approx(300)
    assert r["maior_s"] == pytest.approx(300)


def test_disponibilidade_e_percentual_do_tempo_coberto():
    assert disponibilidade(300, 600) == pytest.approx(50.0)


def test_disponibilidade_cheia_quando_nao_ha_lacuna():
    assert disponibilidade(0, 600) == 100.0


def test_disponibilidade_nao_arredonda_para_cem():
    """99,9% e 100% dizem coisas diferentes. Arredondar transforma "caiu uma
    vez" em "nunca caiu" — e aí ninguém investiga."""
    d = disponibilidade(1, 86400)
    assert d < 100.0


def test_janela_de_duracao_zero_nao_divide_por_zero():
    assert disponibilidade(0, 0) == 100.0


def test_resumo_sem_lacuna_nenhuma():
    r = resumo([], INICIO, FIM)
    assert r["n"] == 0 and r["total_s"] == 0 and r["disponibilidade"] == 100.0


# --- o pulso sai do Neon ---------------------------------------------------
#
# Medido em 07/08/2026: o Neon Free dá 100 CU-hours/mês. O pulso de 30s segura
# conexão aberta 24/7, o que impede o autosuspend — 730h × 0,25 CU = 182
# CU-hours, 82% ACIMA do teto. Estourar suspende o banco INTEIRO até o mês
# seguinte: painel, jobs, listener, tudo.
#
# A medição existia no Neon porque runs do GitHub são efêmeros e vários, e o
# banco era o único lugar compartilhado. Com um VPS só isso deixa de ser
# verdade: o pulso vira arquivo local, custa zero CU-hour, e mede igual.

import json

from listener_saude import bater_arquivo, ler_arquivo, podar


def test_batida_vai_para_o_arquivo(tmp_path):
    a = tmp_path / "pulso.jsonl"
    bater_arquivo(a, "vps-1")
    b = ler_arquivo(a)
    assert len(b) == 1 and b[0]["instancia"] == "vps-1"


def test_batidas_acumulam(tmp_path):
    a = tmp_path / "pulso.jsonl"
    for _ in range(5):
        bater_arquivo(a, "vps-1")
    assert len(ler_arquivo(a)) == 5


def test_arquivo_inexistente_nao_explode(tmp_path):
    """Primeira execução não tem arquivo. Devolver vazio é certo; levantar
    faria o relatório morrer no dia da estreia."""
    assert ler_arquivo(tmp_path / "nao-existe.jsonl") == []


def test_pasta_e_criada_sozinha(tmp_path):
    a = tmp_path / "fundo" / "do" / "poco" / "pulso.jsonl"
    bater_arquivo(a, "vps-1")
    assert a.exists()


def test_linha_corrompida_nao_derruba_o_relatorio(tmp_path):
    """Queda de energia no meio de um append deixa linha pela metade. Uma
    linha ruim não pode apagar a medição inteira."""
    a = tmp_path / "pulso.jsonl"
    bater_arquivo(a, "vps-1")
    with a.open("a", encoding="utf-8") as f:
        f.write('{"instancia": "vps-1", "quan\n')
    bater_arquivo(a, "vps-1")
    assert len(ler_arquivo(a)) == 2


def test_batida_tem_fuso(tmp_path):
    """Sem fuso, `lacunas` compara datetime naive com aware e levanta."""
    a = tmp_path / "pulso.jsonl"
    bater_arquivo(a, "vps-1")
    assert ler_arquivo(a)[0]["quando"].tzinfo is not None


def test_poda_corta_o_que_e_velho(tmp_path):
    a = tmp_path / "pulso.jsonl"
    velho = '{"instancia":"x","quando":"2020-01-01T00:00:00+00:00"}\n'
    a.write_text(velho * 3, encoding="utf-8")
    bater_arquivo(a, "vps-1")
    assert podar(a, dias=30) == 3
    assert len(ler_arquivo(a)) == 1


def test_poda_em_arquivo_limpo_nao_faz_nada(tmp_path):
    a = tmp_path / "pulso.jsonl"
    bater_arquivo(a, "vps-1")
    assert podar(a, dias=30) == 0
    assert len(ler_arquivo(a)) == 1


# --- o alerta que grita sozinho -------------------------------------------
#
# Hoje o relatório de saúde só existe se alguém rodar. Em setembro não vai
# ter ninguém para rodar. O monitoramento precisa gritar no Slack por conta
# própria — e o desenho do alerta é onde monitoramento morre:
#
# Alerta demais vira ruído, e ruído vira gente ignorando o canal. Alerta de
# menos é o silêncio que a gente já tem. O ponto é gritar POUCO e CERTO.

from listener_saude import deve_gritar, texto_do_alerta


def test_sem_lacuna_nao_grita():
    assert not deve_gritar(total_s=0, maior_s=0, ja_avisado=False)


def test_piscada_curta_nao_grita():
    """Um pulso perdido por atraso de rede não é queda. Gritar nisso ensina
    a ignorar o canal."""
    assert not deve_gritar(total_s=100, maior_s=100, ja_avisado=False)


def test_queda_de_verdade_grita():
    assert deve_gritar(total_s=1800, maior_s=1800, ja_avisado=False)


def test_nao_repete_o_mesmo_alerta():
    """Checagem de hora em hora com a mesma queda mandaria 24 mensagens por
    dia sobre o mesmo fato."""
    assert not deve_gritar(total_s=1800, maior_s=1800, ja_avisado=True)


def test_queda_longa_grita_mesmo_ja_avisado():
    """10h fora do ar não é o mesmo fato de 30min. Piorou muito, avisa de
    novo."""
    assert deve_gritar(total_s=36000, maior_s=36000, ja_avisado=True)


def test_muitas_quedas_curtas_somam_e_gritam():
    """Vinte piscadas de 2min não aparecem no `maior`, mas 40min fora do ar
    num dia é queda."""
    assert deve_gritar(total_s=2400, maior_s=120, ja_avisado=False)


def test_alerta_diz_quanto_tempo_e_o_que_fazer():
    t = texto_do_alerta(total_s=1800, maior_s=1800, horas=24)
    assert "30min" in t
    assert "runbook" in t.lower() or "fazer" in t.lower()


def test_alerta_nao_usa_jargao():
    """Quem lê é a Thayná, não um dev."""
    t = texto_do_alerta(total_s=1800, maior_s=1800, horas=24).lower()
    for palavra in ("socket", "websocket", "systemd", "daemon", "cu-hour",
                    "timeout", "exception"):
        assert palavra not in t


def test_alerta_diz_o_efeito_pratico():
    """Número sem consequência não move ninguém."""
    t = texto_do_alerta(total_s=1800, maior_s=1800, horas=24).lower()
    assert "clique" in t or "botão" in t or "botao" in t
