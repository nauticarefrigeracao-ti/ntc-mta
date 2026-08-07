"""O push criou run? Confere e grita quando nao criou.

POR QUE ISTO EXISTE

Em 06/08/2026 o GitHub teve um incidente critico de 10h42 (Actions + Pages).
Durante ele, os webhooks foram estrangulados a 15% e, no texto oficial,
"many push and pull request events are not triggering workflow runs".
https://www.githubstatus.com/incidents/qcvjkzcs7j74

Quatro commits foram para `main` e NENHUM rodou o CI. Ninguem percebeu por
quase um dia, porque um run que nao nasce nao aparece em lugar nenhum -- nao
ha vermelho, nao ha aviso, so ausencia. E ausencia parece sucesso.

A doc confirma que isso nao e enfileirado e sim descartado: "The workflow
runs that were supposed to be triggered by the webhook events will be
blocked and will not be queued."
https://docs.github.com/en/actions/reference/limits

Nenhuma configuracao de workflow impede um incidente do fornecedor. O que se
pode fazer e DETECTAR -- e e so isso que este script faz.

Uso:
    python scripts/conferir_ci.py                 # confere o HEAD
    python scripts/conferir_ci.py --sha abc1234
    python scripts/conferir_ci.py --esperar 40    # segundos antes de checar
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import Any, Mapping, Optional

REPO = "nauticarefrigeracao-ti/ntc-mta"


def contar_runs(payload: Optional[Mapping[str, Any]]) -> int:
    """Quantos runs existem para o SHA. Resposta estranha conta como ZERO.

    Errar para o lado de "nao rodou" e o certo: um alerta a mais custa uma
    conferida; um alerta a menos custa codigo sem teste em producao.
    """
    try:
        return int((payload or {}).get("total_count") or 0)
    except (TypeError, ValueError):
        return 0


def diagnostico(sha: str, n_runs: int) -> tuple[bool, str]:
    """(esta_ok, mensagem). Curto e acionavel -- ninguem le paragrafo em CI."""
    if n_runs > 0:
        return True, f"OK: {n_runs} run(s) criado(s) para {sha[:7]}"
    return False, (
        f"ALERTA: o push de {sha[:7]} NAO criou run de CI.\n"
        f"  1. confira https://www.githubstatus.com (Actions)\n"
        f"  2. se o GitHub estiver saudavel, dispare na mao:\n"
        f"     gh workflow run tests.yml\n"
        f"  Ate lá, o codigo deste commit esta em main SEM teste no CI."
    )


# --- I/O -------------------------------------------------------------------

def _gh(*args: str) -> Optional[dict]:
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True,
                           timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def sha_do_head() -> Optional[str]:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                           text=True, timeout=20)
        return r.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sha")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--esperar", type=int, default=25,
                    help="segundos antes de checar (o run leva alguns "
                         "segundos para nascer)")
    args = ap.parse_args()

    sha = args.sha or sha_do_head()
    if not sha:
        print("nao consegui descobrir o SHA", file=sys.stderr)
        return 1

    if args.esperar:
        time.sleep(args.esperar)

    payload = _gh("api", f"repos/{args.repo}/actions/runs?head_sha={sha}")
    if payload is None:
        print("nao consegui falar com a API do GitHub — conferir na mao",
              file=sys.stderr)
        return 1

    ok, msg = diagnostico(sha, contar_runs(payload))
    print(msg, file=sys.stdout if ok else sys.stderr)
    # Codigo 1 quando nao rodou: quem chama isso num script para na hora.
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
