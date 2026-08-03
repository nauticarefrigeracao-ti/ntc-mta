"""Vigia: um run que vive e faz o ciclo por dentro, em vez de pedir favor ao cron.

POR QUE ISTO EXISTE
-------------------
O workflow do notificador pede `cron: */5`. Medição de 60 runs (30/07 12:17 →
03/08 11:24, fuso UTC):

    intervalo médio  97 min      menor  52 min      maior  219 min
    runs por hora   0,6                 esperado    12

Nenhum intervalo ficou abaixo de 30 minutos. A latência de ~25 min que
prometemos ao negócio nunca aconteceu: a Maria era avisada entre 1h e 3h40
depois do fato. O GitHub trata `schedule` como melhor-esforço e despriorija
os crons mais frequentes — insistir em `*/5` é insistir num pedido que não
é atendido.

A saída: o cron passa a servir só de PARTIDA. Cada run entra num laço de
~5h30 executando o ciclo a cada 5 minutos. Como o repo é público, minuto de
Actions não é cobrado — o que seria caro num plano pago aqui sai de graça.

DUAS DECISÕES QUE VALEM SER DITAS
---------------------------------
1. **Falha de um ciclo não mata o laço.** Um `OperationalError` às 00h10
   deixaria a Maria sem aviso até as 6h. O ciclo seguinte tenta de novo.

2. **Mas falha não é engolida.** O erro vai para stderr com hora e motivo, e
   o run termina com código 1 — fica vermelho no painel do Actions. Continuar
   trabalhando não é o mesmo que fingir que deu certo (CLAUDE.md: falhar alto).

Uso:
    python vigia.py                          # notificador, 5h30, ciclo de 5 min
    python vigia.py --minutos 20             # janela curta, para conferir
    python vigia.py --canal "#sac-teste"
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Callable, Optional

# Job do GitHub morre em 6h. 5h30 encerra limpo antes disso e ainda cobre o
# pior intervalo de cron já medido (3h39) com folga.
MINUTOS_PADRAO = 330

# A cadência que o negócio recebeu como promessa. Mudar aqui muda a promessa.
INTERVALO_PADRAO_SEG = 300


def vigiar(
    minutos: int = MINUTOS_PADRAO,
    intervalo_seg: int = INTERVALO_PADRAO_SEG,
    ciclo: Optional[Callable[[], object]] = None,
    agora: Optional[Callable[[], float]] = None,
    dormir: Optional[Callable[[float], None]] = None,
    ao_falhar: Optional[Callable[[BaseException], None]] = None,
) -> int:
    """Executa `ciclo` a cada `intervalo_seg` durante `minutos`.

    Retorna o código de saída: 0 se todo ciclo passou, 1 se algum falhou.
    `agora`/`dormir` são injetáveis para que o teste não dependa do relógio.
    """
    agora = agora or time.monotonic
    dormir = dormir or time.sleep
    if ciclo is None:
        raise ValueError("vigiar precisa de um ciclo para executar")

    inicio = agora()
    prazo = inicio + minutos * 60
    falhou = False

    while True:
        try:
            ciclo()
        except KeyboardInterrupt:
            # Cancelamento do run (deploy novo, cancelamento manual) é
            # encerramento esperado, não defeito.
            return 1 if falhou else 0
        except Exception as exc:  # noqa: BLE001 — relatado, nunca engolido
            falhou = True
            if ao_falhar:
                ao_falhar(exc)
            else:
                print(f"[{datetime.now(timezone.utc):%H:%M:%S}Z] ciclo FALHOU: "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)
                traceback.print_exc()

        restante = prazo - agora()
        if restante <= 0:
            break
        # Nunca dormir além do prazo: o run precisa encerrar limpo antes do
        # limite do runner.
        dormir(min(intervalo_seg, restante))

    return 1 if falhou else 0


def _ciclo_notificador(canal: str) -> Callable[[], None]:
    """O ciclo real: o mesmo trabalho que `slack_notify.py --once` faz."""
    import slack_notify

    def ciclo() -> None:
        tentadas, enviadas = slack_notify.notificar_processos(canal)
        codigo = slack_notify.status_saida(tentadas, enviadas)
        marca = f"[{datetime.now(timezone.utc):%H:%M:%S}Z]"
        if codigo:
            raise RuntimeError(
                f"{enviadas}/{tentadas} enviadas em {canal} — "
                f"{tentadas - enviadas} FALHARAM")
        if enviadas:
            print(f"{marca} ✓ {enviadas}/{tentadas} enviada(s) em {canal}",
                  flush=True)
        else:
            print(f"{marca} nada novo", flush=True)

    return ciclo


def main() -> int:
    import slack_client
    import slack_notify

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutos", type=int, default=MINUTOS_PADRAO)
    ap.add_argument("--intervalo", type=int, default=INTERVALO_PADRAO_SEG,
                    help="segundos entre ciclos (padrão 300)")
    ap.add_argument("--canal", default=slack_notify.CANAL_PADRAO)
    args = ap.parse_args()

    # FAIL-LOUD antes de entrar num laço de 5h: sem token, todo ciclo vai
    # falhar igual, e descobrir isso às 5h29 é tarde demais.
    if not slack_client._token():
        print("vigia: sem Bot Token (SLACK_BOT_TOKEN)", file=sys.stderr)
        return 1

    print(f"vigia: ciclo a cada {args.intervalo}s por {args.minutos} min "
          f"em {args.canal}", flush=True)
    codigo = vigiar(minutos=args.minutos, intervalo_seg=args.intervalo,
                    ciclo=_ciclo_notificador(args.canal))
    print(f"vigia: janela encerrada ({'com falhas' if codigo else 'sem falhas'})",
          flush=True)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
