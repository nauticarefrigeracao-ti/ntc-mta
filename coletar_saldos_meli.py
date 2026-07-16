"""Coletor de saldos REAIS do Meli — RPA pedido a pedido, campo a campo.
========================================================================
Para cada pedido em disputa/cancelado do período, abre a página da venda no
Meli e extrai o painel 'Detalhe do recebimento' COMPLETO:

    Preço do produto | Tarifa de venda | Envios | Cancelamentos/Devoluções | TOTAL

Grava tudo em `meli_page_saldos` (Neon). O dashboard passa a usar esse Total
como Saldo Final (fonte = a própria plataforma → paridade por construção),
com o snapshot da base `orders` apenas como fallback marcado.

Uso:
    python scripts/coletar_saldos_meli.py --de 2026-05-01 --ate 2026-05-31
    python scripts/coletar_saldos_meli.py --de 2026-05-01 --ate 2026-05-31 --max 400
    python scripts/coletar_saldos_meli.py --ids 2000016230577678 2000016291938344

Requer sessão salva em _rpa_meli_profile/ (roda o validar_amostras_meli.py 1ª vez).
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

from src.db.connection import get_db_connection
from validar_amostras_meli import ensure_login, _parse_brl, PROFILE_DIR, VENDA_URL, _ERROS_MELI

_DDL = """
CREATE TABLE IF NOT EXISTS meli_page_saldos (
    order_id      BIGINT PRIMARY KEY,
    coletado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    produto       NUMERIC(14,2),
    tarifa_venda  NUMERIC(14,2),
    envios        NUMERIC(14,2),
    cancelamentos NUMERIC(14,2),
    parcelamento  NUMERIC(14,2),
    total         NUMERIC(14,2),
    painel_titulo TEXT,
    status_pagina TEXT,
    detalhes      TEXT
)
"""
_DDL_MIG = "ALTER TABLE meli_page_saldos ADD COLUMN IF NOT EXISTS detalhes TEXT"

# labels do painel lateral do Meli → coluna; regex tolerante a variações
_CAMPOS = [
    ("produto",       re.compile(r"Preço dos? produtos?\s*\n\s*(-?\s?R\$\s?[\d\.\,]+)")),
    ("tarifa_venda",  re.compile(r"Tarifa de venda total\s*(?:\(\?\)\s*)?\n\s*(-?\s?R\$\s?[\d\.\,]+)")),
    ("envios",        re.compile(r"Envios\s*\n\s*(-?\s?R\$\s?[\d\.\,]+)")),
    ("cancelamentos", re.compile(r"(?:Cancelamentos|Devoluções)\s*\n\s*(-?\s?R\$\s?[\d\.\,]+)")),
    ("parcelamento",  re.compile(r"Taxa de parcelamento e acréscimo\s*\n\s*(-?\s?R\$\s?[\d\.\,]+)")),
]
_RE_TOTAL_FIM = re.compile(r"Total\s*\n\s*(-?\s?R\$\s?[\d\.\,]+)")
_RE_TITULO    = re.compile(r"(Recebimento devolvido|Recebimento em mediação|Pagamento aprovado|Detalhe do recebimento)")


def selecionar_ids(de: str, ate: str, max_n: int, recolher: bool = False) -> list[int]:
    """Pedidos que importam: em disputa (claim/devolução) ou cancelados no período."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
            cur.execute(_DDL_MIG)
            conn.commit()
            cur.execute("""
                WITH alvo AS (
                    SELECT DISTINCT o.order_id::bigint AS oid, o.data_venda
                    FROM orders o
                    WHERE o.data_venda >= %s::timestamptz AND o.data_venda < %s::timestamptz + interval '1 day'
                      AND (
                        EXISTS (SELECT 1 FROM ml_devolucoes m WHERE m.order_id = o.order_id::bigint)
                        OR EXISTS (SELECT 1 FROM mp_transactions t WHERE t.order_id = o.order_id::bigint)
                        OR o.estado ILIKE 'Cancelada%%' OR o.estado ILIKE 'Pacote cancelado%%'
                        OR o.estado ILIKE 'Venda cancelada%%' OR o.estado ILIKE 'Você cancelou%%'
                        OR o.cancelamentos_reembolsos_brl::numeric <> 0
                      )
                )
                SELECT oid FROM alvo
                WHERE %s OR oid NOT IN (
                    SELECT order_id FROM meli_page_saldos
                    WHERE coletado_em > NOW() - interval '20 hours'
                      AND detalhes IS NOT NULL
                )
                ORDER BY (SELECT data_venda FROM alvo a2 WHERE a2.oid = alvo.oid) DESC
                LIMIT %s
            """, (de, ate, recolher, max_n))
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def extrair(page, oid: int) -> dict:
    out: dict = {"order_id": oid, "status_pagina": "ok"}
    page.goto(VENDA_URL.format(oid=oid), timeout=60000)
    page.wait_for_timeout(3200)
    body = page.inner_text("body")
    for _ in range(2):  # F5 nos erros intermitentes do Meli
        if not any(e.lower() in body.lower() for e in _ERROS_MELI) and _RE_TOTAL_FIM.search(body):
            break
        page.reload(timeout=60000)
        page.wait_for_timeout(4000)
        body = page.inner_text("body")

    # EXPANDE todas as seções recolhidas do painel (Tarifa de devolução,
    # Cancelamento de tarifa etc. só aparecem no texto quando expandidas)
    try:
        for el in page.locator('[aria-expanded="false"]').all()[:14]:
            try:
                el.click(timeout=800)
                page.wait_for_timeout(120)
            except Exception:
                pass
        page.wait_for_timeout(400)
        body = page.inner_text("body")
    except Exception:
        pass

    t = _RE_TITULO.search(body)
    out["painel_titulo"] = t.group(1) if t else None
    for campo, rx in _CAMPOS:
        m = rx.search(body)
        out[campo] = _parse_brl(m.group(1)) if m else None
    m = _RE_TOTAL_FIM.findall(body)
    out["total"] = _parse_brl(m[-1]) if m else None
    if out["total"] is None:
        out["status_pagina"] = "sem_total"

    # extrato COMPLETO do painel (linha a linha, expandido) — do 1º campo até o Total
    ini = body.find("Preço do produto")
    if ini == -1:
        ini = body.find("Preço dos produtos")
    mt = list(_RE_TOTAL_FIM.finditer(body))
    if ini != -1 and mt:
        out["detalhes"] = body[ini:mt[-1].end()][:2400]
    else:
        out["detalhes"] = None
    return out


def salvar(rows: list[dict]) -> None:
    if not rows:
        return
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
            cur.execute(_DDL_MIG)
            for r in rows:
                cur.execute("""
                    INSERT INTO meli_page_saldos
                        (order_id, produto, tarifa_venda, envios, cancelamentos,
                         parcelamento, total, painel_titulo, status_pagina, detalhes, coletado_em)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                    ON CONFLICT (order_id) DO UPDATE SET
                        produto=EXCLUDED.produto, tarifa_venda=EXCLUDED.tarifa_venda,
                        envios=EXCLUDED.envios, cancelamentos=EXCLUDED.cancelamentos,
                        parcelamento=EXCLUDED.parcelamento, total=EXCLUDED.total,
                        painel_titulo=EXCLUDED.painel_titulo,
                        status_pagina=EXCLUDED.status_pagina,
                        detalhes=EXCLUDED.detalhes, coletado_em=NOW()
                """, (r["order_id"], r.get("produto"), r.get("tarifa_venda"),
                      r.get("envios"), r.get("cancelamentos"), r.get("parcelamento"),
                      r.get("total"), r.get("painel_titulo"), r.get("status_pagina"),
                      r.get("detalhes")))
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Coleta saldo real (Total) do Meli, pedido a pedido.")
    ap.add_argument("--de",  default="2026-05-01")
    ap.add_argument("--ate", default="2026-05-31")
    ap.add_argument("--max", type=int, default=500)
    ap.add_argument("--ids", nargs="*", type=int, help="ids específicos (ignora período)")
    ap.add_argument("--ids-file", dest="ids_file", help="arquivo com um id por linha (ignora período)")
    ap.add_argument("--recolher", action="store_true", help="recoleta mesmo os já coletados")
    args = ap.parse_args()

    ids = args.ids or []
    if args.ids_file:
        ids += [int(l) for l in Path(args.ids_file).read_text(encoding="utf-8").split() if l.strip().isdigit()]
    ids = ids or selecionar_ids(args.de, args.ate, args.max, recolher=args.recolher)
    print(f"{len(ids):,} pedidos a coletar ({args.de} → {args.ate})", flush=True)
    if not ids:
        print("nada a coletar — tudo já tem saldo fresco (<20h)")
        return

    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    kwargs = dict(headless=False, viewport={"width": 1400, "height": 900},
                  args=["--disable-blink-features=AutomationControlled"])
    try:
        ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), channel="chrome", **kwargs)
    except Exception:
        ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), **kwargs)
    page = ensure_login(ctx.pages[0] if ctx.pages else ctx.new_page())

    buf: list[dict] = []
    ok = falhas = 0
    t0 = time.monotonic()
    for i, oid in enumerate(ids, 1):
        try:
            r = extrair(page, oid)
            buf.append(r)
            ok += 1 if r.get("total") is not None else 0
            falhas += 0 if r.get("total") is not None else 1
        except Exception as exc:
            buf.append({"order_id": oid, "status_pagina": f"erro:{type(exc).__name__}"})
            falhas += 1
        if len(buf) >= 25:
            salvar(buf); buf = []
        if i % 25 == 0:
            dt = time.monotonic() - t0
            print(f"  {i}/{len(ids)}  ok={ok} falhas={falhas}  ({dt/i:.1f}s/pedido, "
                  f"restam ~{(len(ids)-i)*dt/i/60:.0f} min)", flush=True)
    salvar(buf)
    ctx.close(); pw.stop()
    print(f"\nFIM: {ok}/{len(ids)} com Total coletado | {falhas} falhas", flush=True)


if __name__ == "__main__":
    main()
