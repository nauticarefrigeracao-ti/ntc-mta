"""QA do Motor v2 — motor (API pura) × extratos reais coletados da plataforma.
================================================================================
Roda motor_saldo.calcular() sobre os pedidos com extrato coletado e mede a
acurácia do saldo (±R$ 1). Divergências saem com o delta decomposto por
componente — cada erro aponta a regra/insumo a refinar (nada de copiar valor).

Uso:
    python scripts/qa_motor_v2.py --n 200        # amostra
    python scripts/qa_motor_v2.py                # corpus inteiro
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
from src.db.connection import get_db_connection
from src.services import motor_saldo


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="amostra (0 = corpus inteiro)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--tol", type=float, default=1.00)
    args = ap.parse_args()

    conn = get_db_connection()
    df = pd.read_sql(
        "SELECT order_id, total::float AS pagina, painel_titulo, "
        "produto::float AS pg_produto, tarifa_venda::float AS pg_tarifa, "
        "envios::float AS pg_envios, cancelamentos::float AS pg_cancel "
        "FROM meli_page_saldos WHERE total IS NOT NULL ORDER BY coletado_em DESC", conn)
    conn.close()
    if args.n:
        df = df.head(args.n)

    alvo = {int(r["order_id"]): r.to_dict() for _, r in df.iterrows()}
    print(f"QA MOTOR v2 — {len(alvo):,} pedidos × extrato da plataforma (tol ±R$ {args.tol:.2f})")

    res: list[dict] = []
    lock = threading.Lock()
    t0 = time.monotonic()

    def um(oid: int):
        m = motor_saldo.calcular(oid)
        a = alvo[oid]
        pagina, painel = float(a["pagina"]), str(a["painel_titulo"])
        r = {"oid": oid, "pagina": pagina, "painel": painel}
        if m is None:
            r["status"] = "sem_api"
            return r
        # diff COMPONENTE a COMPONENTE — identifica a regra errada, não só o total
        env_motor = -(m["frete_ida"] + m["tarifa_devolucao"])
        canc_motor = -(m["reembolso"] - m["estornos"])
        r.update({
            "motor": m["saldo"], "delta": round(m["saldo"] - pagina, 2),
            "d_produto": round(m["produto"] - a["pg_produto"], 2) if a["pg_produto"] is not None and not pd.isna(a["pg_produto"]) else None,
            "d_tarifa":  round(-m["tarifa_venda"] - a["pg_tarifa"], 2) if a["pg_tarifa"] is not None and not pd.isna(a["pg_tarifa"]) else None,
            "d_envios":  round(env_motor - a["pg_envios"], 2) if a["pg_envios"] is not None and not pd.isna(a["pg_envios"]) else None,
            "d_cancel":  round(canc_motor - a["pg_cancel"], 2) if a["pg_cancel"] is not None and not pd.isna(a["pg_cancel"]) else None,
            "regras": "+".join(m["regras"]), "tipo": m["processo"]["tipo"],
            "resolucao": m["processo"]["resolucao"], "motivo": m["processo"].get("motivo"), "beneficiado": m["processo"]["beneficiado"],
            "ml_cobriu": m["processo"]["ml_cobriu"], "rev": m["processo"]["reverso_status"],
            "status": "ok" if abs(m["saldo"] - pagina) <= args.tol else "diverge"})
        return r

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(um, oid): oid for oid in alvo}
        done = 0
        for f in as_completed(futs):
            with lock:
                res.append(f.result())
                done += 1
                if done % 50 == 0:
                    dt = time.monotonic() - t0
                    ok = sum(1 for x in res if x.get("status") == "ok")
                    print(f"  {done}/{len(alvo)}  acerto parcial {ok}/{done} ({ok/done*100:.1f}%)  "
                          f"[{dt/done:.1f}s/pedido]", flush=True)

    ok  = sum(1 for x in res if x.get("status") == "ok")
    div = [x for x in res if x.get("status") == "diverge"]
    sem = sum(1 for x in res if x.get("status") == "sem_api")
    n = len(res)
    print(f"\n{'='*70}\nACURÁCIA DO MOTOR: {ok}/{n} ({ok/n*100:.1f}%)  |  divergem {len(div)}  |  sem API {sem}")

    por_tipo = {}
    for x in res:
        if x.get("status") in ("ok", "diverge"):
            t = f"{x.get('tipo')}/{x.get('painel','')[:22]}"
            a, b = por_tipo.get(t, (0, 0))
            por_tipo[t] = (a + (x["status"] == "ok"), b + 1)
    print("\npor tipo/painel:")
    for t, (a, b) in sorted(por_tipo.items(), key=lambda kv: -kv[1][1]):
        print(f"  {t:44s} {a:4d}/{b:<4d} ({a/b*100:5.1f}%)")

    print("\ntop 15 divergências por |delta| (componente errado em destaque):")
    for x in sorted(div, key=lambda x: -abs(x["delta"]))[:15]:
        comp = " ".join(f"{k[2:]}={v:+.2f}" for k in ("d_produto","d_tarifa","d_envios","d_cancel")
                        if (v := x.get(k)) is not None and abs(v) > args.tol)
        print(f"  {x['oid']}  delta={x['delta']:>8.2f}  ERRO EM: {comp or '?'}  "
              f"[mot={x.get('motivo')} res={x.get('resolucao')} ben={x.get('beneficiado')} rev={x.get('rev')}]")

    # regressão de regra: acurácia do componente cancelamentos × condição de estorno
    dfd = pd.DataFrame([x for x in res if x.get("status") in ("ok","diverge")])
    if not dfd.empty and "d_cancel" in dfd.columns:
        dfd["cancel_ok"] = dfd["d_cancel"].abs() <= args.tol
        print("\nacurácia do componente CANCELAMENTOS por resolução×cobertura:")
        g = dfd.groupby(["resolucao","motivo"])["cancel_ok"].agg(["sum","count"])
        for (resol, cob), row in g.iterrows():
            if row["count"] >= 3:
                print(f"  res={str(resol):24s} cobriu={str(cob):5s}  {int(row['sum'])}/{int(row['count'])} ({row['sum']/row['count']*100:.0f}%)")

    out = ROOT / "reports" / f"qa_motor_v2_{datetime.now():%Y-%m-%d_%H%M}.csv"
    pd.DataFrame(res).to_csv(out, index=False)
    print(f"\n✓ detalhe completo: {out}")


if __name__ == "__main__":
    main()
