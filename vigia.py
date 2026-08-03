"""Vigia: um run que vive e cicla por dentro, em vez de pedir favor ao cron.

POR QUE ISTO EXISTE
-------------------
Os workflows pediam `*/5` e `*/20`. Medição de 03/08/2026 nos dois repos:

    workflow          cron pede   entrega (média)   menor / maior
    notificador          5 min        97 min         52 / 219 min
    sync-rapido         20 min       104 min         56 / 229 min
    ntc-sync           120 min       166 min         84 / 284 min

O GitHub entrega mais ou menos UM run a cada ~100 minutos por workflow,
independente do que se peça. Pedir mais rápido que isso não compra nada:
`schedule` é melhor-esforço e os crons mais frequentes são os mais
despriorizados. A latência de ~25 min que prometemos ao negócio nunca
existiu — a mediana real medida no banco foi de 4,9h (7 dias) e 13,3h
(últimos 2 dias).

A saída: o cron vira só a PARTIDA. Cada run entra num laço de ~5h30
executando o comando a cada 5 minutos. Os dois repos são públicos, então
minuto de Actions não é cobrado.

POR QUE SUBPROCESSO, E NÃO IMPORT
---------------------------------
Este arquivo é compartilhado entre B.I e ntc-mta (ver harness/sincronia.py):
cada repo cicla um comando diferente. Rodar por subprocesso mantém o arquivo
IDÊNTICO nos dois — e dá isolamento de brinde: um ciclo que vaza memória ou
corrompe estado não contamina as 5h seguintes.

DUAS DECISÕES QUE VALEM SER DITAS
---------------------------------
1. **Falha de um ciclo não mata o laço.** Um `OperationalError` às 00h10
   deixaria a Maria sem aviso até as 6h. O ciclo seguinte tenta de novo.

2. **Mas falha não é engolida.** O erro vai para stderr com hora e código, e
   o run termina com 1 — fica vermelho no painel do Actions. Continuar
   trabalhando não é o mesmo que fingir que deu certo (CLAUDE.md: falhar alto).

Uso:
    python vigia.py --comando "python slack_notify.py --once"
    python vigia.py --comando "python scripts/sync_cloud.py --so-claims"
    python vigia.py --comando "..." --minutos 20 --intervalo 60
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Callable, Optional

# Job do GitHub morre em 6h. 5h30 encerra limpo antes disso e ainda cobre o
# pior intervalo de cron já medido (3h49) com folga.
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


def ciclo_de_comando(comando: str,
                     executar: Optional[Callable[..., object]] = None
                     ) -> Callable[[], None]:
    """Transforma uma linha de comando no ciclo que o vigia repete.

    Saída não-zero vira exceção — é assim que o laço sabe que o ciclo falhou
    e que o run tem que terminar vermelho.
    """
    if not (comando or "").strip():
        raise ValueError("vigia precisa de --comando para executar")
    executar = executar or subprocess.run
    argv = shlex.split(comando)

    def ciclo() -> None:
        marca = f"[{datetime.now(timezone.utc):%H:%M:%S}Z]"
        r = executar(argv, check=False)
        codigo = getattr(r, "returncode", 0)
        if codigo:
            raise RuntimeError(f"{comando!r} saiu com código {codigo}")
        print(f"{marca} ok", flush=True)

    return ciclo


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Cicla um comando por uma janela.")
    ap.add_argument("--comando", required=True,
                    help="linha de comando a repetir a cada intervalo")
    ap.add_argument("--minutos", type=int, default=MINUTOS_PADRAO)
    ap.add_argument("--intervalo", type=int, default=INTERVALO_PADRAO_SEG,
                    help="segundos entre ciclos (padrão 300)")
    args = ap.parse_args(argv)

    print(f"vigia: `{args.comando}` a cada {args.intervalo}s "
          f"por {args.minutos} min", flush=True)
    codigo = vigiar(minutos=args.minutos, intervalo_seg=args.intervalo,
                    ciclo=ciclo_de_comando(args.comando))
    print(f"vigia: janela encerrada "
          f"({'COM FALHAS' if codigo else 'sem falhas'})", flush=True)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
