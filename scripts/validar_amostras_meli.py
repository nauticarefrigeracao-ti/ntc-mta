"""Validação por amostragem — Painel de Devoluções × ML API × plataforma Meli (web).
===================================================================================
Para cada card do painel, pega os N primeiros resultados (mesma ordem do modal)
e valida em DUAS fontes:
  1. ML API  → GET /orders/{id} (total, status, pay detail, valor devolvido ao comprador)
  2. Web     → mercadolivre.com.br/vendas/{id}/detalhe (RPA Playwright, screenshot + total)

Uso:
    python scripts/validar_amostras_meli.py               # 10 por card, com web RPA
    python scripts/validar_amostras_meli.py --n 5         # 5 por card
    python scripts/validar_amostras_meli.py --sem-web     # só ML API (sem browser)

Login: na 1ª execução abre uma janela do Chromium — faça login no Mercado Livre.
A sessão fica salva em _rpa_meli_profile/ e não precisa logar de novo.

Saída:
    reports/validacao_amostras_YYYY-MM-DD.md   (tabela por card com veredito)
    _rpa_meli_valida/<card>/<order_id>.png     (evidência da plataforma)
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd

import processar_relatorios_mp as rel
from src.api import ml_client
from src.db.connection import get_db_connection

PROFILE_DIR = ROOT / "_rpa_meli_profile"
SHOTS_DIR   = ROOT / "_rpa_meli_valida"
TODAY       = datetime.now().strftime("%Y-%m-%d")

VENDA_URL = "https://www.mercadolivre.com.br/vendas/{oid}/detalhe"


# ── 1. Amostras — mesma construção dos modais do painel ──────────────────────

def build_samples(n: int) -> dict[str, pd.DataFrame]:
    conn = get_db_connection()
    try:
        df_mp, df_port = rel._load_and_enrich(conn)
    finally:
        conn.close()

    SD_DEV  = {"bpp_refunded","bpp_covered","partially_bpp_refunded","partially_bpp_covered","ppv_covered_melienvio"}
    SD_REC  = {"reconciled","compensated","not_reconciled","by_admin"}

    df_port_u = df_port.drop_duplicates("order_id").copy()
    df_dev    = df_port_u[df_port_u["perda_bruta"] > 0].copy()

    df_mp_c   = df_mp.copy()
    df_devol  = df_mp_c[df_mp_c["status_detail"].isin(SD_DEV)]
    df_recl_m = df_mp_c[df_mp_c["status_detail"].isin({"reconciled","compensated"})]
    df_nc     = df_mp_c[df_mp_c["status_detail"] == "not_reconciled"]
    df_ref    = df_mp_c[df_mp_c["status_detail"] == "refunded"]

    _estado = df_port_u.get("estado", pd.Series("", index=df_port_u.index)).astype(str)
    df_cancel = df_port_u[_estado.str.match(r"(?i)^(cancelada|pacote cancelado|venda cancelada|você cancelou)", na=False)].copy()
    df_cancel["valor_reemb"] = df_cancel["cancelamentos_reembolsos_brl"].clip(upper=0).abs()

    df_revert = df_dev[(df_dev["perda_bruta"] > 0) & (df_dev["recuperado_ml"] >= df_dev["perda_bruta"] * 0.90)]

    def top(df, idcol, cols):
        d = df.head(n)[[idcol] + cols].copy()
        d.columns = ["order_id"] + cols
        return d

    return {
        "reclamacoes":        top(df_dev,    "order_id",    ["perda_bruta","recuperado_ml","taxa_ml_retida","perda_liquida","mantido"]),
        "devolucoes_bpp":     top(df_devol,  "mp_order_id", ["status_detail","mp_valor"]),
        "cancelamentos":      top(df_cancel.sort_values("data_venda", ascending=False), "order_id", ["estado","valor_reemb","total_brl"]),
        "mediacoes":          top(df_recl_m, "mp_order_id", ["status_detail","mp_valor"]),
        "nao_conciliados":    top(df_nc,     "mp_order_id", ["status_detail","mp_valor"]),
        "cancel_diretos_mp":  top(df_ref,    "mp_order_id", ["status_detail","mp_valor"]),
        "revertidos":         top(df_revert, "order_id",    ["perda_bruta","recuperado_ml"]),
    }


# ── 2. Validação via ML API ───────────────────────────────────────────────────

def check_api(oid) -> dict:
    out = {"api_status": "—", "api_pay_detail": "—", "api_total": None, "api_devolvido": None}
    try:
        o = ml_client.get_order(int(oid))
        if not o:
            return {**out, "api_status": "order_not_found"}
        pays = o.get("payments") or [{}]
        p0 = pays[0] if pays else {}
        out["api_status"]     = str(o.get("status") or "—")
        out["api_pay_detail"] = str(p0.get("status_detail") or "—")
        out["api_total"]      = float(o.get("total_amount") or 0)
        out["api_devolvido"]  = float(p0.get("transaction_amount_refunded") or 0)
    except Exception as exc:
        out["api_status"] = f"erro:{type(exc).__name__}"
    return out


# ── 3. Validação via plataforma web (RPA) ─────────────────────────────────────

_RE_TOTAL = re.compile(r"Total\s*\n\s*(-?\s?R\$\s?[\d\.\,]+)")


def _logado(url: str) -> bool:
    return ("mercadolivre.com.br" in url
            and "login" not in url and "registration" not in url
            and "signin" not in url and "account-verification" not in url
            and "mercadolibre.com" not in url)


def ensure_login(page) -> None:
    LISTA = "https://www.mercadolivre.com.br/vendas/omni/lista"
    page.goto(LISTA, timeout=60000)
    page.wait_for_timeout(4000)
    if not _logado(page.url) or "vendas" not in page.url:
        print("\n" + "!" * 70)
        print("!!  FAÇA LOGIN NA JANELA DO NAVEGADOR — sem pressa, nada recarrega.  !!")
        print("!!  O script detecta sozinho quando terminar (até 10 min).          !!")
        print("!" * 70 + "\n", flush=True)
        # espera PASSIVA: nunca navega/recarrega enquanto o usuário digita
        for _ in range(120):
            time.sleep(5)
            try:
                if _logado(page.url) and "login" not in page.url:
                    break
            except Exception:
                continue
        else:
            raise TimeoutError("Login não concluído em 10 minutos.")
        # navegação única de confirmação, só depois do login detectado
        page.goto(LISTA, timeout=60000)
        page.wait_for_timeout(3000)
        if not _logado(page.url):
            raise TimeoutError("Sessão não ficou ativa após o login.")
    print("  ✓ sessão Meli ativa", flush=True)


def check_web(page, oid, card: str) -> dict:
    out = {"web_total": "—", "web_shot": "—"}
    try:
        page.goto(VENDA_URL.format(oid=oid), timeout=60000)
        page.wait_for_timeout(3500)
        body = page.inner_text("body")
        m = _RE_TOTAL.findall(body)
        if m:
            out["web_total"] = m[-1].replace("\xa0", " ").strip()
        shot_dir = SHOTS_DIR / card
        shot_dir.mkdir(parents=True, exist_ok=True)
        shot = shot_dir / f"{oid}.png"
        page.screenshot(path=str(shot))
        out["web_shot"] = str(shot.relative_to(ROOT))
    except Exception as exc:
        out["web_total"] = f"erro:{type(exc).__name__}"
    return out


# ── 4. Veredito por card (semântica de negócio) ───────────────────────────────

def verdict(card: str, row: dict) -> str:
    d   = str(row.get("api_pay_detail") or "")
    st  = str(row.get("api_status") or "")
    dev = row.get("api_devolvido")
    dev = float(dev) if dev is not None else None

    if "order_not_found" in st:
        return "⚪ sem par na API (id só existe no MP)"

    if card == "reclamacoes":
        liq  = float(row.get("perda_liquida") or 0)
        mant = float(row.get("mantido") or 0)
        if mant > 0:
            return "✅ mantido confirmado" if d == "accredited" and (dev or 0) < 0.01 else f"❌ esperava accredited/sem devolução, API={d}/dev={dev}"
        if liq > 5:
            return "✅ perda confirmada (comprador reembolsado)" if (dev or 0) > 0.01 or d in ("refunded","partially_refunded") else f"⚠ perda no painel mas API={d}, devolvido={dev}"
        return "✅ revertida confirmada (ML cobriu)" if d.startswith(("bpp_", "partially_bpp")) or d in ("refunded","by_payer") else f"⚠ revertida mas API={d}"

    if card in ("devolucoes_bpp", "revertidos"):
        return "✅ proteção ML confirmada" if d.startswith(("bpp_", "partially_bpp")) or d == "by_payer" else f"⚠ API={d}"

    if card == "cancelamentos":
        ok_status = st == "cancelled"
        reemb = float(row.get("valor_reemb") or 0)
        ok_val = dev is None or reemb <= 0.01 or abs((dev or 0) - reemb) <= max(1.0, reemb * 0.35)
        if ok_status and ok_val:
            return "✅ cancelamento confirmado"
        if ok_status:
            return f"⚠ cancelado, mas reembolso difere (painel={reemb:.2f} API={dev})"
        return f"❌ status API={st} (esperava cancelled)"

    if card == "mediacoes":
        return "✅ mediação confirmada" if d in ("reconciled","compensated") or d.startswith("bpp_") else f"⚠ API={d}"

    if card == "nao_conciliados":
        return "✅ perda confirmada" if d in ("not_reconciled","refunded","accredited") else f"⚠ API={d}"

    if card == "cancel_diretos_mp":
        return "✅ reembolso direto confirmado" if d in ("refunded","partially_refunded") or st == "cancelled" else f"⚠ API={d}/{st}"

    return "—"


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Valida amostras do painel contra ML API + plataforma web.")
    ap.add_argument("--n", type=int, default=10, help="amostras por card (padrão 10)")
    ap.add_argument("--sem-web", action="store_true", dest="sem_web", help="pula o RPA web (só API)")
    args = ap.parse_args()

    print("=" * 70)
    print(f"VALIDAÇÃO POR AMOSTRAGEM — {args.n} primeiros de cada card")
    print("=" * 70)

    print("\n[1/3] Montando amostras (mesma ordem dos modais)…")
    samples = build_samples(args.n)
    for card, df in samples.items():
        print(f"  {card:20s} {len(df):2d} pedidos")

    page = ctx = pw = None
    if not args.sem_web:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False,
            viewport={"width": 1500, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ensure_login(page)

    print("\n[2/3] Validando pedido a pedido…")
    all_rows: list[dict] = []
    for card, df in samples.items():
        for _, r in df.iterrows():
            oid = r["order_id"]
            row = {k: (round(float(v), 2) if isinstance(v, float) else v) for k, v in r.items()}
            row["card"] = card
            row.update(check_api(oid))
            if page is not None:
                row.update(check_web(page, oid, card))
            row["veredito"] = verdict(card, row)
            all_rows.append(row)
            icon = row["veredito"][:1]
            print(f"  [{card:18s}] {oid}  API={row['api_pay_detail']:<22s} {icon}", flush=True)

    if ctx is not None:
        ctx.close()
        pw.stop()

    print("\n[3/3] Gerando relatório…")
    out = ROOT / "reports" / f"validacao_amostras_{TODAY}.md"
    lines = [
        f"# Validação por Amostragem — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        f"{args.n} primeiros resultados de cada card do painel, validados contra a **ML API**"
        + ("" if args.sem_web else " e a **plataforma web** (screenshots em `_rpa_meli_valida/`)") + ".",
        "",
    ]
    ok = sum(1 for r in all_rows if r["veredito"].startswith("✅"))
    neutro = sum(1 for r in all_rows if r["veredito"].startswith("⚪"))
    lines.append(f"**Resultado: {ok}/{len(all_rows)} confirmados** ({neutro} sem par na API, "
                 f"{len(all_rows)-ok-neutro} a revisar)\n")

    for card in samples:
        rows = [r for r in all_rows if r["card"] == card]
        if not rows:
            continue
        lines.append(f"\n## {card} ({len(rows)})\n")
        cols = [c for c in rows[0].keys() if c not in ("card",)]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "---|" * len(cols))
        for r in rows:
            lines.append("| " + " | ".join(str(r.get(c, "—")) for c in cols) + " |")

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"  ✓ relatório: {out}")
    print(f"\n  Confirmados: {ok}/{len(all_rows)}  |  Sem par API: {neutro}  |  A revisar: {len(all_rows)-ok-neutro}")


if __name__ == "__main__":
    main()
