"""O card bate com o Meli? -- a conferencia que evita passar vergonha.

Regra da casa: nada e validado ate ser visto na interface com que a pessoa
interage. Aqui a interface e o card no Slack; a verdade e a tela do Mercado
Livre.

O medo concreto: a Thayna abre o #sac-teste, clica no numero do pedido, e o
que esta no Slack nao bate com o que abre no Meli. Ja aconteceu -- o card
imprimia o `order_id` e a tela mostrava o `pack_id`.

TRES PROBLEMAS DIFERENTES, QUE NAO PODEM SER MISTURADOS

  DADO      o card diz R$ 659,00 e o Meli diz R$ 1.318,00. E defeito, e nao
            tem desculpa.

  TEMPO     o card diz "etiqueta gerada" e o Meli ja diz "a caminho". Nao e
            defeito: e atraso. O que importa e o TAMANHO dele -- 2 minutos
            ninguem nota, 3 horas a Maria trabalha com informacao velha.

  AUSENCIA  o caso existe no Meli e nao ha card nenhum. Pior que divergir,
            porque nao aparece para ninguem.

Juntar os tres num numero so ("87% de paridade") esconde exatamente a
diferenca que decide o que fazer.

Uso:
    python paridade_card.py --canal "#sac-teste"
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

sys.path.insert(0, str(Path(__file__).parent))

# Diferenca abaixo disto e arredondamento de float, nao divergencia. Acusar
# centavo encheria o relatorio de ruido e esconderia o que importa.
TOLERANCIA_REAIS = 0.02

# Campos que, quando diferem, sao DEFEITO -- o card mostra coisa errada.
CAMPOS_DE_DADO = ("numero", "sku", "unidades", "valor")

# Campos que mudam sozinhos no Meli e chegam ao card no proximo ciclo. Diferir
# aqui e atraso, nao erro.
CAMPOS_DE_TEMPO = ("estado",)

_NOMES = {
    "numero": "número do pedido",
    "sku": "código da peça",
    "unidades": "quantidade",
    "valor": "valor da venda",
    "estado": "situação da entrega",
    "card": "o card",
}

_ESTADOS = {
    "label_generated": "etiqueta gerada",
    "ready_to_ship": "etiqueta gerada",
    "shipped": "a caminho",
    "delivered": "entregue",
    "pending": "aguardando o comprador postar",
}


def _brl(v: Any) -> str:
    if v is None:
        return "sem valor"
    return f"R$ {float(v):,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _iguais(campo: str, a: Any, b: Any) -> bool:
    if campo == "valor":
        if a is None or b is None:
            # Nao saber o valor NAO e o mesmo que os valores baterem.
            return a is None and b is None
        return abs(float(a) - float(b)) <= TOLERANCIA_REAIS
    return str(a or "").strip() == str(b or "").strip()


def comparar(do_card: Optional[Mapping[str, Any]],
             do_meli: Optional[Mapping[str, Any]]) -> list[dict]:
    """As divergencias entre o que esta na tela e o que o Meli diz."""
    if do_meli is None:
        return []
    if do_card is None:
        return [{"campo": "card", "no_card": None,
                 "no_meli": do_meli.get("numero")}]

    saida = []
    for campo in CAMPOS_DE_DADO + CAMPOS_DE_TEMPO:
        a, b = do_card.get(campo), do_meli.get(campo)
        if not _iguais(campo, a, b):
            saida.append({"campo": campo, "no_card": a, "no_meli": b})
    return saida


def classificar(divergencia: Mapping[str, Any]) -> str:
    """"dado" (defeito) · "tempo" (atraso) · "ausencia" (nao aparece)."""
    campo = divergencia.get("campo")
    if campo == "card":
        return "ausencia"
    return "tempo" if campo in CAMPOS_DE_TEMPO else "dado"


def atraso_de(mudou_em: Optional[datetime],
              agora: Optional[datetime] = None) -> Optional[float]:
    """Segundos desde que o Meli mudou. None quando nao da para saber.

    Relogio fora de sincronia produziria atraso negativo -- que no relatorio
    viraria "o card sabe do futuro". Vira zero.
    """
    if mudou_em is None:
        return None
    agora = agora or datetime.now(timezone.utc)
    if mudou_em.tzinfo is None:
        mudou_em = mudou_em.replace(tzinfo=timezone.utc)
    return max(0.0, (agora - mudou_em).total_seconds())


def fmt_atraso(segundos: Optional[float]) -> str:
    if segundos is None:
        return "tempo desconhecido"
    s = int(segundos)
    if s < 60:
        return f"{s} segundos"
    if s < 3600:
        return f"{s // 60} minutos"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}"
    return f"{s // 86400} dias"


def explicar(divergencia: Mapping[str, Any],
             atraso_s: Optional[float] = None) -> str:
    """A divergencia em portugues de gente. Quem le e a Thayna."""
    campo = divergencia.get("campo")
    nome = _NOMES.get(campo, campo)
    a, b = divergencia.get("no_card"), divergencia.get("no_meli")

    if campo == "card":
        return (f"O pedido {b} está no Mercado Livre mas **não aparece** "
                f"nenhum card no Slack. Ninguém vai tratar esse caso.")

    if campo in CAMPOS_DE_TEMPO:
        return (f"A {nome} no card está **atrasada**: o card mostra "
                f"\"{_ESTADOS.get(str(a), a)}\" e o Mercado Livre já mudou "
                f"para \"{_ESTADOS.get(str(b), b)}\""
                + (f", há {fmt_atraso(atraso_s)}" if atraso_s else "")
                + ". Não é erro — o card ainda não foi redesenhado.")

    if campo == "valor":
        return (f"O {nome} não bate: o card mostra {_brl(a)} e o Mercado "
                f"Livre mostra {_brl(b)}.")

    return (f"O {nome} não bate: o card mostra \"{a}\" e o Mercado Livre "
            f"mostra \"{b}\".")


# --- I/O -------------------------------------------------------------------

def _do_meli(claim_id: int, order_id: int, tok: str) -> Optional[dict]:
    """A verdade, buscada ao vivo -- nao a copia que temos no banco."""
    import em_transito

    API = "https://api.mercadolibre.com"
    pedido = em_transito._get(f"{API}/orders/{order_id}", tok) or {}
    if not pedido:
        return None

    itens = pedido.get("order_items") or []
    it = (itens[0] if itens else {}) or {}
    unidades = sum(int(x.get("quantity") or 0) for x in itens) or None
    valor = sum(float(x.get("unit_price") or 0) * int(x.get("quantity") or 0)
                for x in itens) or None

    ret = em_transito._get(
        f"{API}/post-purchase/v2/claims/{claim_id}/returns", tok) or {}

    return {
        "numero": em_transito.pack_do_pedido(pedido) or int(pedido.get("id")),
        "sku": (it.get("item") or {}).get("seller_sku"),
        "unidades": unidades,
        "valor": valor,
        "estado": ret.get("status"),
        "mudou_em": ret.get("last_updated"),
    }


def _do_banco(conn, claim_id: int) -> Optional[dict]:
    import card_maria

    cur = conn.cursor()
    cur.execute(card_maria.SQL_CASOS.replace(
        "AND d.return_destino = 'loja'", "AND d.claim_id = %s"), (claim_id,))
    linhas = card_maria._linhas(cur)
    if not linhas:
        return None
    c = linhas[0]
    return {
        # O numero que a Maria LE e o pack; o que a API do ML
        # ACEITA em /orders/ e o order_id. Confundir os dois fazia a
        # conferencia dizer "nao consegui ler o Mercado Livre" onde o card
        # estava certo -- defeito do verificador, nao do produto.
        "order_id": int(c["order_id"]),
        "numero": card_maria.numero_na_plataforma(c),
        "sku": c.get("item_sku"),
        "unidades": int(c.get("unidades") or 0) or None,
        "valor": float(c["valor"]) if c.get("valor") is not None else None,
        "estado": c.get("return_status"),
        "titulo": c.get("item_title"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canal", default="#sac-teste")
    args = ap.parse_args()

    from src.api.ml_client import _token
    from src.db.connection import get_db_connection

    import slack_client

    tok = _token()
    if not tok:
        print("sem token do ML — a conferência pararia calada", file=sys.stderr)
        return 1

    conn = get_db_connection()
    cid = slack_client.garantir_canal(args.canal)
    cur = conn.cursor()
    cur.execute("SELECT claim_id FROM sac_cards WHERE channel_id = %s "
                "ORDER BY claim_id", (cid,))
    claims = [r[0] for r in cur.fetchall()]
    if not claims:
        print(f"nenhum card em {args.canal}", file=sys.stderr)
        return 1

    agora = datetime.now(timezone.utc)
    print(f"Paridade card x Mercado Livre — {args.canal}")
    print(f"{len(claims)} card(s) publicados, conferidos ao vivo na API\n")

    import em_transito

    n_dado = n_tempo = n_ok = 0
    atrasos = []
    for claim in claims:
        # Oito casos x duas chamadas em rajada tomam 429 do ML no meio, e o
        # 429 vira "nao consegui ler" -- que se leria como divergencia onde
        # nao ha. Meio segundo entre casos custa 4s no total.
        time.sleep(0.5)
        do_card = _do_banco(conn, claim)
        # `numero` e o pack (o que a Maria LE na tela); `order_id` e o que a
        # API do ML ACEITA em /orders/. Passar o pack aqui devolvia 404, e o
        # 404 virava "nao consegui ler o Mercado Livre" -- que se leria como
        # falha de paridade onde o card estava certo.
        do_meli = _do_meli(claim, do_card["order_id"], tok) if do_card else None
        if do_meli is None:
            print(f"  claim {claim} (pedido {(do_card or {}).get('order_id')}): "
                  f"o Mercado Livre não devolveu o pedido — conferir na mão")
            continue

        atraso = atraso_de(em_transito.dia_da_previsao(do_meli.get("mudou_em"))
                           and _instante(do_meli.get("mudou_em")), agora)
        divs = comparar(do_card, do_meli)
        if not divs:
            n_ok += 1
            continue
        titulo = (do_card or {}).get("titulo") or ""
        print(f"  #{do_meli['numero']} · {titulo[:44]}")
        for d in divs:
            tipo = classificar(d)
            n_dado += tipo == "dado"
            n_tempo += tipo == "tempo"
            if tipo == "tempo" and atraso:
                atrasos.append(atraso)
            print(f"     [{tipo}] {explicar(d, atraso)}")
        print()

    print(f"  {n_ok} de {len(claims)} card(s) sem nenhuma divergência")
    print(f"  divergências de DADO  : {n_dado}   (defeito)")
    print(f"  divergências de TEMPO : {n_tempo}   (atraso)")
    if atrasos:
        print(f"  atraso medido         : {fmt_atraso(min(atrasos))} a "
              f"{fmt_atraso(max(atrasos))}")
    # Divergencia de DADO derruba o job; atraso nao.
    return 1 if n_dado else 0


def _instante(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    sys.exit(main())
