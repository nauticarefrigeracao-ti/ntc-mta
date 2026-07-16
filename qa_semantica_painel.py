"""QA SEMÂNTICO do painel — invariantes de negócio verificadas a cada geração.
================================================================================
Papel: analista de requisitos automatizado. Cada card tem uma PROMESSA
semântica; este script valida que o conteúdo gerado cumpre a promessa.
Falhou invariante = regressão de negócio (não de código) → acusa antes do
usuário/chefe ver.

Uso:
    python scripts/qa_semantica_painel.py [caminho_do_html]
Exit code 1 se qualquer invariante falhar (integrável a CI/daemon).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HTML = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "reports" / "painel_devolucoes_live.html"

# códigos internos que NUNCA podem aparecer crus em campos de exibição
_COD_CRU = re.compile(r"^(bpp_|PDD\d|PNR\d|cc_rejected|not_reconciled$|reconciled$|by_admin$)")

falhas: list[str] = []
ok: list[str] = []


def check(nome: str, cond: bool, detalhe: str = "") -> None:
    (ok if cond else falhas).append(f"{nome}{' — ' + detalhe if detalhe and not cond else ''}")


def main() -> int:
    html = HTML.read_text(encoding="utf-8")
    D = json.loads(re.search(r"const D=(\{.*?\});\n", html, re.S).group(1))

    recl   = D["recl_modal"]
    rev    = D["revert_modal"]
    canc   = D["cancel_modal"]
    nc     = D["nc_modal"]

    # I1 — REVERTIDOS: promessa "fechou sem prejuízo". Nenhum ativo com saldo
    # negativo relevante ou sem confirmação.
    ativos = [r for r in rev if not r.get("fora")]
    ruins = [r for r in ativos if r.get("saldo_final") is None or float(r.get("saldo_final") or 0) < -5]
    check("I1 revertidos só zero/positivo confirmado", not ruins,
          f"{len(ruins)} violações ex.: {[r['order_id'] for r in ruins[:3]]}")

    # I2 — UM PEDIDO UM CARD: cancelamentos × reclamações sem interseção
    inter = {str(r.get("order_id")) for r in canc} & {str(r.get("order_id")) for r in recl}
    check("I2 overlap cancelamentos×reclamações = 0", not inter, f"{len(inter)} pedidos em 2 cards")

    # I3 — GRUPOS COERENTES COM O SALDO
    viol = []
    for r in recl:
        g, s = r.get("grupo"), r.get("saldo_final")
        if g == "perda" and s is not None and s >= -0.005:
            viol.append((r["order_id"], g, s))
        if g == "mantida" and (s is None or s <= 0.005):
            viol.append((r["order_id"], g, s))
        if g == "revertida" and (s is None or abs(s) > 5):
            viol.append((r["order_id"], g, s))
        if g == "conciliacao" and s is not None:
            viol.append((r["order_id"], g, s))
    check("I3 grupo bate com saldo (perda<0, mantida>0, revertida≈0, conciliação=None)",
          not viol, f"{len(viol)} ex.: {viol[:3]}")

    # I4 — NÃO CONCILIADOS: resolvido nunca conta como perda/prejuízo ativo
    nc_ruins = [r for r in nc if r.get("resolvido")
                and re.search(r"Perda|Prejuízo", str(r.get("situacao", "")))]
    check("I4 nc resolvido não rotulado como perda", not nc_ruins, f"{len(nc_ruins)}")

    # I5 — CANCELAMENTOS: anomalia ⇔ residual NEGATIVO não zerado; residual
    # POSITIVO com rótulo de indenização/saldo é legítimo (ML pagou o vendedor)
    canc_ruins = []
    for r in canc:
        resid = float(r.get("resid") or 0)
        sit = str(r.get("situacao", ""))
        eh_anomalia = "ANOMALIA" in sit
        eh_positivo_ok = resid > 0.10 and ("🟢" in sit or "positiv" in sit.lower())
        deveria_anomalia = abs(resid) > 0.10 and not eh_positivo_ok
        if eh_anomalia != deveria_anomalia:
            canc_ruins.append(r.get("order_id"))
    check("I5 anomalia ⇔ residual não explicado (positivo indenizado é ok)", not canc_ruins,
          f"{len(canc_ruins)} ex.: {canc_ruins[:3]}")

    # I6 — UX WRITING: nenhum código interno cru em campos exibidos
    _cod_motivo = re.compile(r"^\s*(PDD|PNR)\d+\s*$|^status ML API:")
    cru = []
    for lista in (recl, rev, canc, nc):
        for r in lista:
            for campo in ("situacao", "estado_api"):
                v = str(r.get(campo) or "")
                if _COD_CRU.match(v):
                    cru.append((r.get("order_id"), campo, v))
            for campo in ("motivo_ml", "motivo"):
                v = str(r.get(campo) or "")
                if _cod_motivo.match(v):
                    cru.append((r.get("order_id"), campo, v))
    check("I6 zero código interno cru na tela", not cru, f"{len(cru)} ex.: {cru[:3]}")

    # I7 — KPI PERDA = soma dos saldos negativos confirmados (daily_disp)
    perda_daily = sum(x.get("perda_cx") or 0 for x in D.get("daily_disp", []))
    perda_modal = -sum(r["saldo_final"] for r in recl
                       if r.get("grupo") in ("perda", "parcial") and r.get("saldo_final") is not None
                       and r["saldo_final"] < 0)
    # modal capado em 500 linhas de dados — tolerância proporcional
    coerente = perda_modal <= perda_daily * 1.02 + 1
    check("I7 KPI perda ≥ soma do modal (sem perda fantasma)", coerente,
          f"daily={perda_daily:.2f} modal={perda_modal:.2f}")

    # I8 — ESTIMATIVA nunca vira saldo oficial
    est_ruins = [r for r in recl if r.get("saldo_estimado") is not None and r.get("saldo_final") is not None]
    check("I8 estimativa só onde não há saldo confirmado", not est_ruins, f"{len(est_ruins)}")

    # I9 — VOCABULÁRIO ÚNICO: prejuízo = saldo final negativo (régua da
    # diretoria). O termo ambíguo "Perda Real" não pode voltar à tela — o
    # componente de produto chama-se "Produto não recuperado".
    check("I9 vocabulário único (sem 'Perda Real' na tela)", "Perda Real" not in html)

    print(f"QA SEMÂNTICO — {HTML.name}")
    for o in ok:
        print(f"  ✅ {o}")
    for f in falhas:
        print(f"  ❌ {f}")
    print(f"\n{len(ok)} invariantes OK | {len(falhas)} FALHAS")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
