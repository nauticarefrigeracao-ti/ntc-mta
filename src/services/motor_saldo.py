"""Motor de Saldo v2 — calcula o resultado financeiro de cada venda SÓ com API.
================================================================================
Clean-room: fórmula e regras mineradas de 1.257 extratos reais da plataforma
(reports/spec_regras_meli.md). Nenhum valor copiado de página — o RPA de coleta
é usado apenas como QA (scripts/qa_motor_v2.py mede o motor contra os extratos).

FÓRMULA (identidade contábil, 100,0% no corpus):
    saldo = produto + acrescimo_liq − tarifa_venda − envios − cancelamentos_liq

Componentes e fontes (todas ML API, sempre frescas):
    produto          Σ order_items.unit_price × qty
    acrescimo_liq    0 (acréscimo do comprador cobre a taxa de parcelamento — 100%)
    tarifa_venda     Σ order_items.sale_fee × qty (estornada conforme resolução)
    frete_ida        /shipments/{id}/costs → senders[seller].cost
    tarifa_devolucao 2 × frete_ida quando há devolução física (regra 2×, 86,8%;
                     fonte definitiva futura: billing API)
    reembolso        payments.transaction_amount_refunded (pagamento efetivo)
    estornos         por resolução do claim (ver _estornos)
    processo         claim.resolution + return.shipments[] (máquina de estados)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.api import ml_client

SELLER_ID = 96077248  # Náutica Refrigeração


def _retry(fn, *args, tentativas: int = 4):
    """A ML API responde 429 sob rajada — como o ml_client engole erros e
    retorna None, re-tenta com backoff antes de aceitar o None."""
    import time as _t
    for i in range(tentativas):
        r = fn(*args)
        if r is not None:
            return r
        _t.sleep(1.5 * (i + 1))
    return None


def _best_payment(pays: list) -> dict:
    if not pays:
        return {}
    _rank = {"refunded": 0, "approved": 1, "in_mediation": 2, "pending": 3,
             "in_process": 3, "authorized": 3, "cancelled": 4, "rejected": 9}
    return min(pays, key=lambda p: (
        _rank.get(str(p.get("status") or ""), 5),
        -float(p.get("transaction_amount") or 0)))


def _frete_ida(ship_id) -> float | None:
    if not ship_id:
        return None
    c = _retry(lambda: ml_client._get(f"/shipments/{ship_id}/costs"))
    if not c:
        return None
    for s in c.get("senders") or []:
        if int(s.get("user_id") or 0) == SELLER_ID:
            return float(s.get("cost") or 0)
    return None


def _processo(order: dict) -> dict:
    """Estado do processo pós-venda: claim, resolução e logística reversa."""
    out = {"tipo": "venda", "estado": "concluida", "resolucao": None,
           "beneficiado": None, "ml_cobriu": None, "devolucao_fisica": False, "motivo": None,
           "reverso_status": None}
    if str(order.get("status")) == "cancelled":
        out["tipo"] = "cancelamento"
    meds = order.get("mediations") or []
    if not meds:
        return out
    claim_id = meds[0].get("id")
    out["tipo"] = "disputa"
    cl = _retry(ml_client.get_claim, claim_id) or {}
    res = cl.get("resolution") or {}
    out["resolucao"]   = res.get("reason")
    out["motivo"]      = cl.get("reason_id")
    out["beneficiado"] = (res.get("benefited") or [None])[0]
    out["ml_cobriu"]   = bool(res.get("applied_coverage"))
    out["estado"]      = str(cl.get("status") or "")
    ret = _retry(ml_client.get_return, claim_id)
    if ret:
        ships = (ret or {}).get("shipments") or []
        if ships:
            out["devolucao_fisica"] = True
            # perna final = devolução ao vendedor; define o rótulo do processo
            st = {s.get("status") for s in ships}
            if "pending" in st or "ready_to_ship" in st or "shipped" in st:
                out["reverso_status"] = "em_transito"
            elif st and st.issubset({"delivered", "cancelled", "not_delivered"}):
                out["reverso_status"] = "finalizado"
    return out


def _estornos(tarifa_venda: float, frete_ida: float, tarifa_dev: float,
              reembolso: float, proc: dict) -> tuple[float, list[str]]:
    """Regras de estorno — iteração 3, ajustadas pelo QA componente-a-componente
    (qa_motor_v2, 150 casos): E3 era aplicada demais; evidência mostrou que o
    estorno da tarifa de devolução só ocorre com o processo EM TRÂNSITO
    (provisório — 0/37 em item_returned finalizado, 2/30 em warehouse finalizado).
    E1: reembolso ao comprador → tarifa de venda estornada (94,9% integral)
    E2: cancelamento pré-envio → frete também estornado (saldo zera)
    E3: tarifa de devolução estornada apenas enquanto reverso em trânsito
    """
    est, regras = 0.0, []
    if reembolso > 0.005:
        est += tarifa_venda
        regras.append("E1_tarifa_venda")
        if proc["tipo"] == "cancelamento" and not proc["devolucao_fisica"]:
            est += frete_ida
            regras.append("E2_frete_cancelamento")
        # E4: devolução SEM culpa do vendedor (arrependimento) → ML cobre o
        # processo; frete de ida estornado junto (doc oficial + QA 29 casos)
        if proc["devolucao_fisica"] and not proc.get("culpa_vendedor", True):
            est += frete_ida
            regras.append("E4_frete_arrependimento")
    if tarifa_dev > 0.005 and proc.get("reverso_status") == "em_transito":
        est += tarifa_dev
        regras.append("E3_tarifa_dev_em_transito")
    return est, regras


def calcular(order_id: int) -> dict | None:
    """Resultado financeiro + estado de processo de um pedido, 100% via API."""
    o = _retry(ml_client.get_order, order_id)
    if not o:
        return None
    its = o.get("order_items") or []
    produto      = sum(float(i.get("unit_price") or 0) * int(i.get("quantity") or 0) for i in its)
    tarifa_venda = sum(float(i.get("sale_fee") or 0) * int(i.get("quantity") or 0) for i in its)
    pay          = _best_payment(o.get("payments") or [])
    reembolso    = float(pay.get("transaction_amount_refunded") or 0)
    proc         = _processo(o)

    frete_ida = _frete_ida((o.get("shipping") or {}).get("id"))
    if frete_ida is None:
        frete_ida = 0.0
    # POLÍTICA DE CULPA (doc oficial ML + QA por motivo):
    #  - defeito/diferente (PDD9949…): vendedor paga ida + tarifa de devolução
    #    (QA: 10/10 no corpus)
    #  - arrependimento/não serve (PDD9939…): devolução GRÁTIS pro vendedor —
    #    ML cobre; nada de tarifa de devolução e o frete de ida é estornado
    #    (QA: página zera 29/29; doc: "será grátis e não afetará sua reputação")
    motivo = str(proc.get("motivo") or "")
    ARREPENDIMENTO = {"PDD9939", "PDD9938", "PDD9952", "PDD9955"}  # família 'se arrependeu/não serve'
    proc["culpa_vendedor"] = motivo not in ARREPENDIMENTO
    cobra_dev = (proc["devolucao_fisica"]
                 and proc.get("beneficiado") == "complainant"
                 and proc.get("resolucao") != "item_changed"
                 and proc["culpa_vendedor"])
    tarifa_dev = 2.0 * frete_ida if cobra_dev else 0.0

    estornos, regras = _estornos(tarifa_venda, frete_ida, tarifa_dev, reembolso, proc)
    envios = frete_ida + tarifa_dev
    cancel_liq = reembolso - estornos
    saldo = round(produto - tarifa_venda - envios - cancel_liq, 2)

    return {
        "order_id": order_id,
        "saldo": saldo,
        "produto": round(produto, 2),
        "tarifa_venda": round(tarifa_venda, 2),
        "frete_ida": round(frete_ida, 2),
        "tarifa_devolucao": round(tarifa_dev, 2),
        "reembolso": round(reembolso, 2),
        "estornos": round(estornos, 2),
        "regras": regras,
        "processo": proc,
        "pay_detail": str(pay.get("status_detail") or ""),
    }


if __name__ == "__main__":
    import json
    oid = int(sys.argv[1]) if len(sys.argv) > 1 else 2000017196746200
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(calcular(oid), ensure_ascii=False, indent=1))
