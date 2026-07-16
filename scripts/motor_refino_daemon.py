"""Daemon de REFINO do Motor v2 — medição e evidência contínuas.
================================================================
A cada ciclo (padrão 3h):
  1. roda o QA do motor no corpus completo (motor × extratos da plataforma)
  2. grava a trajetória em motor_qa_historico (Neon) — série temporal da acurácia
  3. detecta clusters de divergência com massa (n≥10) e escreve o dossiê em
     reports/motor_clusters_pendentes.md — fila de evidência p/ a próxima regra
  4. ALARMA regressão (queda >2pp vs. última medição) — regra nova quebrou algo

O daemon NÃO muda regras sozinho (regra sem evidência já causou regressão
59,5%→26% uma vez); ele industrializa a medição e a coleta de evidência para
o ciclo de engenharia clean-room.

Uso:
    python scripts/motor_refino_daemon.py --intervalo-h 3
    python scripts/motor_refino_daemon.py --once
"""
from __future__ import annotations

import argparse
import glob
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.db.connection import get_db_connection

_DDL = """
CREATE TABLE IF NOT EXISTS motor_qa_historico (
    id BIGSERIAL PRIMARY KEY,
    medido_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    corpus INT, acertos INT, acuracia NUMERIC(5,2),
    divergem INT, sem_api INT,
    clusters TEXT
)
"""


def ciclo() -> None:
    import pandas as pd

    log = ROOT / "reports" / "qa_refino_ultimo.log"
    r = subprocess.run([sys.executable, "-u", str(ROOT / "scripts" / "qa_motor_v2.py"), "--workers", "4"],
                       capture_output=True, text=True, timeout=3000, encoding="utf-8", errors="replace")
    log.write_text(r.stdout or "", encoding="utf-8")

    import re
    m = re.search(r"ACURÁCIA DO MOTOR: (\d+)/(\d+) \(([\d\.]+)%\)\s+\|\s+divergem (\d+)\s+\|\s+sem API (\d+)", r.stdout or "")
    if not m:
        print(f"[{datetime.now():%H:%M}] ✗ QA não produziu placar — ver {log}")
        return
    acertos, corpus, acc, div, sem = int(m[1]), int(m[2]), float(m[3]), int(m[4]), int(m[5])

    # dossiê de clusters pendentes a partir do CSV do QA
    csvs = sorted(glob.glob(str(ROOT / "reports" / "qa_motor_v2_*.csv")))
    clusters_txt = ""
    if csvs:
        df = pd.read_csv(csvs[-1])
        d = df[df["status"] == "diverge"]
        if len(d):
            g = (d.groupby(["motivo", "resolucao"]).agg(n=("oid", "count"), delta_med=("delta", "median"))
                 .reset_index().sort_values("n", ascending=False))
            g = g[g["n"] >= 10]
            clusters_txt = g.to_string(index=False)
            dossie = ROOT / "reports" / "motor_clusters_pendentes.md"
            linhas = [f"# Clusters de divergência com massa (n≥10) — {datetime.now():%d/%m/%Y %H:%M}",
                      f"\nAcurácia atual: {acc}% ({acertos}/{corpus})\n",
                      "| motivo | resolução | n | delta mediano | exemplos |", "|---|---|---|---|---|"]
            for _, row in g.iterrows():
                ex = d[(d["motivo"] == row["motivo"]) & (d["resolucao"] == row["resolucao"])]["oid"].head(3).tolist()
                linhas.append(f"| {row['motivo']} | {row['resolucao']} | {int(row['n'])} | {row['delta_med']:.2f} | {ex} |")
            dossie.write_text("\n".join(linhas), encoding="utf-8")

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(_DDL)
        cur.execute("SELECT acuracia FROM motor_qa_historico ORDER BY medido_em DESC LIMIT 1")
        prev = cur.fetchone()
        cur.execute("INSERT INTO motor_qa_historico (corpus, acertos, acuracia, divergem, sem_api, clusters) "
                    "VALUES (%s,%s,%s,%s,%s,%s)", (corpus, acertos, acc, div, sem, clusters_txt[:2000]))
    conn.commit()
    conn.close()

    alarme = ""
    if prev and float(prev[0]) - acc > 2.0:
        alarme = f"  🚨 REGRESSÃO: {prev[0]}% → {acc}% (regra recente quebrou algo — reverter/investigar)"
    print(f"[{datetime.now():%d/%m %H:%M}] motor {acc}% ({acertos}/{corpus}) | divergem {div} | sem API {sem}{alarme}",
          flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervalo-h", type=float, default=3.0, dest="h")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    print(f"DAEMON DE REFINO DO MOTOR — QA a cada {args.h:g}h (corpus completo)")
    while True:
        try:
            ciclo()
        except Exception as exc:
            print(f"  ✗ ciclo falhou: {type(exc).__name__}: {exc}", flush=True)
        if args.once:
            break
        time.sleep(args.h * 3600)


if __name__ == "__main__":
    main()
