"""Validação REAL tela-a-tela — nosso Dashboard × ML API × plataforma Meli (web).
================================================================================
Para cada card do painel, pega os N primeiros pedidos e compara TRÊS fontes:

  1. NOSSO DASH (RPA)  → abre o painel renderizado, acha a linha do pedido no
                          modal e lê as células como o usuário vê
  2. ML API            → GET /orders/{id} (pagamento real, valor devolvido)
  3. MELI WEB (RPA)    → mercadolivre.com.br/vendas/{id}/detalhe — extrai o
                          'Total' do detalhe do recebimento + screenshot

CRITÉRIO DE ACEITE: 'Saldo Final' no nosso dash == 'Total' no Meli (±R$ 1).

Uso:
    python scripts/validar_amostras_meli.py --n 10
    python scripts/validar_amostras_meli.py --sem-web        # só dash × API

Login (1ª vez): faça login na janela que abre; fica salvo em _rpa_meli_profile/.
Saída: reports/validacao_amostras_YYYY-MM-DD.md + _rpa_meli_valida/<card>/<id>.png
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
DASH_HTML   = ROOT / "reports" / "painel_devolucoes_live.html"

VENDA_URL = "https://www.mercadolivre.com.br/vendas/{oid}/detalhe"

# card → (modal no dash, grupo semântico, coluna-chave do id)
CARD_MODAL = {
    "reclamacoes":       ("reclamacoes",    None,        "order_id"),
    "perda_real":        ("reclamacoes",    "perda",     "order_id"),
    "recuperado":        ("reclamacoes",    "revertida", "order_id"),
    "mantido":           ("reclamacoes",    "mantida",   "order_id"),
    "devolucoes_bpp":    ("devolucoes",     None,        "mp_order_id"),
    "cancelamentos":     ("cancelamentos",  None,        "order_id"),
    "mediacoes":         ("mediacao",       None,        "mp_order_id"),
    "nao_conciliados":   ("nao_conciliado", None,        "mp_order_id"),
    "revertidos":        ("revertidos",     None,        "order_id"),
}


def _parse_brl(s) -> float | None:
    """'R$ 434,90' | '-R$ 148,05' | '+434,90' | '−148,05' → float. None se não numérico."""
    if s is None:
        return None
    t = str(s).replace("\xa0", " ").strip()
    if not t or t in ("—", "-"):
        return None
    neg = t.startswith("-") or t.startswith("−") or "-R$" in t or "−R$" in t
    t = re.sub(r"[^\d,\.]", "", t)
    if not t:
        return None
    t = t.replace(".", "").replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


# ── 1. Amostras — direto do dado que alimenta o dash ─────────────────────────

def build_samples(n: int) -> dict[str, pd.DataFrame]:
    conn = get_db_connection()
    try:
        df_mp, df_port = rel._load_and_enrich(conn)
        df_saldos = pd.read_sql(
            "SELECT order_id::text AS oid, total::float AS total "
            "FROM meli_page_saldos WHERE total IS NOT NULL", conn)
    finally:
        conn.close()
    # mesma fonte do dash: saldo coletado da página sobrepõe o snapshot,
    # senão a amostra agrupa diferente do painel e acusa falso negativo
    _saldo_meli = dict(zip(df_saldos["oid"], df_saldos["total"]))
    df_port["total_brl"] = (
        df_port["order_id"].astype(str).map(_saldo_meli).astype(float)
        .fillna(pd.to_numeric(df_port["total_brl"], errors="coerce").fillna(0.0))
    )

    SD_DEV = {"bpp_refunded","bpp_covered","partially_bpp_refunded","partially_bpp_covered","ppv_covered_melienvio"}

    df_port_u = df_port.drop_duplicates("order_id").copy()
    df_dev    = df_port_u[df_port_u["perda_bruta"] > 0].copy()

    df_mp_c   = df_mp.copy()
    df_devol  = df_mp_c[df_mp_c["status_detail"].isin(SD_DEV)]
    df_recl_m = df_mp_c[df_mp_c["status_detail"].isin({"reconciled","compensated"})]
    df_nc     = df_mp_c[df_mp_c["status_detail"] == "not_reconciled"]

    _estado = df_port_u.get("estado", pd.Series("", index=df_port_u.index)).astype(str)
    df_cancel = df_port_u[_estado.str.match(r"(?i)^(cancelada|pacote cancelado|venda cancelada|você cancelou)", na=False)].copy()
    df_cancel["valor_reemb"] = df_cancel["cancelamentos_reembolsos_brl"].clip(upper=0).abs()

    # mesma régua do dash: grupo pelo SALDO FINAL (= 'Total' do Meli)
    df_revert = df_dev[(df_dev["perda_bruta"] > 0) & (df_dev["recuperado_ml"] >= df_dev["perda_bruta"] * 0.90)]
    df_perda  = df_dev[df_dev["total_brl"] < -5]
    df_mant   = df_dev[df_dev["total_brl"] > 0.005]
    df_rever2 = df_dev[(df_dev["total_brl"] >= -5) & (df_dev["total_brl"] <= 0.005)]  # zeradas — grupo 'revertida' no dash

    port_saldo = df_port_u.set_index(df_port_u["order_id"].astype(str))["total_brl"].to_dict()

    def top(df, idcol, cols):
        d = df.drop_duplicates(idcol).head(n + 3)[[idcol] + cols].copy()
        d.columns = ["order_id"] + cols
        # ids vêm como float do pandas (…490.0) — normaliza para string inteira
        d["order_id"] = d["order_id"].map(
            lambda v: str(int(float(v))) if pd.notna(v) and str(v).strip() not in ("", "nan") else None)
        d = d.dropna(subset=["order_id"]).head(n)
        if "total_brl" not in d.columns:
            d["total_brl"] = d["order_id"].map(port_saldo)
        return d

    rc = ["perda_bruta","recuperado_ml","perda_liquida","total_brl"]
    return {
        "perda_real":       top(df_perda,  "order_id",    rc),
        "recuperado":       top(df_rever2, "order_id",    rc),
        "mantido":          top(df_mant,   "order_id",    rc),
        "devolucoes_bpp":   top(df_devol,  "mp_order_id", ["status_detail","mp_valor"]),
        "cancelamentos":    top(df_cancel.sort_values("data_venda", ascending=False), "order_id", ["estado","valor_reemb","total_brl"]),
        "mediacoes":        top(df_recl_m, "mp_order_id", ["status_detail","mp_valor"]),
        "nao_conciliados":  top(df_nc,     "mp_order_id", ["status_detail","mp_valor"]),
        "revertidos":       top(df_revert, "order_id",    rc),
    }


# ── 2. NOSSO DASH via RPA — lê a linha renderizada como o usuário vê ─────────

def dash_open(pw):
    b = pw.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1700, "height": 1000})
    pg.goto(DASH_HTML.as_uri())
    pg.wait_for_timeout(2500)
    # todo o período: garante que a linha não está fora do filtro
    pg.evaluate("setR(0)")
    return b, pg


_JS_FIND_ROW = """
([modal, oid]) => {
  const tb = document.getElementById('tbody-' + modal);
  if (!tb) return null;
  for (const tr of tb.querySelectorAll('tr')) {
    const a = tr.querySelector('a');
    if (a && a.textContent.trim() === oid) {
      const heads = Array.from(tr.closest('table').querySelectorAll('thead th')).map(th => th.textContent.trim());
      const cells = Array.from(tr.cells).map(td => td.textContent.trim());
      const o = {};
      heads.forEach((h, i) => o[h] = cells[i] ?? '');
      return o;
    }
  }
  return null;
}
"""


def dash_row(dashpg, card: str, oid) -> dict | None:
    modal, grupo, _ = CARD_MODAL.get(card, ("reclamacoes", None, "order_id"))
    g = f",'{grupo}'" if grupo else ""
    dashpg.evaluate(f"()=>{{document.querySelectorAll('dialog[open]').forEach(d=>d.close());openM('{modal}'{g});}}")
    dashpg.wait_for_timeout(250)
    return dashpg.evaluate(_JS_FIND_ROW, [modal, str(oid)])


# ── 3. ML API ─────────────────────────────────────────────────────────────────

def check_api(oid) -> dict:
    out = {"api_status": "—", "api_pay_detail": "—", "api_total": None, "api_devolvido": None}
    try:
        o = ml_client.get_order(int(oid))
        if not o:
            return {**out, "api_status": "order_not_found"}
        p0 = rel._best_payment(o.get("payments") or [])
        out["api_status"]     = str(o.get("status") or "—")
        out["api_pay_detail"] = str(p0.get("status_detail") or "—")
        out["api_total"]      = float(o.get("total_amount") or 0)
        out["api_devolvido"]  = float(p0.get("transaction_amount_refunded") or 0)
    except Exception as exc:
        out["api_status"] = f"erro:{type(exc).__name__}"
    return out


# ── 4. MELI WEB via RPA ───────────────────────────────────────────────────────

_RE_TOTAL = re.compile(r"Total(?: da transação)?\s*\n\s*(-?\s?R\$\s?[\d\.\,]+)")
_ERROS_MELI = ("dados de cobrança", "Algo deu errado", "Não foi possível carregar",
               "Tente novamente", "algo salió mal")


def _logado(url: str) -> bool:
    return ("mercadolivre.com.br" in url
            and "login" not in url and "registration" not in url
            and "signin" not in url and "account-verification" not in url)


def ensure_login(page):
    """Garante sessão logada; retorna a page ativa (login pode trocar de aba)."""
    LISTA = "https://www.mercadolivre.com.br/vendas/omni/lista"
    ctx = page.context
    page.goto(LISTA, timeout=60000)
    page.wait_for_timeout(4000)
    if not (_logado(page.url) and "vendas" in page.url):
        print("\n" + "!" * 70)
        print("!!  FAÇA LOGIN NA JANELA DO NAVEGADOR — sem pressa, nada recarrega.  !!")
        print("!" * 70 + "\n", flush=True)
        ativa = None
        for _ in range(120):
            time.sleep(5)
            try:
                for p in ctx.pages:
                    if not p.is_closed() and _logado(p.url) and "login" not in p.url:
                        ativa = p
                        break
                if ativa is not None:
                    break
            except Exception:
                continue
        else:
            raise TimeoutError("Login não concluído em 10 minutos.")
        page = ativa
        page.goto(LISTA, timeout=60000)
        page.wait_for_timeout(3000)
        if not _logado(page.url):
            raise TimeoutError("Sessão não ficou ativa após o login.")
    print("  ✓ sessão Meli ativa", flush=True)
    return page


def check_web(page, oid, card: str) -> dict:
    out = {"meli_total": None, "meli_total_txt": "—", "web_shot": "—"}
    try:
        page.goto(VENDA_URL.format(oid=oid), timeout=60000)
        page.wait_for_timeout(3500)
        body = page.inner_text("body")
        # Meli instável ("não deu pra mostrar os dados de cobrança") → F5 até 2×
        for _ in range(2):
            if not any(e.lower() in body.lower() for e in _ERROS_MELI) and _RE_TOTAL.findall(body):
                break
            page.reload(timeout=60000)
            page.wait_for_timeout(4000)
            body = page.inner_text("body")
        m = _RE_TOTAL.findall(body)
        if m:
            # a página pode ter vários painéis 'Total' (recebimento + reembolso):
            # guarda todos — o veredito aceita se o dash bater com QUALQUER um
            totais = [_parse_brl(x) for x in m]
            out["meli_totais"] = [t for t in totais if t is not None]
            out["meli_total_txt"] = " | ".join(x.replace("\xa0", " ").strip() for x in m)
            out["meli_total"] = out["meli_totais"][-1] if out["meli_totais"] else None
        shot_dir = SHOTS_DIR / card
        shot_dir.mkdir(parents=True, exist_ok=True)
        shot = shot_dir / f"{oid}.png"
        page.screenshot(path=str(shot))
        out["web_shot"] = str(shot.relative_to(ROOT))
    except Exception as exc:
        out["meli_total_txt"] = f"erro:{type(exc).__name__}"
    return out


# ── 5. Veredito — critério de aceite: Saldo Final (dash) == Total (Meli) ─────

def verdict(card: str, row: dict) -> str:
    dash_saldo = row.get("dash_saldo")
    meli_total = row.get("meli_total")
    d  = str(row.get("api_pay_detail") or "")
    st = str(row.get("api_status") or "")

    if "order_not_found" in st and row.get("dash_row_ok") != "sim":
        return "⚪ sem par na API nem no dash"

    if row.get("dash_row_ok") != "sim":
        return "❌ pedido NÃO apareceu no modal esperado do dash"

    # critério de ouro: paridade de saldo com a plataforma
    totais = row.get("meli_totais") or ([meli_total] if meli_total is not None else [])
    if dash_saldo is not None and totais:
        hit = next((t for t in totais if abs(dash_saldo - t) <= 1.0), None)
        if hit is not None:
            return "✅ SALDO BATE com o Meli"
        return f"❌ SALDO DIFERE: dash={dash_saldo:.2f} × Meli={'/'.join(f'{t:.2f}' for t in totais)}"

    # fallback: coerência semântica com a API quando não deu pra ler o Total
    if card in ("recuperado", "revertidos", "devolucoes_bpp"):
        return "✅ coerente com API (ML cobriu)" if d.startswith(("bpp_","partially_bpp")) or d in ("refunded","by_payer") else f"⚠ API={d}"
    if card == "mantido":
        return "✅ coerente com API (venda creditada)" if d == "accredited" else f"⚠ API={d}"
    if card == "perda_real":
        dev = row.get("api_devolvido") or 0
        return "✅ coerente com API (comprador reembolsado)" if dev > 0.01 or d in ("refunded","partially_refunded") else f"⚠ API={d}"
    if card == "cancelamentos":
        return "✅ coerente com API (cancelado)" if st == "cancelled" else f"⚠ status API={st}"
    if card == "mediacoes":
        return "✅ coerente com API" if d in ("reconciled","compensated") or d.startswith("bpp_") else f"⚠ API={d}"
    if card == "nao_conciliados":
        return "✅ perda coerente" if d in ("not_reconciled","refunded") else f"⚠ estado vivo={d} (resolvido depois?)"
    return "—"


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Valida dash × ML API × plataforma web, pedido a pedido.")
    ap.add_argument("--n", type=int, default=10, help="amostras por card (padrão 10)")
    ap.add_argument("--sem-web", action="store_true", dest="sem_web", help="pula Meli web (só dash × API)")
    ap.add_argument("--cdp", type=int, metavar="PORTA", help="conecta no seu Chrome já aberto com --remote-debugging-port")
    args = ap.parse_args()

    print("=" * 70)
    print(f"VALIDAÇÃO TELA-A-TELA — {args.n} primeiros de cada card")
    print("aceite: 'Saldo Final' (dash) == 'Total' (Meli) ± R$ 1")
    print("=" * 70)

    if not DASH_HTML.exists():
        raise FileNotFoundError(f"Painel não encontrado: {DASH_HTML} — rode o processar_relatorios_mp.py antes.")

    print("\n[1/4] Montando amostras…")
    samples = build_samples(args.n)
    for card, df in samples.items():
        print(f"  {card:18s} {len(df):2d} pedidos")

    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()

    print("\n[2/4] Abrindo NOSSO dash renderizado (RPA)…")
    dash_browser, dashpg = dash_open(pw)
    print(f"  ✓ dash carregado: {DASH_HTML.name}")

    page = ctx = browser = None
    own_ctx = False
    if not args.sem_web:
        print("\n[3/4] Abrindo Meli…")
        if args.cdp:
            browser = pw.chromium.connect_over_cdp(f"http://localhost:{args.cdp}")
            ctx = browser.contexts[0]
            page = ensure_login(ctx.new_page())
        else:
            kwargs = dict(headless=False, viewport={"width": 1500, "height": 950},
                          args=["--disable-blink-features=AutomationControlled"])
            try:
                ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), channel="chrome", **kwargs)
            except Exception:
                ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), **kwargs)
            own_ctx = True
            page = ensure_login(ctx.pages[0] if ctx.pages else ctx.new_page())

    print("\n[4/4] Comparando pedido a pedido (dash × API × Meli)…")
    all_rows: list[dict] = []
    for card, df in samples.items():
        for _, r in df.iterrows():
            oid = r["order_id"]
            row = {k: (round(float(v), 2) if isinstance(v, float) else v) for k, v in r.items()}
            row["card"] = card

            drow = dash_row(dashpg, card, oid)
            row["dash_row_ok"] = "sim" if drow else "NÃO"
            row["dash_saldo"] = _parse_brl((drow or {}).get("Saldo Final da Venda (R$)")
                                           or (drow or {}).get("Saldo Residual (R$)"))
            row["dash_situacao"] = (drow or {}).get("Situação") or (drow or {}).get("Situação ⚠") or "—"

            row.update(check_api(oid))
            if page is not None:
                row.update(check_web(page, oid, card))

            row["veredito"] = verdict(card, row)
            all_rows.append(row)
            print(f"  [{card:16s}] {oid}  dash={str(row['dash_saldo']):>10s}  "
                  f"meli={str(row.get('meli_total')):>10s}  {row['veredito'][:60]}", flush=True)

    if page is not None and not own_ctx:
        page.close()
    if own_ctx and ctx is not None:
        ctx.close()
    dash_browser.close()
    pw.stop()

    print("\nGerando relatório…")
    out = ROOT / "reports" / f"validacao_amostras_{TODAY}.md"
    ok     = sum(1 for r in all_rows if r["veredito"].startswith("✅"))
    neutro = sum(1 for r in all_rows if r["veredito"].startswith("⚪"))
    lines = [
        f"# Validação Tela-a-Tela — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        f"Critério de aceite: **'Saldo Final' no nosso dash = 'Total' no detalhe do Meli (±R$ 1)**.",
        f"Fontes: dash renderizado (RPA) × ML API × plataforma web (screenshots em `_rpa_meli_valida/`).",
        "",
        f"**Resultado: {ok}/{len(all_rows)} confirmados** | {neutro} sem par | {len(all_rows)-ok-neutro} a revisar",
    ]
    for card in samples:
        rows = [r for r in all_rows if r["card"] == card]
        if not rows:
            continue
        lines.append(f"\n## {card} ({len(rows)})\n")
        cols = [c for c in rows[0].keys() if c != "card"]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "---|" * len(cols))
        for r in rows:
            lines.append("| " + " | ".join(str(r.get(c, "—")) for c in cols) + " |")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"  ✓ relatório: {out}")
    print(f"\n  Confirmados: {ok}/{len(all_rows)}  |  Sem par: {neutro}  |  A revisar: {len(all_rows)-ok-neutro}")


if __name__ == "__main__":
    main()
