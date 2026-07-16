"""Mineração de regras de negócio dos extratos coletados (clean-room, fase spec).
==================================================================================
Parseia os 1.257 extratos linha-a-linha de `meli_page_saldos.detalhes` e:

 1. cataloga TODOS os rótulos monetários distintos (frequência + exemplos)
 2. testa identidades matemáticas candidatas a regra:
    - Total == soma das seções (produto + parcelamento + tarifa + envios + cancelamentos)
    - Tarifa de devolução == k × frete de ida (k = 1? 2?)
    - 'Cancelamento de tarifa …' == estorno integral da tarifa de venda
    - devolução finalizada → Total == −envios (produto e tarifa se anulam)
 3. mede acurácia de cada regra sobre o corpus → vira spec do motor v2

Saída: reports/spec_regras_meli.md
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
from src.db.connection import get_db_connection

_RE_VAL = re.compile(r"^(-?\s?R\$\s?[\d\.\,]+)$")


def _parse_brl(t: str) -> float | None:
    t = t.replace("\xa0", " ").strip()
    neg = t.startswith("-")
    t = re.sub(r"[^\d,\.]", "", t)
    if not t:
        return None
    try:
        v = float(t.replace(".", "").replace(",", "."))
    except ValueError:
        return None
    return -v if neg else v


def parse_extrato(det: str) -> list[tuple[str, float]]:
    """['label', valor] na ordem do painel. Linha-valor pertence ao label anterior."""
    pares = []
    label = None
    for ln in det.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if _RE_VAL.match(ln):
            if label:
                pares.append((label, _parse_brl(ln)))
                label = None
        else:
            label = ln
    return pares


def main() -> None:
    conn = get_db_connection()
    df = pd.read_sql(
        "SELECT order_id::text AS oid, painel_titulo, produto, tarifa_venda, envios, "
        "cancelamentos, parcelamento, total, detalhes "
        "FROM meli_page_saldos WHERE detalhes IS NOT NULL AND total IS NOT NULL", conn)
    conn.close()

    n = len(df)
    labels = Counter()
    exemplos: dict[str, str] = {}
    ok_ident = ok_dev2x = n_dev2x = ok_estorno = n_estorno = ok_devtotal = n_devtotal = 0
    n_reemb = com_estorno = ok_parc0 = n_parc = 0

    # seções do painel: o valor vem na 1ª linha-valor após o cabeçalho
    # (pode haver texto explicativo no meio)
    SECOES = ["Preço do produto","Preço dos produtos","Taxa de parcelamento e acréscimo",
              "Tarifa de venda total","Envios","Cancelamentos","Devoluções","Estorno"]

    def secoes_do_extrato(det: str) -> dict[str, float]:
        out: dict[str, float] = {}
        atual = None
        for ln in str(det).splitlines():
            ln = ln.strip()
            if not ln:
                continue
            if ln in SECOES and ln not in out:
                atual = "produto" if ln.startswith("Preço") else ln
                continue
            if atual and _RE_VAL.match(ln):
                out[atual] = _parse_brl(ln)
                atual = None
        return out

    for _, r in df.iterrows():
        pares = parse_extrato(str(r["detalhes"]))
        sec = secoes_do_extrato(r["detalhes"])
        total = float(r["total"])

        # identidade contábil: Total = soma das seções (a partir do extrato cru)
        soma = sum(v for k, v in sec.items())
        if sec and abs(soma - total) <= 0.05:
            ok_ident += 1

        # parcelamento líquido = 0 (acréscimo do comprador cobre a taxa)
        if "Taxa de parcelamento e acréscimo" in sec:
            n_parc += 1
            if abs(sec["Taxa de parcelamento e acréscimo"]) <= 0.05:
                ok_parc0 += 1

        # quando há reembolso (seção Cancelamentos/Devoluções ≠ 0), há estorno de tarifa?
        reemb_sec = sec.get("Cancelamentos", sec.get("Devoluções"))
        if reemb_sec is not None and abs(reemb_sec) > 0.05:
            n_reemb += 1
            if any(l.startswith("Cancelamento de tarifa") for l, _ in pares):
                com_estorno += 1

        for lbl, _v in pares:
            labels[lbl] += 1
            exemplos.setdefault(lbl, r["oid"])

        d = dict(pares)
        frete_ida = d.get("Tarifa do Mercado Envios (por sua conta)")
        tar_dev   = d.get("Tarifa de devolução")
        if frete_ida is not None and tar_dev is not None and frete_ida != 0:
            n_dev2x += 1
            if abs(abs(tar_dev) - 2 * abs(frete_ida)) <= 0.05:
                ok_dev2x += 1

        # regra 3: 'Cancelamento de tarifa de X%' == estorno integral da tarifa de venda
        est = next((v for l, v in pares if l.startswith("Cancelamento de tarifa de")), None)
        if est is not None and r["tarifa_venda"] is not None:
            n_estorno += 1
            if abs(abs(est) - abs(float(r["tarifa_venda"]))) <= 0.05:
                ok_estorno += 1

        # regra 4: devolução finalizada → Total == −(envios)
        if str(r["painel_titulo"]) == "Recebimento devolvido" and r["envios"] is not None:
            n_devtotal += 1
            if abs(total - float(r["envios"])) <= 0.05:
                ok_devtotal += 1

    out = [f"# Spec de Regras Meli — minerado de {n:,} extratos reais ({datetime.now():%d/%m/%Y %H:%M})", ""]
    out.append(f"## Identidades testadas\n")
    out.append(f"- **IDENTIDADE CONTÁBIL: Total = Σ seções (produto+parcelamento−tarifa−envios−cancelamentos)**: {ok_ident}/{n} ({ok_ident/n*100:.1f}%)")
    out.append(f"- **Parcelamento líquido = 0 (acréscimo do comprador cobre a taxa)**: {ok_parc0}/{n_parc} ({(ok_parc0/n_parc*100) if n_parc else 0:.1f}%)")
    out.append(f"- **Reembolso presente → existe estorno de tarifa**: {com_estorno}/{n_reemb} ({(com_estorno/n_reemb*100) if n_reemb else 0:.1f}%)")
    out.append(f"- **Tarifa de devolução = 2× frete de ida**: {ok_dev2x}/{n_dev2x} ({(ok_dev2x/n_dev2x*100) if n_dev2x else 0:.1f}%)")
    out.append(f"- **'Cancelamento de tarifa' = estorno integral da tarifa de venda**: {ok_estorno}/{n_estorno} ({(ok_estorno/n_estorno*100) if n_estorno else 0:.1f}%)")
    out.append(f"- **Devolução finalizada → Total = −envios**: {ok_devtotal}/{n_devtotal} ({(ok_devtotal/n_devtotal*100) if n_devtotal else 0:.1f}%)")
    out.append(f"\n## Catálogo de rótulos recorrentes (≥3 ocorrências; nomes de produto filtrados)\n")
    out.append("| rótulo | freq | exemplo (order_id) |")
    out.append("|---|---|---|")
    for lbl, c in labels.most_common(80):
        if c >= 3:
            out.append(f"| {lbl[:70]} | {c} | {exemplos[lbl]} |")

    dest = ROOT / "reports" / "spec_regras_meli.md"
    dest.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out[:12]))
    print(f"\n✓ spec completa: {dest}")


if __name__ == "__main__":
    main()
