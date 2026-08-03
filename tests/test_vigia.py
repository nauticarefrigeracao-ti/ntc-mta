"""O cron do GitHub não entrega o que promete — o run tem que se virar.

Medição em 03/08/2026, 60 runs do notificador (30/07 12:17 → 03/08 11:24):

    cron pedido      5 min
    intervalo médio  97 min
    menor / maior    52 min / 219 min
    runs por hora    0,6  (esperado: 12)

Nenhum intervalo ficou abaixo de 30 minutos. Ou seja: a latência de ~25 min
que prometemos ao negócio nunca existiu — na prática a Maria era avisada
entre 1h e 3h40 depois. Cron de alta frequência é pedido, não contrato, e o
GitHub despriorija justamente os mais frequentes.

A saída não é insistir no cron: é um run que VIVE e faz o ciclo por dentro,
a cada 5 minutos, por horas. O repo é público, então minuto de Actions é
livre — o que era caro em plano pago aqui não custa nada.

O que estes testes travam:
  - a janela é respeitada (o runner mata o job em 6h; estourar perde o ciclo);
  - falha de um ciclo NÃO mata os seguintes — senão um erro às 00h10 deixa a
    Maria sem aviso até as 6h;
  - mas falha também não é engolida: o run termina vermelho (CLAUDE.md,
    "falhar alto");
  - a última espera nunca ultrapassa o prazo.
"""
import pytest

from vigia import vigiar


class Relogio:
    """Tempo controlado — teste de laço não pode depender de relógio real."""

    def __init__(self):
        self.t = 0.0

    def agora(self) -> float:
        return self.t

    def dormir(self, seg: float) -> None:
        self.t += seg


def test_roda_pelo_menos_um_ciclo_mesmo_com_janela_zerada():
    """Janela curta não pode virar run que não faz nada e sai verde."""
    r, n = Relogio(), []
    vigiar(minutos=0, intervalo_seg=300, ciclo=lambda: n.append(1),
           agora=r.agora, dormir=r.dormir)
    assert len(n) == 1


def test_a_janela_manda_no_numero_de_ciclos():
    r, n = Relogio(), []
    # 30 min de janela, ciclo a cada 5 min -> 1 imediato + 6 = 7
    vigiar(minutos=30, intervalo_seg=300, ciclo=lambda: n.append(1),
           agora=r.agora, dormir=r.dormir)
    assert len(n) == 7


def test_nao_dorme_alem_do_prazo():
    """O runner mata o job em 6h. Dormir 5 min quando faltam 2 é jogar fora
    o encerramento limpo do run."""
    r = Relogio()
    vigiar(minutos=12, intervalo_seg=300, ciclo=lambda: None,
           agora=r.agora, dormir=r.dormir)
    assert r.t <= 12 * 60


def test_ciclo_que_falha_nao_derruba_os_seguintes():
    """Um erro às 00h10 não pode deixar a Maria sem aviso até as 6h."""
    r, n = Relogio(), []

    def ciclo():
        n.append(1)
        if len(n) == 2:
            raise RuntimeError("Neon dormiu")

    vigiar(minutos=20, intervalo_seg=300, ciclo=ciclo,
           agora=r.agora, dormir=r.dormir)
    assert len(n) == 5, "o laço tinha que continuar depois da falha"


def test_falha_deixa_o_run_vermelho():
    """Continuar rodando não é o mesmo que fingir que deu certo."""
    r = Relogio()

    def ciclo():
        raise RuntimeError("Neon dormiu")

    codigo = vigiar(minutos=10, intervalo_seg=300, ciclo=ciclo,
                    agora=r.agora, dormir=r.dormir)
    assert codigo == 1


def test_tudo_ok_sai_verde():
    r = Relogio()
    assert vigiar(minutos=10, intervalo_seg=300, ciclo=lambda: None,
                  agora=r.agora, dormir=r.dormir) == 0


def test_falha_isolada_no_meio_ainda_deixa_vermelho():
    """Sucesso posterior não apaga a falha: o ciclo que não rodou não rodou."""
    r, n = Relogio(), []

    def ciclo():
        n.append(1)
        if len(n) == 2:
            raise RuntimeError("timeout")

    assert vigiar(minutos=20, intervalo_seg=300, ciclo=ciclo,
                  agora=r.agora, dormir=r.dormir) == 1


def test_interrupcao_encerra_limpo():
    """Quando o GitHub cancela o run (novo deploy, cancelamento manual), o
    encerramento é esperado — não é defeito para ficar vermelho."""
    r = Relogio()

    def ciclo():
        raise KeyboardInterrupt

    assert vigiar(minutos=60, intervalo_seg=300, ciclo=ciclo,
                  agora=r.agora, dormir=r.dormir) == 0


def test_a_janela_padrao_cabe_no_limite_do_runner():
    """O job do GitHub morre em 6h. A janela padrão tem que terminar antes,
    senão o run é morto no meio de um envio."""
    import vigia
    assert vigia.MINUTOS_PADRAO < 6 * 60
    assert vigia.MINUTOS_PADRAO >= 300, "janela curta demais reabre o buraco do cron"


def test_a_cadencia_padrao_e_a_prometida_ao_negocio():
    import vigia
    assert vigia.INTERVALO_PADRAO_SEG == 300


def test_o_workflow_chama_o_vigia_e_nao_o_once():
    """Regressão: se alguém voltar o workflow para `--once`, a latência volta
    para ~97 min sem que nada fique vermelho. O gate tem que ser aqui."""
    from pathlib import Path
    wf = (Path(__file__).parent.parent / ".github" / "workflows" /
          "notify_slack.yml").read_text(encoding="utf-8")
    assert "vigia.py" in wf
    assert "slack_notify.py --once" not in wf


def test_o_timeout_do_job_cabe_a_janela_do_vigia():
    """timeout-minutes menor que a janela mata o run no meio de um envio."""
    import re
    from pathlib import Path

    import vigia
    wf = (Path(__file__).parent.parent / ".github" / "workflows" /
          "notify_slack.yml").read_text(encoding="utf-8")
    m = re.search(r"timeout-minutes:\s*(\d+)", wf)
    assert m, "job sem timeout-minutes: o default de 6h corta no meio"
    assert int(m.group(1)) >= vigia.MINUTOS_PADRAO
