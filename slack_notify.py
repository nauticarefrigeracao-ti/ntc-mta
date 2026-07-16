"""Notificador Slack do SAC — reclamações/devoluções do ML no canal #sac.
================================================================================
Lê a URL do webhook de um ARQUIVO LOCAL (fora do repo, nunca commitado):
    C:\\Users\\Pichau\\slack_webhook.txt   (uma linha: https://hooks.slack.com/...)

O daemon (ml_live_poll) chama `--once` a cada ciclo: reclamação ABERTA ainda
não notificada → mensagem com produto, valor, motivo e prazo de resposta.
Estado em slack_notificados (Neon) — cada claim avisa UMA vez por status.

Uso:
    python scripts/slack_notify.py --test    # mensagem de resumo (demo)
    python scripts/slack_notify.py --once    # notifica reclamações novas
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WEBHOOK_FILE = Path(r"C:\Users\Pichau\slack_webhook.txt")

_DDL = """
CREATE TABLE IF NOT EXISTS slack_notificados (
    claim_id BIGINT NOT NULL,
    status   TEXT NOT NULL,
    avisado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (claim_id, status)
)
"""


def _webhook() -> str | None:
    try:
        url = WEBHOOK_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        return url if url.startswith("https://hooks.slack.com/") else None
    except Exception:
        return None


def enviar(texto: str, blocos: list | None = None) -> bool:
    url = _webhook()
    if not url:
        return False
    payload: dict = {"text": texto}
    if blocos:
        payload["blocks"] = blocos
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as exc:
        print(f"slack: falha no envio: {type(exc).__name__}: {exc}")
        return False


def _fmt_brl(v) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"


def notificar_reclamacoes() -> int:
    """Reclamações ABERTAS ainda não avisadas → 1 mensagem cada no #sac."""
    from src.db.connection import get_db_connection
    conn = get_db_connection()
    enviadas = 0
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
            conn.commit()
            cur.execute("""
                SELECT d.claim_id, d.order_id, d.item_title, d.item_sku,
                       d.order_total, d.reason_label, d.claim_stage, d.date_created
                FROM ml_devolucoes d
                LEFT JOIN slack_notificados s
                       ON s.claim_id = d.claim_id AND s.status = d.claim_status
                WHERE d.claim_status = 'opened' AND s.claim_id IS NULL
                ORDER BY d.date_created DESC
                LIMIT 15
            """)
            rows = cur.fetchall()
            for cid, oid, titulo, sku, total, motivo, stage, criada in rows:
                prazo = ""
                try:
                    lim = criada + timedelta(days=2)
                    resta = lim - datetime.now(timezone.utc)
                    h = int(resta.total_seconds() // 3600)
                    prazo = (f"⏰ *restam ~{h}h para responder*" if h > 0
                             else "🚨 *PRAZO DE RESPOSTA ESTOURADO*")
                except Exception:
                    pass
                etapa = {"claim": "Reclamação direta", "dispute": "Mediação do ML",
                         "recontact": "Recontato"}.get(str(stage), str(stage or "Reclamação"))
                txt = (f":rotating_light: *Nova reclamação no Mercado Livre* — {etapa}\n"
                       f"*{titulo or 'Produto'}* (SKU {sku or '—'}) · valor {_fmt_brl(total)}\n"
                       f"Motivo: _{motivo or 'não informado'}_\n{prazo}\n"
                       f"➡️ <https://www.mercadolivre.com.br/vendas/{oid}/detalhe|Pedido {oid} — clique para ATENDER a reclamação>"
                       f" _(logada no ML, abre direto na venda com o botão de atender)_")
                if enviar(txt):
                    cur.execute("INSERT INTO slack_notificados (claim_id, status) VALUES (%s,'opened') "
                                "ON CONFLICT DO NOTHING", (cid,))
                    conn.commit()
                    enviadas += 1
    finally:
        conn.close()
    return enviadas


def teste() -> None:
    """Mensagem de resumo — demonstração ao vivo."""
    from src.db.connection import get_db_connection
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COALESCE(SUM(order_total),0) FROM ml_devolucoes WHERE claim_status='opened'")
        n, v = cur.fetchone()
    conn.close()
    ok = enviar(
        f":bar_chart: *Painel de Devoluções — Náutica Refrigeração*\n"
        f"Neste momento: *{n} disputas em andamento*, {_fmt_brl(v)} em jogo.\n"
        f"A partir de agora, toda reclamação nova do Mercado Livre chega aqui "
        f"no *#sac* com produto, valor, motivo e prazo de resposta (janela de 2 dias).\n"
        f"<https://ntc-mta.streamlit.app|Abrir o painel completo>")
    print("✓ mensagem de teste enviada ao #sac" if ok else
          "✗ não enviou — confira C:\\Users\\Pichau\\slack_webhook.txt")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    if not _webhook():
        print("slack: sem webhook (C:\\Users\\Pichau\\slack_webhook.txt) — nada a fazer")
        return
    if args.test:
        teste()
    if args.once or not args.test:
        n = notificar_reclamacoes()
        print(f"✓ {n} reclamações notificadas no #sac")


if __name__ == "__main__":
    main()
