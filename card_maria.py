"""O card da Maria -- UM card por caso, com botao, no formato do pos-venda.

A primeira tentativa publicou uma parede de texto: 28 casos numa mensagem so,
sem botao, sem acao. Era mensagem, nao card. Aqui cada caso e uma mensagem
propria que se ATUALIZA no lugar a cada clique -- e o `ts` dela mora em
`sac_cards`, senao o canal viraria um deposito de cards repetidos.

Tres defeitos de dado que os prints do Meli expuseram, corrigidos aqui:

**O numero impresso e o `pack_id`, nao o `order_id`.** Medido:
`/orders/2000017686941586` devolve `pack_id: 2000014291726681`, e e esse que a
tela do vendedor mostra. O link funcionava; o numero nao batia.

**"Atrasado" so vale para pacote em transito.** Medido: shipment
`ready_to_ship / printed`, sem `tracking_method` -- o comprador nem postou. O
`estimated_delivery_time` ali e promessa da etiqueta, nao entrega em curso.

**Caso de dez/2024 nao e "a caminho".** O card dizia "atrasado 588 dias". Isso
e caso parado, ja tem lugar no Quadro; no card do dia so rouba atencao.

Uso:
    python card_maria.py --dry-run
    python card_maria.py --publicar
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

sys.path.insert(0, str(Path(__file__).parent))

import sac_fluxo
from em_transito import dia_da_previsao
from slack_notify import _fmt_brl, motivo_humano

CANAL = "#sac"

# Estados em que o pacote ainda NAO saiu das maos do comprador. Aqui "prazo
# vencido" nao e atraso de entrega: e o comprador que nao postou. Chamar de
# atraso manda a Maria procurar caixa que ninguem despachou.
NAO_POSTADO = ("label_generated", "ready_to_ship", "pending", "to_be_agreed")

# Acima disso o caso deixa de ser "a caminho" e vira parado. Nao e chute: a
# lista real tinha casos de dez/2024 aparecendo como "atrasado 588 dias".
LIMITE_PARADO_DIAS = 45

# Slack corta a mensagem em 50 blocos. Estourar derruba a publicacao inteira,
# entao a linha do tempo tem teto.
MAX_BLOCOS = 50
MAX_TIMELINE = 12


def numero_na_plataforma(caso: Mapping[str, Any]) -> int:
    """O numero que a Maria LE na tela do Meli.

    Venda com mais de um item vive num pack, e e o pack que o vendedor ve.
    Imprimir o `order_id` faz ela conferir e nao achar.
    """
    return int(caso.get("pack_id") or caso.get("order_id"))


def _dias_desde(texto: Optional[str], hoje: date) -> Optional[int]:
    d = dia_da_previsao(texto)
    return (hoje - d).days if d else None


def situacao_do_envio(caso: Mapping[str, Any], hoje: date) -> str:
    """Uma frase honesta sobre onde o pacote esta."""
    st = str(caso.get("return_status") or "")
    prev = caso.get("return_estimated_delivery")
    d = dia_da_previsao(prev)

    if st == "delivered":
        return "📬 *Já chegou* — confira e confirme"

    if st in NAO_POSTADO:
        # Nada atrasou do nosso lado: o comprador tem a etiqueta e nao postou.
        base = "🏷️ *Etiqueta gerada* — aguardando o comprador postar"
        if d:
            base += f" · prazo dele até {d:%d/%m}"
        return base

    if d is None:
        return "🚚 A caminho — *sem previsão* do Mercado Livre"
    if d < hoje:
        n = (hoje - d).days
        return (f"⏰ *Atrasado {n} dia{'s' if n != 1 else ''}* — "
                f"era para chegar {d:%d/%m}")
    if d == hoje:
        return f"📦 *Chega hoje* — {d:%d/%m}"
    return f"🚚 A caminho — previsto {d:%d/%m}"


def esta_parado(caso: Mapping[str, Any], hoje: date) -> bool:
    """Caso velho demais para ser "a caminho".

    A ancora e a previsao; sem ela, a abertura do caso. Um caso de dez/2024
    aparecendo como "atrasado 588 dias" nao informa nada -- so consome a
    atencao que as nove do dia precisam.
    """
    for campo in ("return_estimated_delivery", "date_created"):
        n = _dias_desde(caso.get(campo), hoje)
        if n is not None:
            return n > LIMITE_PARADO_DIAS
    return False


def separar_parados(casos: Iterable[Mapping[str, Any]],
                    hoje: date) -> tuple[list, list]:
    ativos, parados = [], []
    for c in casos:
        (parados if esta_parado(c, hoje) else ativos).append(c)
    return ativos, parados


def texto_de_alerta(quantos: int) -> Optional[str]:
    """Os parados saem da lista do dia, mas nao somem calados.

    Sumir com o caso e o mesmo erro que sumir com o `delivered`: o que nao
    aparece deixa de existir para quem opera.
    """
    if not quantos:
        return None
    s = "s" if quantos != 1 else ""
    return (f"⚠️ {quantos} caso{s} parado{s} há mais de {LIMITE_PARADO_DIAS} "
            f"dias fora desta lista — estão no Quadro, para revisar ou "
            f"encerrar.")


def _https(url: Optional[str]) -> Optional[str]:
    """O Slack recusa imagem em http -- e cai calado, sem foto e sem erro."""
    if not url:
        return None
    u = str(url).strip()
    return "https://" + u[len("http://"):] if u.startswith("http://") else u


def _sec(txt: str, acessorio: Optional[dict] = None) -> dict:
    b = {"type": "section", "text": {"type": "mrkdwn", "text": txt}}
    if acessorio:
        b["accessory"] = acessorio
    return b


def blocos_do_card(caso: Mapping[str, Any],
                   timeline: list,
                   hoje: date,
                   ensaio: bool = False) -> list[dict]:
    """O card de UM caso: identificacao, situacao, historico e os botoes.

    `ensaio` marca o card publicado fora do canal oficial -- treinamento ou
    conferencia. Sem o selo, alguem abre o #sac-teste, ve um caso "Recusado"
    e leva o numero para uma reuniao.
    """
    num = numero_na_plataforma(caso)
    claim = caso.get("claim_id")
    titulo = (caso.get("item_title") or "Produto").strip()
    if len(titulo) > 70:
        titulo = titulo[:69].rstrip() + "…"

    un = int(caso.get("unidades") or 1)
    unidades = f"{un} unidade" if un == 1 else f"{un} unidades"

    estado = sac_fluxo.estado_de(timeline)

    cabeca = (
        f"*<https://www.mercadolivre.com.br/vendas/{num}/detalhe|#{num}>* · "
        f"{titulo}\n"
        f"SKU `{caso.get('item_sku') or '—'}` · {unidades} · "
        f"*{_fmt_brl(caso.get('valor'))}*\n"
        f"_{motivo_humano(caso.get('reason_label'))}_"
    )
    foto = _https(caso.get("item_thumbnail"))
    acessorio = ({"type": "image", "image_url": foto, "alt_text": titulo[:60]}
                 if foto else None)

    blocos: list[dict] = []
    if ensaio:
        blocos.append({"type": "context", "elements": [{
            "type": "mrkdwn",
            "text": "🧪 *ENSAIO* — canal de treino. O que for clicado aqui "
                    "*não conta* no cofrinho nem no balanço."}]})
    blocos.append(_sec(cabeca, acessorio))

    linha = situacao_do_envio(caso, hoje)
    rastreio = caso.get("return_tracking_number")
    if rastreio:
        linha += f"\nrastreio `{rastreio}`"
    blocos.append(_sec(linha))

    blocos.append({"type": "context", "elements": [
        {"type": "mrkdwn",
         "text": f"*{sac_fluxo.rotulo_do_estado(estado)}*"}]})

    # Linha do tempo: data e hora em cada marcacao, pedido explicito da
    # Thayna. Cortada pelo fim -- o passo mais recente e o que importa.
    if timeline:
        recentes = list(timeline)[-MAX_TIMELINE:]
        omitidos = len(timeline) - len(recentes)
        linhas = [sac_fluxo.linha_da_timeline(e) for e in recentes]
        if omitidos:
            linhas.insert(0, f"_(+{omitidos} marcações anteriores)_")
        blocos.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": "\n".join(linhas)}]})

    acoes = sac_fluxo.acoes_de(estado)
    if acoes:
        blocos.append({
            "type": "actions",
            "block_id": f"sac_{claim}",
            "elements": [
                {k: v for k, v in {
                    "type": "button",
                    "text": {"type": "plain_text", "text": a["rotulo"],
                             "emoji": True},
                    "action_id": f"sac_{a['id']}",
                    "value": str(claim),
                    "style": a["estilo"],
                }.items() if v is not None}
                for a in acoes
            ],
        })
    return blocos[:MAX_BLOCOS]


# --- I/O -------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS sac_timeline (
    id          BIGSERIAL PRIMARY KEY,
    claim_id    BIGINT      NOT NULL,
    etapa       TEXT        NOT NULL,
    quem        TEXT,
    quando      TIMESTAMPTZ NOT NULL DEFAULT now(),
    observacao  TEXT
);
CREATE INDEX IF NOT EXISTS ix_sac_timeline_claim
    ON sac_timeline (claim_id, quando);

CREATE TABLE IF NOT EXISTS sac_cards (
    claim_id    BIGINT NOT NULL,
    channel_id  TEXT NOT NULL,
    ts          TEXT NOT NULL,
    atualizado  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Em que canal a marcacao nasceu. O card do #sac-teste aponta para o MESMO
-- claim real do #sac; sem esta coluna, um clique de treino vira dinheiro no
-- cofrinho do mes.
ALTER TABLE sac_timeline ADD COLUMN IF NOT EXISTS channel_id TEXT;

-- O mesmo caso pode ter um card no #sac e outro no #sac-teste, e sao
-- mensagens diferentes. Com a chave so no claim, o segundo publish
-- sobrescreveria o `ts` do primeiro e orfanaria a mensagem la.
ALTER TABLE sac_cards DROP CONSTRAINT IF EXISTS sac_cards_pkey;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'sac_cards_claim_canal') THEN
        ALTER TABLE sac_cards ADD CONSTRAINT sac_cards_claim_canal
              PRIMARY KEY (claim_id, channel_id);
    END IF;
END $$;

ALTER TABLE ml_devolucoes ADD COLUMN IF NOT EXISTS pack_id BIGINT;
"""

SQL_CASOS = """
    SELECT d.order_id, d.pack_id, d.claim_id, d.item_title, d.item_sku,
           d.item_thumbnail, d.reason_label, d.date_created,
           d.return_destino, d.return_estimated_delivery, d.return_status,
           d.return_tracking_number, d.return_transportadora,
           COALESCE(i.unidades, 1) AS unidades,
           COALESCE(i.valor, d.order_total) AS valor
    FROM ml_devolucoes d
    LEFT JOIN (
        SELECT order_id, SUM(unidades) AS unidades,
               SUM(unidades * preco_unitario) AS valor
        FROM order_items GROUP BY order_id
    ) i ON i.order_id = d.order_id::text
    WHERE d.claim_status = 'opened'
      AND d.return_status IS NOT NULL
      AND d.return_destino = 'loja'
"""


def garantir_tabelas() -> None:
    from src.db.connection import get_db_connection

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(_DDL)
    conn.commit()


def _linhas(cur) -> list[dict]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def buscar_casos(conn) -> list[dict]:
    cur = conn.cursor()
    cur.execute(SQL_CASOS)
    return _linhas(cur)


def buscar_timelines(conn, claims: list,
                     channel_id: Optional[str] = None) -> dict:
    """As marcacoes por claim. Com `channel_id`, so as nascidas nesse canal.

    O card do #sac-teste avanca com os cliques do #sac-teste; o do #sac, com
    os do #sac. Misturar faria o treino da Maria mexer na tela de producao.
    """
    if not claims:
        return {}
    cur = conn.cursor()
    cur.execute("""
        SELECT claim_id, etapa, quem, quando, observacao, channel_id
        FROM sac_timeline
        WHERE claim_id = ANY(%s)
          AND (%s::text IS NULL OR channel_id = %s)
        ORDER BY quando, id
    """, (list(claims), channel_id, channel_id))
    saida: dict = {}
    for r in _linhas(cur):
        saida.setdefault(r["claim_id"], []).append(r)
    return saida


def registrar(conn, claim_id: int, etapa: str, quem: Optional[str],
              channel_id: Optional[str] = None,
              observacao: Optional[str] = None) -> None:
    """Grava a marcacao.

    A regra do fluxo NAO e validada aqui -- quem valida e `sac_fluxo.aplicar`,
    antes de chamar, para a regra morar num lugar so.

    O `channel_id` e obrigatorio e falha alto quando falta: sem ele nao da
    para saber se a marcacao e dinheiro de verdade ou clique de treino, e
    assumir "oficial" seria escolher o pior dos dois erros, calado.
    """
    if not channel_id:
        raise ValueError(
            f"marcação '{etapa}' do claim {claim_id} chegou sem canal. "
            "Sem canal não dá para separar dinheiro de ensaio."
        )
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sac_timeline
                   (claim_id, etapa, quem, channel_id, observacao)
            VALUES (%s, %s, %s, %s, %s)
        """, (claim_id, etapa, quem, channel_id, observacao))
    conn.commit()


def publicar(canal: str = CANAL, dry_run: bool = False) -> int:
    from src.db.connection import get_db_connection

    import slack_client

    hoje = datetime.now().date()
    conn = get_db_connection()
    casos = buscar_casos(conn)
    ativos, parados = separar_parados(casos, hoje)

    if dry_run:
        timelines = buscar_timelines(conn, [c["claim_id"] for c in ativos])
        for c in ativos:
            print(f"--- #{numero_na_plataforma(c)} claim {c['claim_id']}")
            for b in blocos_do_card(c, timelines.get(c["claim_id"], []), hoje):
                if b["type"] == "actions":
                    print("  [botões] " + " | ".join(
                        e["text"]["text"] for e in b["elements"]))
                else:
                    t = (b.get("text") or {}).get("text") or ""
                    if not t and b.get("elements"):
                        t = b["elements"][0].get("text", "")
                    print("  " + t.replace("\n", "\n  "))
        alerta = texto_de_alerta(len(parados))
        if alerta:
            print(f"\n{alerta}")
        print(f"\n{len(ativos)} card(s) na loja · {len(parados)} parado(s)")
        return 0

    cid = slack_client.garantir_canal(canal)
    if not cid:
        print(f"não consegui abrir {canal}", file=sys.stderr)
        return 1

    # O card avanca com os cliques do PROPRIO canal, e se identifica como
    # ensaio quando nao esta no canal oficial.
    ensaio = canal != CANAL
    timelines = buscar_timelines(conn, [c["claim_id"] for c in ativos], cid)

    cur = conn.cursor()
    cur.execute("SELECT claim_id, ts FROM sac_cards WHERE channel_id = %s",
                (cid,))
    ja = dict(cur.fetchall())

    novos = atualizados = falhas = 0
    for c in ativos:
        claim = c["claim_id"]
        blocos = blocos_do_card(c, timelines.get(claim, []), hoje, ensaio)
        resumo = (f"Devolução #{numero_na_plataforma(c)} — "
                  f"{(c.get('item_title') or '')[:60]}")
        if claim in ja:
            if slack_client.update_message(cid, ja[claim], resumo,
                                           blocks=blocos):
                atualizados += 1
            else:
                falhas += 1
            continue
        r = slack_client.post_message_full(cid, resumo, blocks=blocos)
        if not r or not r.get("ts"):
            falhas += 1
            continue
        with conn.cursor() as c2:
            c2.execute("""
                INSERT INTO sac_cards (claim_id, channel_id, ts)
                VALUES (%s, %s, %s)
                ON CONFLICT (claim_id, channel_id) DO UPDATE
                  SET ts = EXCLUDED.ts, atualizado = now()
            """, (claim, cid, r["ts"]))
        conn.commit()
        novos += 1

    alerta = texto_de_alerta(len(parados))
    if alerta:
        slack_client.post_message(canal, alerta)

    print(f"{novos} card(s) novo(s) · {atualizados} atualizado(s) · "
          f"{falhas} falha(s) · {len(parados)} parado(s) fora da lista")
    # Falha calada foi o que escondeu o erro do sync por semanas.
    return 1 if falhas else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--publicar", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--canal", default=CANAL)
    args = ap.parse_args()

    if not (args.publicar or args.dry_run):
        ap.print_help()
        return 0
    if args.publicar:
        garantir_tabelas()
    return publicar(canal=args.canal, dry_run=not args.publicar)


if __name__ == "__main__":
    sys.exit(main())
