"""VALIDACAO E2E — item por item, com paridade real contra o Mercado Livre.

Para a reuniao com o chefe. Nao afirma nada que nao tenha sido medido.

Confere, para CADA caso notificado:
  1. o que o Slack disse (slack_notificados + o que o montador gera)
  2. o que o painel mostra (ml_devolucoes)
  3. o que o Mercado Livre diz AGORA (API)
  4. se o link abre de verdade (API /orders)

E, para o Canvas: se cada linha do Quadro corresponde a um caso real e aberto.
"""
import json
import logging
import os
import pathlib
import sys
import tomllib
from collections import Counter
from datetime import datetime, timezone

d = tomllib.loads(pathlib.Path(r"C:\Users\Pichau\analise_progress\.streamlit\secrets.toml").read_text(encoding="utf-8"))


def _f(o, k):
    if isinstance(o, dict):
        for a, b in o.items():
            if a == k and isinstance(b, str):
                return b
            r = _f(b, k)
            if r:
                return r
    return None


if not os.environ.get("ML_NEON_URL"):
    os.environ["ML_NEON_URL"] = _f(d, "ML_NEON_URL") or _f(d, "NEON_URL") or _f(d, "url")
sys.path.insert(0, r"C:\Users\Pichau\ntc-mta")
os.chdir(r"C:\Users\Pichau\ntc-mta")
logging.disable(logging.CRITICAL)

from src.api.ml_client import get_claim, get_order
from src.db.connection import get_db_connection, dict_cursor
import slack_notify as sn

LIMITE = int(sys.argv[1]) if len(sys.argv) > 1 else 40

conn = get_db_connection()
with dict_cursor(conn) as cur:
    cur.execute("""
        SELECT DISTINCT ON (sn.claim_id)
               sn.claim_id, sn.status AS chave_slack, sn.avisado_em,
               d.order_id, d.claim_status, d.claim_stage, d.claim_type,
               d.reason_label, d.item_title, d.item_sku, d.order_total,
               d.date_created
        FROM slack_notificados sn
        JOIN ml_devolucoes d ON d.claim_id = sn.claim_id
        ORDER BY sn.claim_id, sn.avisado_em DESC
    """)
    todos = cur.fetchall()

todos.sort(key=lambda r: r["avisado_em"], reverse=True)
amostra = todos[:LIMITE]

print("=" * 108)
print(f"VALIDACAO E2E — {len(amostra)} casos (de {len(todos)} notificados) — "
      f"{datetime.now().strftime('%d/%m/%Y %H:%M')}")
print("=" * 108)
print(f"{'claim':<12}{'pedido':<18}{'painel':<16}{'ML agora':<16}{'link':<7}{'SKU':<11}{'valor':<12}{'veredito'}")
print("-" * 108)

cont = Counter()
problemas = []

for r in amostra:
    claim_id = r["claim_id"]
    oid = r["order_id"]

    c = get_claim(claim_id)
    if not c:
        cont["api_sem_resposta"] += 1
        print(f"{claim_id:<12}{str(oid):<18}{'-':<16}{'SEM RESPOSTA':<16}")
        continue

    st_ml, stg_ml = c.get("status") or "-", c.get("stage") or "-"
    st_p, stg_p = r["claim_status"] or "-", r["claim_stage"] or "-"

    link_ok = get_order(oid) is not None if oid else False
    tem_sku = bool(r["item_sku"])
    tem_valor = bool(r["order_total"])

    falhas = []
    if st_p != st_ml:
        falhas.append(f"status painel={st_p} ML={st_ml}")
    if stg_p != stg_ml:
        falhas.append(f"etapa painel={stg_p} ML={stg_ml}")
    if not link_ok:
        falhas.append("link 404")
    if not tem_sku:
        falhas.append("sem SKU")
    if not tem_valor:
        falhas.append("sem valor")

    # o que o Slack MOSTRARIA hoje para esse caso.
    # So e problema se o rotulo for CODIGO do ML (PDD9952, PNR3837...).
    # reason_label que ja e texto humano DEVE aparecer -- a primeira versao
    # deste check acusava 36 de 40 casos corretos como defeito.
    texto, _ = sn.montar_mensagem(r)
    rotulo = str(r["reason_label"] or "").strip()
    if rotulo and sn._RE_CODIGO_MOTIVO.match(rotulo) and rotulo in texto:
        falhas.append("motivo cru no texto")

    if falhas:
        cont["com_problema"] += 1
        problemas.append((claim_id, oid, falhas))
        veredito = "; ".join(falhas)[:38]
    else:
        cont["ok"] += 1
        veredito = "OK"

    print(f"{claim_id:<12}{str(oid):<18}{st_p + '/' + stg_p:<16}{st_ml + '/' + stg_ml:<16}"
          f"{'abre' if link_ok else '404':<7}{(r['item_sku'] or '—'):<11}"
          f"{('R$ ' + f'{float(r['order_total'] or 0):,.2f}') if tem_valor else '—':<12}{veredito}")

print()
print("=" * 108)
print("RESUMO")
print("=" * 108)
tot = sum(cont.values())
for k, v in cont.most_common():
    print(f"  {k:<24}{v:>4}  ({100*v/tot:.0f}%)")

# ── Canvas: cada linha corresponde a caso real? ─────────────────────────────
print()
print("=" * 108)
print("CANVAS — cada item do Quadro e um caso real e aberto?")
print("=" * 108)
with dict_cursor(conn) as cur:
    cur.execute("""
        SELECT claim_id, order_id, claim_status, claim_stage, reason_label,
               item_title, item_sku, order_total, date_created
        FROM ml_devolucoes
        WHERE claim_status = 'opened'
           OR (claim_status = 'closed' AND date_updated ~ '^[0-9]{4}-'
               AND date_updated::timestamptz > NOW() - interval '24 hours')
        ORDER BY date_updated DESC NULLS LAST LIMIT 300
    """)
    rows_canvas = cur.fetchall()

a_fazer = [x for x in rows_canvas if sn.classificar_kanban(x) == "a_fazer"]
print(f"  A Fazer no Canvas: {len(a_fazer)}")
for x in a_fazer:
    c = get_claim(x["claim_id"])
    st = (c or {}).get("status")
    stg = (c or {}).get("stage")
    bate = (st == x["claim_status"] and stg == x["claim_stage"])
    abre = get_order(x["order_id"]) is not None
    print(f"    claim {x['claim_id']} | {x['item_sku'] or '—':<10} | "
          f"ML={st}/{stg} | {'bate' if bate else 'DIVERGE'} | "
          f"link {'abre' if abre else '404'}")

conn.close()
print()
print("=" * 108)
if problemas:
    print(f"PROBLEMAS ({len(problemas)}):")
    for claim_id, oid, fs in problemas[:15]:
        print(f"  claim {claim_id} (pedido {oid}): {'; '.join(fs)}")
else:
    print("NENHUM PROBLEMA ENCONTRADO nos casos verificados.")

