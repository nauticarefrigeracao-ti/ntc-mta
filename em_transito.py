"""Devolucoes a caminho -- o que chega hoje, e o que nunca vai chegar aqui.

O quadro que a Thayna desenhou comeca por "A CAMINHO". Medido em 06/08/2026:
dos 32 casos abertos, a previsao de entrega estava em ZERO. Nao porque o
Mercado Livre nao da -- porque nunca fomos buscar. O dado mora em dois lugares:

    GET /post-purchase/v2/claims/{claim_id}/returns
        -> shipments[].shipment_id, status, tracking_number, destination

    GET /shipments/{shipment_id}   (header x-format-new: true)
        -> lead_time.estimated_delivery_time.date   <- a previsao
        -> lead_time.shipping_method.name
        -> tracking_method

Duas coisas que este modulo existe para nao deixar passar:

**Nem toda devolucao chega na loja.** A amostra real (claim 5550146826) vai
para o galpao do ML em Cajamar -- `types: [warehouse, triage]`. A Maria nunca
vai bipar essa etiqueta porque o pacote nao passa por ela. Perguntar "ja
chegou?" para caixa que nunca chega ensina a ignorar a pergunta, e ai as que
importam se perdem junto.

**Fuso horario come um dia.** O ML devolve `2026-08-21T23:00:00.000-03:00`.
Lido como UTC vira 22/08 -- a devolucao aparece na lista de amanha e a Maria
procura um pacote que ja chegou ontem.

Uso:
    python em_transito.py --coletar        # busca e grava os casos abertos
    python em_transito.py --hoje           # o que chega (ou atrasou) hoje
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional

sys.path.insert(0, str(Path(__file__).parent))

API = "https://api.mercadolibre.com"

# Tipos de endereco que significam "o pacote NAO passa pela Praia Grande".
# Medidos na resposta real do ML, nao supostos.
TIPOS_FULL = ("warehouse", "triage", "logistic_center")


def destino_do_shipment(shipment: Optional[Mapping[str, Any]]) -> Optional[str]:
    """"loja" (a Maria recebe) ou "full" (o ML tria). None = desconhecido.

    Chutar "loja" faria o Slack cobrar confirmacao de um pacote que talvez
    nunca chegue -- o pior dos dois erros, porque treina a ignorar o aviso.
    """
    dest = (shipment or {}).get("destination") or {}
    if not dest:
        return None

    tipos = ((dest.get("shipping_address") or {}).get("types")) or []
    for t in tipos:
        if any(marca in str(t) for marca in TIPOS_FULL):
            return "full"
    if tipos:
        return "loja"

    nome = str(dest.get("name") or "")
    if any(marca in nome for marca in TIPOS_FULL):
        return "full"
    return "loja" if nome else None


def previsao_do_detalhe(detalhe: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Data prevista, crua como o ML devolve.

    `estimated_delivery_time` as vezes vem nulo e o `limit` nao. Pior data
    conhecida e melhor que data nenhuma -- e e a que nao atrasa a Maria.
    """
    lead = (detalhe or {}).get("lead_time") or {}
    for campo in ("estimated_delivery_time", "estimated_delivery_limit",
                  "estimated_delivery_final"):
        d = (lead.get(campo) or {}).get("date")
        if d:
            return d
    return None


def dia_da_previsao(texto: Optional[str]) -> Optional[date]:
    """O DIA no fuso que o ML mandou -- nunca convertido para UTC.

    `2026-08-21T23:00:00-03:00` lido como UTC vira 22/08. Um dia a mais e a
    devolucao na lista errada.
    """
    if not texto:
        return None
    try:
        return datetime.fromisoformat(str(texto).replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None


def chega_ate(previsao: Optional[str], hoje: date) -> bool:
    """Entra na lista de hoje?

    Atrasado tambem entra: previsao de ontem que nao chegou e MAIS urgente que
    a de hoje, nao menos. Sem previsao NAO entra -- "nao sabemos" nao e "chega
    hoje"; esses vao para uma secao propria, para nao sumirem nem poluirem.
    """
    d = dia_da_previsao(previsao)
    return d is not None and d <= hoje


def pack_do_pedido(pedido: Optional[Mapping[str, Any]]) -> Optional[int]:
    """O numero que a tela do vendedor mostra.

    Venda com mais de um item vive num pack, e e o `pack_id` que aparece como
    "Venda #..." no Meli. Medido: `/orders/2000017686941586` devolve
    `pack_id: 2000014291726681` -- e era 2000014291726681 que o chefe via na
    tela enquanto o Slack dizia 2000017686941586.

    Pack igual ao pedido nao e pack: e o proprio pedido, e guardar isso so
    duplicaria o dado.
    """
    p = pedido or {}
    pack = p.get("pack_id")
    if pack is None:
        return None
    try:
        pack, oid = int(pack), int(p.get("id") or 0)
    except (TypeError, ValueError):
        return None
    return None if pack == oid else pack


def resumo_do_retorno(payload: Optional[Mapping[str, Any]]) -> dict:
    """Achata o retorno no que o card precisa.

    Entre os shipments, o que interessa e o de `type == "return"`: o pacote da
    VENDA tambem aparece, e ele nao e o que volta.
    """
    p = payload or {}
    envios = p.get("shipments") or []
    escolhido = next((s for s in envios if s.get("type") == "return"), None)
    if escolhido is None and len(envios) == 1:
        escolhido = envios[0]
    escolhido = escolhido or {}

    return {
        "return_id": p.get("id"),
        "status": p.get("status"),
        "subtype": p.get("subtype"),
        "status_money": p.get("status_money"),
        "shipment_id": escolhido.get("shipment_id"),
        "shipment_status": escolhido.get("status"),
        "tracking_number": escolhido.get("tracking_number"),
        "destino": destino_do_shipment(escolhido),
    }


# --- I/O -------------------------------------------------------------------

def _get(url: str, tok: str, novo_formato: bool = False,
         tentativas: int = 4) -> Optional[dict]:
    """GET no ML. 403/404 sao respostas legitimas; 429 espera e tenta de novo.

    Buscar pack + retorno + shipment de ~30 casos sao ~90 chamadas em rajada,
    e o ML devolve 429 no meio. Sem o backoff a coleta morre pela metade --
    e meia coleta e pior que nenhuma, porque parece completa.
    """
    cab = {"Authorization": f"Bearer {tok}"}
    if novo_formato:
        cab["x-format-new"] = "true"
    espera = 2.0
    for n in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=cab)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return None
            if e.code == 429 and n < tentativas - 1:
                try:
                    espera = float((e.headers or {}).get("Retry-After") or espera)
                except (TypeError, ValueError):
                    pass
                time.sleep(espera)
                espera *= 2
                continue
            raise
    return None


def coletar(limite: Optional[int] = None) -> dict:
    """Busca previsao e destino de todos os casos abertos e grava."""
    from src.api.ml_client import _token
    from src.db.connection import get_db_connection

    tok = _token()
    if not tok:
        raise RuntimeError("sem token do ML — a coleta pararia calada")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT claim_id, order_id FROM ml_devolucoes
        WHERE claim_status = 'opened' ORDER BY date_updated DESC
    """)
    casos = cur.fetchall()
    if limite:
        casos = casos[:limite]

    contas = {"casos": len(casos), "com_retorno": 0, "com_previsao": 0,
              "loja": 0, "full": 0, "sem_destino": 0, "falhas": 0}

    for claim_id, order_id in casos:
        # O pack vem primeiro: mesmo caso sem devolucao ainda precisa
        # imprimir o numero que a Maria ve na tela do Meli.
        pack = pack_do_pedido(_get(f"{API}/orders/{order_id}", tok))
        if pack:
            cur.execute("UPDATE ml_devolucoes SET pack_id = %s WHERE claim_id = %s",
                        (pack, claim_id))
            contas["com_pack"] = contas.get("com_pack", 0) + 1

        ret = _get(f"{API}/post-purchase/v2/claims/{claim_id}/returns", tok)
        if not ret:
            continue
        r = resumo_do_retorno(ret)
        contas["com_retorno"] += 1

        previsao = transportadora = metodo = None
        historico = atrasos = carrier_url = None
        if r["shipment_id"]:
            sid = r["shipment_id"]
            det = _get(f"{API}/shipments/{sid}", tok, novo_formato=True)
            if det:
                previsao = previsao_do_detalhe(det)
                metodo = det.get("tracking_method")
                transportadora = ((det.get("lead_time") or {})
                                  .get("shipping_method") or {}).get("name")

            # A viagem em etapas, o atraso que o proprio ML declara, e o link
            # de rastreio da transportadora. Sao tres chamadas a mais por
            # caso; o backoff de 429 ja cobre a rajada.
            h = _get(f"{API}/shipments/{sid}/history", tok, novo_formato=True)
            if h:
                historico = json.dumps(h)
                contas["com_historico"] = contas.get("com_historico", 0) + 1
            d = _get(f"{API}/shipments/{sid}/delays", tok, novo_formato=True)
            if d and (d.get("delays") or []):
                atrasos = json.dumps(d["delays"])
                contas["com_atraso"] = contas.get("com_atraso", 0) + 1
            car = _get(f"{API}/shipments/{sid}/carrier", tok, novo_formato=True)
            if car:
                carrier_url = car.get("url")
                transportadora = car.get("name") or transportadora
        if previsao:
            contas["com_previsao"] += 1
        contas[r["destino"] or "sem_destino"] = \
            contas.get(r["destino"] or "sem_destino", 0) + 1

        cur.execute("""
            UPDATE ml_devolucoes SET
                -- Os tipos da tabela nao seguem um padrao: `return_id` e
                -- TEXT, `return_shipment_id` e BIGINT, e a API manda inteiro
                -- nos dois. Sem o cast certo em cada um, COALESCE recusa
                -- ("types integer and text cannot be matched") e a coleta
                -- inteira para -- alto, que e como tem que ser.
                return_id = COALESCE(%(rid)s::text, return_id),
                return_status = COALESCE(%(st)s, return_status),
                return_shipment_id = COALESCE(%(sid)s::bigint, return_shipment_id),
                return_tracking_number = COALESCE(%(trk)s, return_tracking_number),
                return_tracking_status = COALESCE(%(sst)s, return_tracking_status),
                return_estimated_delivery = COALESCE(%(prev)s, return_estimated_delivery),
                return_destino = %(dest)s,
                return_transportadora = COALESCE(%(tp)s, return_transportadora),
                return_metodo = COALESCE(%(mt)s, return_metodo),
                -- Carimbo de quando CONFERIMOS no ML. E o que
                -- permite o card dizer "ha 4 minutos" em vez
                -- de "atualizado 07/08", que nao responde a
                -- pergunta que a Maria tem.
                return_historico = COALESCE(%(hist)s, return_historico),
                return_atrasos = %(atr)s,
                return_carrier_url = COALESCE(%(curl)s, return_carrier_url),
                synced_at = now()
            WHERE claim_id = %(cid)s
        """, {"cid": claim_id, "rid": r["return_id"], "st": r["status"],
              "sid": r["shipment_id"], "trk": r["tracking_number"],
              "sst": r["shipment_status"], "prev": previsao,
              "dest": r["destino"], "tp": transportadora, "mt": metodo,
              "hist": historico, "atr": atrasos, "curl": carrier_url})
    conn.commit()
    return contas


_DDL = """
ALTER TABLE ml_devolucoes ADD COLUMN IF NOT EXISTS return_destino TEXT;
ALTER TABLE ml_devolucoes ADD COLUMN IF NOT EXISTS return_transportadora TEXT;
ALTER TABLE ml_devolucoes ADD COLUMN IF NOT EXISTS return_metodo TEXT;
ALTER TABLE ml_devolucoes ADD COLUMN IF NOT EXISTS pack_id BIGINT;

-- A viagem do pacote. Guardamos o JSON cru do ML de proposito: o card so
-- precisa de quatro etapas, mas quando alguem perguntar "por que esse caso
-- demorou 9 dias" a resposta vai estar aqui inteira.
ALTER TABLE ml_devolucoes ADD COLUMN IF NOT EXISTS return_historico TEXT;
ALTER TABLE ml_devolucoes ADD COLUMN IF NOT EXISTS return_atrasos TEXT;
ALTER TABLE ml_devolucoes ADD COLUMN IF NOT EXISTS return_carrier_url TEXT;
"""


def garantir_colunas() -> None:
    from src.db.connection import get_db_connection

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(_DDL)
    conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coletar", action="store_true")
    ap.add_argument("--limite", type=int)
    args = ap.parse_args()

    if not args.coletar:
        ap.print_help()
        return 0

    garantir_colunas()
    c = coletar(limite=args.limite)
    print(f"  casos abertos     : {c['casos']}")
    print(f"  com devolução     : {c['com_retorno']}")
    print(f"  com previsão      : {c['com_previsao']}")
    print(f"  com histórico      : {c.get('com_historico', 0)}")
    print(f"  com atraso do ML   : {c.get('com_atraso', 0)}")
    print(f"  -> chegam na LOJA  : {c.get('loja', 0)}   (a Maria recebe)")
    print(f"  -> vao para o FULL : {c.get('full', 0)}   (o ML tria)")
    print(f"  destino desconhec.: {c.get('sem_destino', 0)}")
    if not c["com_previsao"]:
        print("\n  ⚠ nenhuma previsão coletada — sem isso não existe "
              "'chega hoje'. Investigar antes de montar o quadro.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
