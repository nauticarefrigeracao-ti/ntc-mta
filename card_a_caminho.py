"""O card "A caminho" -- a tela que a Maria usa todo dia.

Desenhado pela Thayna em 06/08/2026, e a exigencia dela e clara: **parecer com
o que a Maria ja usa no Mercado Livre**. Interface nova e treinamento novo; o
card do pos-venda ela ja le de cabeca.

Tres decisoes que este modulo toma:

**LOJA e FULL nao se misturam.** Medido: das 30 devolucoes com previsao, 19
vao para o galpao do ML em Cajamar e nunca passam pela Praia Grande.
Perguntar "ja chegou?" para essas ensina a Maria a ignorar a pergunta -- e ai
ela perde as 11 que importam.

**Atrasado vem primeiro.** Previsao de ontem que nao chegou e MAIS urgente que
a de hoje. Ordenar so por data faria o atrasado afundar no meio da lista.

**Valor e o da venda inteira.** `order_total` ignora quantidade: publicamos
R$ 659,00 onde o ML mostra R$ 1.318,00. Aqui o valor sai de `order_items`,
senao a Maria confere no Meli e nao bate.

Uso:
    python card_a_caminho.py --dry-run      # mostra sem publicar
    python card_a_caminho.py --publicar
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

sys.path.insert(0, str(Path(__file__).parent))

import em_transito
from slack_notify import _fmt_brl, motivo_humano

CANAL = "#sac"
FUSO = timezone(timezone.utc.utcoffset(None) or __import__("datetime").timedelta(0))

# Emoji com que a Maria confirma. Um so, e sempre o mesmo: escolha de emoji e
# decisao que ela nao deveria precisar tomar todo dia.
EMOJI_CHEGOU = "✅"

_ESTADOS = {
    "label_generated": "etiqueta gerada",
    "shipped": "a caminho",
    "delivered": "entregue",
    "pending": "aguardando o comprador postar",
}


def _dia(caso: Mapping[str, Any]) -> Optional[date]:
    return em_transito.dia_da_previsao(caso.get("return_estimated_delivery"))


def rotulo_de_prazo(caso: Mapping[str, Any], hoje: date) -> str:
    """atrasado · hoje · futuro · sem_data.

    Sem data NAO e "hoje": e desconhecido, e tem que aparecer assim -- senao a
    Maria procura um pacote que ninguem sabe quando vem.
    """
    d = _dia(caso)
    if d is None:
        return "sem_data"
    if d < hoje:
        return "atrasado"
    if d == hoje:
        return "hoje"
    return "futuro"


def separar_por_destino(casos: Iterable[Mapping[str, Any]]) -> dict:
    """loja (a Maria recebe) · full (o ML tria) · desconhecido.

    Chutar "loja" faria o Slack cobrar confirmacao de pacote que talvez nunca
    chegue -- o pior dos dois erros.
    """
    grupos: dict[str, list] = {"loja": [], "full": [], "desconhecido": []}
    for c in casos:
        grupos[c.get("return_destino") or "desconhecido"].append(c)
    return grupos


def ordenar_para_a_maria(casos: Iterable[Mapping[str, Any]],
                         hoje: date) -> list:
    """Caixa que ja esta no balcao primeiro; depois atrasado; depois prazo.

    `delivered` sobe ao topo mesmo com previsao futura: o pacote esta AQUI,
    esperando ela abrir. Ordenar so por data poria uma caixa ja entregue
    depois de outra que chega semana que vem.

    Sem data vai para o fim -- nao some, mas nao ocupa o lugar de quem ja
    estourou o prazo.
    """
    def chave(c):
        entregue = 0 if str(c.get("return_status") or "") == "delivered" else 1
        d = _dia(c)
        if d is None:
            return (entregue, 1, date.max)
        return (entregue, 0, d)

    return sorted(casos, key=chave)


def _dias_de_atraso(caso: Mapping[str, Any], hoje: date) -> int:
    d = _dia(caso)
    return (hoje - d).days if d and d < hoje else 0


def linha_do_card(caso: Mapping[str, Any], hoje: date) -> str:
    """Uma devolucao, no formato do card do pos-venda do Meli."""
    oid = caso.get("order_id")
    titulo = (caso.get("item_title") or "Produto").strip()
    if len(titulo) > 58:
        titulo = titulo[:57].rstrip() + "…"

    un = int(caso.get("unidades") or 1)
    unidades = f"{un} unidade" if un == 1 else f"{un} unidades"

    prazo = rotulo_de_prazo(caso, hoje)
    d = _dia(caso)
    if str(caso.get("return_status") or "") == "delivered":
        # O ML diz "entregue" e isso e o gatilho da Maria, nao o fim do caso.
        # A primeira versao filtrava esses fora da lista ("ja chegou, nao esta
        # mais a caminho") e sumia justamente com o mais urgente do dia: a
        # caixa que esta no balcao esperando alguem abrir.
        quando = "📬 *já chegou* — confira e confirme"
    elif prazo == "atrasado":
        n = _dias_de_atraso(caso, hoje)
        quando = (f"⏰ *atrasado {n} dia{'s' if n != 1 else ''}* "
                  f"— previsto {d:%d/%m}")
    elif prazo == "hoje":
        quando = f"📦 *chega hoje* — {d:%d/%m}"
    elif prazo == "futuro":
        quando = f"previsto {d:%d/%m}"
    else:
        quando = "sem previsão do Mercado Livre"

    estado = _ESTADOS.get(str(caso.get("return_status") or ""),
                          caso.get("return_status") or "—")

    L = [
        f"*<https://www.mercadolivre.com.br/vendas/{oid}/detalhe|#{oid}>* · "
        f"{titulo}",
        f"SKU {caso.get('item_sku') or '—'} · {unidades} · "
        f"{_fmt_brl(caso.get('valor'))}",
        f"_{motivo_humano(caso.get('reason_label'))}_",
        f"{quando} · {estado}",
    ]
    rastreio = caso.get("return_tracking_number")
    if rastreio:
        transportadora = caso.get("return_transportadora") or ""
        L.append(f"rastreio `{rastreio}`"
                 + (f" · {transportadora}" if transportadora else ""))
    return "\n".join(L)


def montar_blocos(casos: list, hoje: date) -> list[dict]:
    """A mensagem do dia, separada por quem recebe o pacote."""
    def sec(txt):
        return {"type": "section", "text": {"type": "mrkdwn", "text": txt}}

    grupos = separar_por_destino(casos)
    na_loja = ordenar_para_a_maria(grupos["loja"], hoje)
    no_full = ordenar_para_a_maria(grupos["full"] + grupos["desconhecido"], hoje)

    blocos: list[dict] = [{
        "type": "header",
        "text": {"type": "plain_text",
                 "text": f"📥 Devoluções a caminho — {hoje:%d/%m}",
                 "emoji": True},
    }]

    if not casos:
        blocos.append(sec("_Nenhuma devolução a caminho hoje._"))
        return blocos

    urgentes = [c for c in na_loja if rotulo_de_prazo(c, hoje) in
                ("atrasado", "hoje")]

    blocos.append(sec(
        f"*Chegam aqui na loja — {len(na_loja)}*  "
        f"({len(urgentes)} para hoje ou atrasadas)\n"
        f"_Quando o pacote chegar, marque {EMOJI_CHEGOU} na mensagem dele._"))

    if na_loja:
        for c in na_loja:
            blocos.append(sec(linha_do_card(c, hoje)))
    else:
        blocos.append(sec("_Nenhuma prevista para a loja._"))

    if no_full:
        blocos.append({"type": "divider"})
        blocos.append(sec(
            f"*Vão para o Full — {len(no_full)}*\n"
            "_O Mercado Livre tria essas no galpão dele. "
            "Não passam pela loja; é só acompanhar._"))
        for c in no_full[:8]:
            blocos.append({"type": "context", "elements": [
                {"type": "mrkdwn", "text": linha_do_card(c, hoje)}]})
        if len(no_full) > 8:
            blocos.append({"type": "context", "elements": [
                {"type": "mrkdwn",
                 "text": f"_e mais {len(no_full) - 8} no Full_"}]})
    return blocos


# --- I/O -------------------------------------------------------------------

SQL = """
    SELECT d.order_id, d.claim_id, d.item_title, d.item_sku, d.reason_label,
           d.return_destino, d.return_estimated_delivery, d.return_status,
           d.return_tracking_number, d.return_transportadora,
           COALESCE(i.unidades, 1) AS unidades,
           COALESCE(i.valor, d.order_total) AS valor
    FROM ml_devolucoes d
    LEFT JOIN (
        SELECT order_id,
               SUM(unidades) AS unidades,
               SUM(unidades * preco_unitario) AS valor
        FROM order_items GROUP BY order_id
    ) i ON i.order_id = d.order_id::text
    WHERE d.claim_status = 'opened'
      AND d.return_status IS NOT NULL
"""


def buscar() -> list[dict]:
    from src.db.connection import get_db_connection

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(SQL)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--publicar", action="store_true")
    ap.add_argument("--canal", default=CANAL)
    args = ap.parse_args()

    hoje = datetime.now().date()
    casos = buscar()
    blocos = montar_blocos(casos, hoje)

    if not args.publicar:
        for b in blocos:
            t = (b.get("text") or {}).get("text") or ""
            if not t and b.get("elements"):
                t = b["elements"][0].get("text", "")
            print(t or f"[{b['type']}]")
            print()
        return 0

    import slack_client

    cid = slack_client.garantir_canal(args.canal)
    if not cid:
        print(f"não consegui abrir {args.canal}")
        return 1
    resumo = f"Devoluções a caminho — {hoje:%d/%m}: {len(casos)} caso(s)"
    if not slack_client.post_message_full(cid, resumo, blocks=blocos):
        print("falha ao publicar")
        return 1
    print(f"publicado em {args.canal}: {len(casos)} caso(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
