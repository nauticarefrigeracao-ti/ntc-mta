"""Notificador Slack do SAC -- reclamacoes, devolucoes e cancelamentos do ML no #sac.
================================================================================
Envia via Slack Bot Token (slack_client.post_message, chat.postMessage) --
nao mais Incoming Webhook. Motivo (2026-07-24): um Incoming Webhook nunca
devolve o `ts` da mensagem enviada, entao nao ha como responder "dentro"
dela depois. Com o Bot Token, cada VENDA (order_id) vira uma thread: a
primeira notificacao daquela venda cria a mensagem-raiz; qualquer
atualizacao de estado da MESMA venda (nova etapa, tracking, lembrete)
responde na MESMA thread, em vez de virar mensagem solta nova e
desconectada no canal -- ver slack_threads (tabela nova).

Funcoes puras (sem I/O) ficam no topo do modulo e sao cobertas por
tests/test_slack_notify.py -- categorizacao, tracking, prazo estimado e
explicacao financeira sao derivadas de COMPORTAMENTO REAL observado na base
(dados do Neon), nao de suposicoes sobre a documentacao da API do ML:

- ml_mandatory_due esta SEMPRE vazio nos dados reais -> prazo e sempre
  apresentado como uma ESTIMATIVA, nunca como dado oficial da API.
- order_total costuma vir zerado em processos recem-abertos -> nunca
  mostramos "R$ 0,00" como se fosse o valor real da venda.
- cancel_purchase/cancel_sale chegam sempre com claim_status='closed' -> sao
  tratados como informativos, sem alerta de prazo.
- return_type e sempre vazio -> usamos return_status para o tracking.

Uso:
    python slack_notify.py --test                    # mensagem de resumo (demo)
    python slack_notify.py --once                    # notifica processos novos/atualizados
    python slack_notify.py --once --canal "#sac-teste"  # mesma coisa, canal de teste
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import slack_client

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CANAL_PADRAO = "#sac"

_DDL = """
CREATE TABLE IF NOT EXISTS slack_notificados (
    claim_id BIGINT NOT NULL,
    status TEXT NOT NULL,
    avisado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (claim_id, status)
)
"""

_DDL_THREADS = """
CREATE TABLE IF NOT EXISTS slack_threads (
    order_id BIGINT PRIMARY KEY,
    channel TEXT NOT NULL,
    thread_ts TEXT NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

# ---------------------------------------------------------------------------
# Funcoes puras -- sem I/O, cobertas por testes (TDD)
# ---------------------------------------------------------------------------

_ETAPAS_MEDIACAO = {
    "claim": "Reclamação direta",
    "dispute": "Mediação do ML",
    "recontact": "Recontato",
}

_TRACKING_LABELS = {
    "shipped": "Em transporte",
    "delivered": "Entregue",
    "label_generated": "Etiqueta gerada",
    "expired": "Etiqueta expirada",
    "not_delivered": "Não entregue",
}

REMINDER_INTERVAL_HORAS = 4  # intervalo minimo entre lembretes de reclamacao/recontato ainda sem resposta
ESTAGIOS_COM_LEMBRETE = {"claim", "recontact"}


def categorizar(row: Mapping[str, Any]) -> str:
    """Classifica o processo em uma categoria clara para o SAC.

    claim_type='mediations' cobre reclamacao direta, mediacao e recontato,
    diferenciados por claim_stage. cancel_purchase/cancel_sale sao
    cancelamentos. 'returns' e devolucao. Um tipo nao mapeado NUNCA e
    silenciosamente escondido -- mostramos o valor bruto explicitamente.
    """
    tipo = row.get("claim_type")
    if tipo == "mediations":
        etapa = row.get("claim_stage")
        return _ETAPAS_MEDIACAO.get(str(etapa), f"Mediação do ML (etapa: {etapa})")
    if tipo == "cancel_purchase":
        return "Cancelamento (arrependimento do comprador)"
    if tipo == "cancel_sale":
        return "Cancelamento (venda)"
    if tipo == "returns":
        return "Devolução"
    return f"Processo do Mercado Livre (tipo: {tipo})"


def bloco_tracking(row: Mapping[str, Any]) -> Optional[str]:
    """Linha humanizada de tracking, ou None se o processo nao tem devolucao fisica."""
    if not row.get("return_id"):
        return None
    status = row.get("return_tracking_status") or row.get("return_status")
    label = _TRACKING_LABELS.get(str(status), str(status) if status else "status desconhecido")
    numero = row.get("return_tracking_number") or row.get("tracking_number")
    if numero:
        return f"📦 Rastreio: {label} (código {numero})"
    return f"📦 Rastreio: {label}"


def prazo_estimado(row: Mapping[str, Any], agora: Optional[datetime] = None) -> Optional[str]:
    """Texto de prazo -- SEMPRE deixando claro quando e uma estimativa.

    ml_mandatory_due esta vazio em 100% dos casos observados -- por isso
    nunca tratamos prazo como dado oficial da API, e sim como estimativa de
    comportamento (~2 dias corridos para responder uma reclamacao direta).
    """
    if row.get("claim_status") != "opened":
        return None
    agora = agora or datetime.now(timezone.utc)
    etapa = row.get("claim_stage")
    if etapa == "claim":
        criada = row.get("date_created")
        if not criada:
            return "⏰ *Prazo estimado*: ~2 dias corridos para responder (data de abertura não disponível)"
        if isinstance(criada, str):
            try:
                criada = datetime.fromisoformat(criada.replace("Z", "+00:00"))
            except ValueError:
                return "⏰ *Prazo estimado*: ~2 dias corridos para responder (data de abertura inválida)"
        if criada.tzinfo is None:
            criada = criada.replace(tzinfo=timezone.utc)
        limite = criada + timedelta(days=2)
        restante = limite - agora
        horas = int(restante.total_seconds() // 3600)
        if horas > 0:
            return f"⏰ *Prazo estimado*: restam ~{horas}h para responder"
        return "🚨 *Prazo estimado ESTOURADO* — responder o quanto antes"
    if etapa == "dispute":
        return "⚖️ Em mediação — o Mercado Livre está arbitrando, não há prazo fixo do vendedor"
    if etapa == "recontact":
        return "🔁 ML pediu mais informações — responder o quanto antes para não perder o prazo"
    return "⏰ Prazo estimado indisponível para esta etapa"


def bloco_financeiro(row: Mapping[str, Any], saldo: Optional[float]) -> str:
    """Explica o valor da venda e o desfecho financeiro do processo.

    - order_total costuma ser 0 (ainda nao sincronizado) em processos recem
      abertos -- nunca mostramos R$ 0,00 como se fosse o valor real.
    - Processo ainda ABERTO nunca tem desfecho financeiro afirmado.
    - Processo FECHADO usa o saldo real (meli_page_saldos.total): positivo =
      ML indenizou/creditou acima do custo; zero = Protecao ao Vendedor
      cobriu (empatou); negativo = prejuizo confirmado.
    """
    total = row.get("order_total")
    valor_venda = _fmt_brl(total) if total else "ainda não sincronizado"
    linha_venda = f"Valor da venda: {valor_venda}"

    if row.get("claim_status") != "closed":
        return f"{linha_venda}\nResultado financeiro: em andamento — ainda sem desfecho definido"

    if saldo is None:
        return f"{linha_venda}\nResultado financeiro: conciliação financeira pendente"
    if saldo > 0:
        return (f"{linha_venda}\nResultado financeiro: +{_fmt_brl(saldo)} "
                "— o Mercado Livre indenizou/creditou acima do custo da venda")
    if saldo == 0:
        return (f"{linha_venda}\nResultado financeiro: R$ 0,00 "
                "— a Proteção ao Vendedor cobriu o custo, sem prejuízo nem ganho")
    return (f"{linha_venda}\nResultado financeiro: {_fmt_brl(saldo)} "
            "— prejuízo confirmado, o custo superou a cobertura")


def _dados_essenciais_completos(row: Mapping[str, Any]) -> str:
    """Flag curta (ex.: "11") indicando se SKU e valor da venda ja chegaram.

    Processos recem-abertos costumam aparecer no Slack com "SKU —" e "valor
    ainda nao sincronizado" porque o registro em ml_devolucoes ainda nao foi
    enriquecido. Sem isso, a primeira mensagem ficava CONGELADA para sempre
    com esses dados incompletos, mesmo depois que o sync preenchia tudo --
    porque claim_status/claim_stage/tracking nao mudavam. Incluir esta flag
    na chave de estado faz uma nova notificacao (atualizacao) disparar assim
    que SKU e valor completarem, sem esperar uma mudanca real de etapa.
    """
    sku_ok = "1" if row.get("item_sku") else "0"
    total_ok = "1" if row.get("order_total") else "0"
    return f"{sku_ok}{total_ok}"


def chave_estado(row: Mapping[str, Any]) -> str:
    """Chave composta reaproveitando slack_notificados (claim_id, status) sem migracao.

    Usamos "status:stage:tracking:dados_completos" como valor de 'status' --
    uma mudanca de etapa (ex.: claim -> dispute), de tracking, OU o
    preenchimento tardio de SKU/valor da venda ja gera uma chave nova e
    dispara nova notificacao.
    """
    tracking = row.get("return_tracking_status") or row.get("return_status") or ""
    completos = _dados_essenciais_completos(row)
    return f"{row.get('claim_status')}:{row.get('claim_stage')}:{tracking}:{completos}"


def deve_notificar(chaves_anteriores: set[str], chave_atual: str) -> bool:
    """True se esta chave de estado ainda nao foi notificada."""
    return chave_atual not in chaves_anteriores


def eh_atualizacao(chaves_anteriores: set[str]) -> bool:
    """True se ja existe pelo menos uma notificacao anterior para este claim
    (ou seja, esta nova mensagem e uma ATUALIZAÇÃO DE ESTADO, nao a primeira)."""
    return len(chaves_anteriores) > 0


def deve_anunciar(claim_status: str, ja_notificado_antes: bool) -> bool:
    """R4 (pedido do chefe): NAO anunciar no canal operacional um caso que ja
    nasce FECHADO e nunca foi notificado antes. Isso e um caso do PASSADO (ja
    resolvido no ML) — cantar como "novo processo"/pendencia confundiria a
    Maria e levaria a um id antigo. Esses vao pro fechamento diario, nao pro
    #sac operacional.

    - opened                  -> sempre anuncia (fila viva da Maria).
    - closed + com historico  -> anuncia (encerramento na thread que ela ja via).
    - closed + sem historico  -> NAO anuncia (caso do passado; vai pro resumo)."""
    if claim_status == "closed" and not ja_notificado_antes:
        return False
    return True


def precisa_lembrete(row: Mapping[str, Any], ultimo_aviso: Optional[datetime],
                      agora: Optional[datetime] = None) -> bool:
    """True se o processo ainda exige resposta do vendedor (reclamacao direta
    ou recontato, em aberto) e ja passou tempo suficiente desde o ultimo aviso
    no Slack -- para insistir ate a resposta ser dada, evitando penalizacao
    de reputacao por demora no atendimento."""
    if row.get("claim_status") != "opened":
        return False
    if row.get("claim_stage") not in ESTAGIOS_COM_LEMBRETE:
        return False
    if ultimo_aviso is None:
        return False
    agora = agora or datetime.now(timezone.utc)
    if ultimo_aviso.tzinfo is None:
        ultimo_aviso = ultimo_aviso.replace(tzinfo=timezone.utc)
    return (agora - ultimo_aviso) >= timedelta(hours=REMINDER_INTERVAL_HORAS)


def montar_mensagem_lembrete(row: Mapping[str, Any], agora: Optional[datetime] = None) -> tuple[str, list[dict]]:
    """Mensagem de insistencia para reclamacao/recontato ainda sem resposta --
    repete periodicamente ate o estado mudar (resposta dada pelo vendedor)."""
    categoria = categorizar(row)
    titulo = row.get("item_title") or "Produto"
    sku = row.get("item_sku") or "—"
    motivo = row.get("reason_label") or "nao informado"
    oid = row.get("order_id")
    linhas = [
        f"*Ainda sem resposta* — {categoria}",
        f"*{titulo}* (SKU {sku})",
        f"Motivo: _{motivo}_",
        "Isso ainda esta aguardando resposta do vendedor -- demora pode penalizar a reputacao no Mercado Livre.",
    ]
    prazo = prazo_estimado(row, agora)
    if prazo:
        linhas.append(prazo)
    if oid:
        linhas.append(f"<https://www.mercadolivre.com.br/vendas/{oid}/detalhe|Pedido {oid} - abrir a venda>")
    
    texto_fallback = "\n".join(linhas)
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{linhas[0]}*\n*{titulo}* (SKU {sku})\nMotivo: _{motivo}_"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "Isso ainda esta aguardando resposta do vendedor -- demora pode penalizar a reputacao no Mercado Livre."}]}
    ]
    if prazo:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": prazo}})
    if oid:
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "Abrir Venda", "emoji": True},
                "url": _link_venda(oid),
                "action_id": f"btn_open_{oid}"
            }]
        })
    return texto_fallback, blocks


def montar_mensagem(row: Mapping[str, Any], saldo: Optional[float] = None,
                     atualizacao: bool = False, agora: Optional[datetime] = None) -> tuple[str, list[dict]]:
    """Monta o texto final da notificacao do Slack."""
    cabecalho = "🔄 *Atualização de estado*" if atualizacao else "🚨 *Novo processo*"
    categoria = categorizar(row)
    titulo = row.get("item_title") or "Produto"
    sku = row.get("item_sku") or "—"
    motivo = row.get("reason_label") or "não informado"
    oid = row.get("order_id")

    linhas = [
        f"{cabecalho} — {categoria}",
        f"*{titulo}* (SKU {sku})",
        f"Motivo: _{motivo}_",
        bloco_financeiro(row, saldo),
    ]
    tracking = bloco_tracking(row)
    if tracking:
        linhas.append(tracking)
    prazo = prazo_estimado(row, agora)
    if prazo:
        linhas.append(prazo)
    if oid:
        linhas.append(f"➡️ <https://www.mercadolivre.com.br/vendas/{oid}/detalhe|Pedido {oid} — abrir a venda>")
    
    texto_fallback = "\n".join(linhas)
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{'🔄 Atualização' if atualizacao else '🚨 Novo processo'} — {categoria}", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{titulo}*\nSKU: {sku} · Motivo: _{motivo}_"}},
        {"type": "divider"}
    ]
    
    finance_block = bloco_financeiro(row, saldo)
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": finance_block}})
    
    context_elements = []
    if tracking:
        context_elements.append({"type": "mrkdwn", "text": tracking})
    if prazo:
        context_elements.append({"type": "mrkdwn", "text": prazo})
    if context_elements:
        blocks.append({"type": "context", "elements": context_elements})

    if oid:
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "Abrir Venda", "emoji": True},
                "url": _link_venda(oid),
                "action_id": f"btn_open_{oid}"
            }]
        })

    return texto_fallback, blocks


def _fmt_brl(v) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"


# ---------------------------------------------------------------------------
# Quadro Kanban do SAC (R10) -- funcoes puras
# ---------------------------------------------------------------------------

# Familias de codigo de motivo do ML -> linguagem da Maria (R5, seed).
_MOTIVO_FAMILIA = {
    "PNR": "Produto não recebido",
    "PDD": "Problema com o produto",
    "CS": "Cancelamento",
}
_RE_CODIGO_MOTIVO = re.compile(r"^([A-Z]{2,4})\d+$")


def motivo_humano(reason_label) -> str:
    """Traduz o motivo pra linguagem da Maria (R5). Se ja e texto (ex.:
    "Produto não funciona"), devolve como esta; se e codigo cru (PNR3837,
    PDD9952, CS6499), devolve a familia legivel. Nunca mostra codigo cru."""
    s = (reason_label or "").strip()
    if not s:
        return "Motivo não informado"
    m = _RE_CODIGO_MOTIVO.match(s)
    if m:
        return _MOTIVO_FAMILIA.get(m.group(1), "Problema com o produto")
    return s


def classificar_kanban(row: Mapping[str, Any]) -> str:
    """Coluna do Kanban da Maria a partir do estado REAL do caso:
    - 'a_fazer'   : aberto em reclamacao/recontato -> bola com o VENDEDOR,
                    o prazo corre, ela precisa responder no ML.
    - 'aguardando': aberto em disputa/mediacao (ML arbitra) ou em transito
                    -> bola com o ML/correio, ela so acompanha.
    - 'feito'     : fechado, com desfecho.
    """
    if row.get("claim_status") == "closed":
        return "feito"
    if row.get("claim_stage") in ("claim", "recontact"):
        return "a_fazer"
    return "aguardando"


def _link_venda(oid) -> str:
    return f"https://www.mercadolivre.com.br/vendas/{oid}/detalhe"


def _linha_quadro(row: Mapping[str, Any]) -> str:
    """Uma linha do quadro: produto (SKU) · motivo humano · link CTA."""
    titulo = (row.get("item_title") or "Produto").strip() or "Produto"
    if len(titulo) > 48:
        titulo = titulo[:47].rstrip() + "…"
    sku = row.get("item_sku") or "—"
    motivo = motivo_humano(row.get("reason_label"))
    oid = row.get("order_id")
    return f"• *{titulo}* (SKU {sku}) · _{motivo}_ · <{_link_venda(oid)}|abrir a venda>"


def montar_quadro(rows, data_str: str) -> tuple[str, list[dict]]:
    """Monta o 'Quadro do SAC' — visao Kanban do dia. A FAZER e listado (o
    que a Maria precisa AGIR, com CTA); AGUARDANDO e FEITO viram contadores
    (ela nao precisa agir neles). rows = claims abertos + fechados recentes."""
    a_fazer, aguardando, feito = [], [], []
    for r in rows:
        col = classificar_kanban(r)
        (a_fazer if col == "a_fazer" else aguardando if col == "aguardando" else feito).append(r)

    texto_fallback = f"Quadro do SAC — {data_str}: {len(a_fazer)} a fazer, {len(aguardando)} aguardando, {len(feito)} feito."

    blocks = []
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"🗂️ Quadro do SAC — {data_str}",
            "emoji": True
        }
    })
    blocks.append({"type": "divider"})

    # A FAZER
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"🔴 *A FAZER — {len(a_fazer)}*\n_responda no ML, o prazo corre_"
        }
    })

    if not a_fazer:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "_Nada pendente da sua parte agora. 👏_"}]
        })
    else:
        for r in a_fazer:
            titulo = (r.get("item_title") or "Produto").strip() or "Produto"
            if len(titulo) > 48:
                titulo = titulo[:47].rstrip() + "…"
            sku = r.get("item_sku") or "—"
            motivo = motivo_humano(r.get("reason_label"))
            oid = r.get("order_id")

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{titulo}*\nSKU: {sku} · Motivo: _{motivo}_"
                },
                "accessory": {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Abrir Venda",
                        "emoji": True
                    },
                    "url": _link_venda(oid),
                    "action_id": f"btn_{oid}"
                }
            })

    blocks.append({"type": "divider"})

    # AGUARDANDO
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"🟡 *AGUARDANDO — {len(aguardando)}*\n_o Mercado Livre está arbitrando ou o produto está a caminho; só acompanhar_"
        }
    })
    blocks.append({"type": "divider"})

    # FEITO
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"🟢 *FEITO — {len(feito)}*\n_casos já encerrados; o balanço em R$ sai no #sac-fechamento_"
        }
    })

    return texto_fallback, blocks


# ---------------------------------------------------------------------------
# I/O -- Slack, Neon, CLI
# ---------------------------------------------------------------------------

_DDL_BOARD = """
CREATE TABLE IF NOT EXISTS slack_board (
    channel TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    channel_id TEXT,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

def _get_thread(cur, order_id) -> Optional[tuple[str, str]]:
    cur.execute("SELECT channel, thread_ts FROM slack_threads WHERE order_id = %s", (order_id,))
    row = cur.fetchone()
    return (row[0], row[1]) if row else None


def _save_thread(cur, order_id, channel: str, thread_ts: str) -> None:
    cur.execute(
        "INSERT INTO slack_threads (order_id, channel, thread_ts) VALUES (%s,%s,%s) "
        "ON CONFLICT (order_id) DO UPDATE SET channel=EXCLUDED.channel, thread_ts=EXCLUDED.thread_ts",
        (order_id, channel, thread_ts),
    )


def enviar_na_venda(cur, canal: str, order_id, texto: str, blocks: Optional[list] = None) -> bool:
    """Posta agrupado por venda: raiz se a venda ainda nao tem thread,
    resposta na MESMA thread se ja tem -- e assim uma venda vira um card
    so que evolui, em vez de mensagens soltas desconectadas."""
    existente = _get_thread(cur, order_id)
    thread_ts = existente[1] if existente else None
    ts = slack_client.post_message(canal, texto, thread_ts=thread_ts, blocks=blocks)
    if not ts:
        return False
    if not existente:
        _save_thread(cur, order_id, canal, ts)
    return True


def enviar(canal: str, texto: str) -> bool:
    """Mensagem avulsa (resumo diario, teste) -- nao esta amarrada a uma
    venda especifica, entao nunca precisa de thread."""
    return bool(slack_client.post_message(canal, texto))


def _saldo_do_pedido(cur, order_id) -> Optional[float]:
    cur.execute("""
        SELECT m.total, v.mp_valor 
        FROM meli_page_saldos m
        LEFT JOIN mp_validation_results v ON v.order_id::text = m.order_id::text
        WHERE m.order_id = %s
    """, (order_id,))
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
        
    ml_total = float(row[0])
    mp_valor = row[1]
    
    # R8: Saldo Zero != Zerado. Se o ML diz 0, exigimos que o MP também tenha cruzado.
    if ml_total == 0.0 and mp_valor is None:
        return None
        
    return ml_total


def _chaves_anteriores(cur, claim_id) -> set[str]:
    cur.execute("SELECT status FROM slack_notificados WHERE claim_id = %s", (claim_id,))
    return {r[0] for r in cur.fetchall()}


def _ultimo_aviso(cur, claim_id) -> Optional[datetime]:
    cur.execute("SELECT MAX(avisado_em) FROM slack_notificados WHERE claim_id = %s", (claim_id,))
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else None


def notificar_processos(canal: str = CANAL_PADRAO) -> int:
    """Processos novos, com mudanca de estado, ou ainda sem resposta (lembrete) -> #sac."""
    from src.db.connection import get_db_connection, dict_cursor
    conn = get_db_connection()
    enviadas = 0
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
            cur.execute(_DDL_THREADS)
            conn.commit()
        with dict_cursor(conn) as cur:
            cur.execute("""
                SELECT claim_id, order_id, claim_type, claim_status, claim_stage,
                       reason_label, item_title, item_sku, order_total,
                       date_created, return_id, return_status, return_tracking_status,
                       return_tracking_number, tracking_number
                FROM ml_devolucoes
                WHERE claim_status IN ('opened', 'closed')
                ORDER BY date_updated DESC NULLS LAST
                LIMIT 50
            """)
            rows = cur.fetchall()
        agora = datetime.now(timezone.utc)
        with conn.cursor() as cur:
            for row in rows:
                anteriores = _chaves_anteriores(cur, row["claim_id"])
                chave = chave_estado(row)
                if deve_notificar(anteriores, chave):
                    atualizacao = eh_atualizacao(anteriores)
                    # R4: pula caso que ja nasce fechado sem historico (passado).
                    if not deve_anunciar(row["claim_status"], atualizacao):
                        continue
                    saldo = _saldo_do_pedido(cur, row["order_id"]) if row["claim_status"] == "closed" else None
                    texto, blocks = montar_mensagem(row, saldo, atualizacao, agora)
                    if enviar_na_venda(cur, canal, row["order_id"], texto, blocks=blocks):
                        cur.execute(
                            "INSERT INTO slack_notificados (claim_id, status) VALUES (%s,%s) "
                            "ON CONFLICT DO NOTHING", (row["claim_id"], chave))
                        conn.commit()
                        enviadas += 1
                    continue
                ultimo_aviso = _ultimo_aviso(cur, row["claim_id"])
                if precisa_lembrete(row, ultimo_aviso, agora):
                    texto, blocks = montar_mensagem_lembrete(row, agora)
                    if enviar_na_venda(cur, canal, row["order_id"], texto, blocks=blocks):
                        marcador = f"lembrete:{agora.isoformat()}"
                        cur.execute(
                            "INSERT INTO slack_notificados (claim_id, status) VALUES (%s,%s) "
                            "ON CONFLICT DO NOTHING", (row["claim_id"], marcador))
                        conn.commit()
                        enviadas += 1
    finally:
        conn.close()
    # Atualiza o Quadro Kanban depois de processar as notificacoes.
    try:
        publicar_quadro(canal)
    except Exception:
        pass
    return enviadas


def _get_board_ts(cur, canal: str) -> Optional[tuple[str, str]]:
    cur.execute("SELECT ts, channel_id FROM slack_board WHERE channel = %s", (canal,))
    row = cur.fetchone()
    return (row[0], row[1]) if row else None


def _save_board_ts(cur, canal: str, ts: str, channel_id: str) -> None:
    cur.execute(
        "INSERT INTO slack_board (channel, ts, channel_id, atualizado_em) "
        "VALUES (%s, %s, %s, CURRENT_TIMESTAMP) "
        "ON CONFLICT (channel) DO UPDATE SET ts=EXCLUDED.ts, channel_id=EXCLUDED.channel_id, atualizado_em=CURRENT_TIMESTAMP",
        (canal, ts, channel_id),
    )


def publicar_quadro(canal: str = CANAL_PADRAO) -> bool:
    """Publica/atualiza o Quadro Kanban do SAC no canal. Se ja existe um quadro,
    ATUALIZA in-place (chat.update) em vez de postar outro — o canal nao vira um
    monte de quadros. Le abertos + fechados nas ultimas 24h."""
    from src.db.connection import get_db_connection, dict_cursor
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL_BOARD)
            try:
                cur.execute("ALTER TABLE slack_board ADD COLUMN channel_id TEXT")
            except Exception:
                pass # ignora se a coluna ja existir
            conn.commit()
        with dict_cursor(conn) as cur:
            cur.execute("""
                SELECT claim_id, order_id, claim_status, claim_stage,
                       reason_label, item_title, item_sku
                FROM ml_devolucoes
                WHERE claim_status = 'opened'
                   OR (claim_status = 'closed'
                       AND date_updated ~ '^[0-9]{4}-'
                       AND date_updated::timestamptz > NOW() - interval '24 hours')
                ORDER BY date_updated DESC NULLS LAST
                LIMIT 200
            """)
            rows = cur.fetchall()
        data_str = datetime.now(timezone.utc).strftime("%d/%m")
        texto, blocks = montar_quadro(rows, data_str)
        with conn.cursor() as cur:
            board_data = _get_board_ts(cur, canal)
        
        ts_atual = board_data[0] if board_data else None
        channel_id_atual = board_data[1] if board_data else None
        
        novo_ts = None
        novo_channel_id = None
        
        if ts_atual and channel_id_atual:
            novo_ts = slack_client.update_message(channel_id_atual, ts_atual, texto, blocks=blocks)
            if novo_ts:
                novo_channel_id = channel_id_atual
                
        if not novo_ts:  # sem quadro ainda, ou msg antiga sumiu -> posta novo
            resp = slack_client.post_message_full(canal, texto, blocks=blocks)
            if resp:
                novo_ts = resp.get("ts")
                novo_channel_id = resp.get("channel")
                
        if novo_ts and novo_channel_id:
            with conn.cursor() as cur:
                _save_board_ts(cur, canal, novo_ts, novo_channel_id)
            conn.commit()
            return True
        return False
    finally:
        conn.close()


def resumo_diario(canal: str = CANAL_PADRAO) -> int:
    """Resumo diario dos processos fechados ONTEM, com prejuizo confirmado.

    Roda 1x por dia (cedo da manha) via workflow separado, fechando a
    contabilidade do dia anterior antes do ciclo normal de --once comecar
    a acompanhar o dia atual.
    """
    from src.db.connection import get_db_connection, dict_cursor
    agora = datetime.now(timezone.utc)
    hoje_0h = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    ontem_0h = hoje_0h - timedelta(days=1)
    data_str = ontem_0h.strftime("%d/%m/%Y")

    conn = get_db_connection()
    try:
        with dict_cursor(conn) as cur:
            cur.execute("SELECT sn.claim_id, d.order_id, d.item_title, d.item_sku, s.total AS saldo FROM slack_notificados sn JOIN ml_devolucoes d ON d.claim_id = sn.claim_id LEFT JOIN meli_page_saldos s ON s.order_id = d.order_id WHERE sn.status LIKE 'closed:%%' AND sn.avisado_em >= %s AND sn.avisado_em < %s", (ontem_0h, hoje_0h))
            rows = cur.fetchall()
    finally:
        conn.close()

    prejuizos = [(r, float(r["saldo"])) for r in rows if r.get("saldo") is not None and float(r["saldo"]) < 0]
    total = sum(v for _, v in prejuizos)

    if not rows:
        texto = f":white_check_mark: *Resumo do dia {data_str}*\nNenhum processo fechado ontem — dia zerado."
    elif not prejuizos:
        texto = f":white_check_mark: *Resumo do dia {data_str}*\n{len(rows)} processo(s) fechado(s), sem prejuízo — Mercado Livre cobriu ou o resultado ficou positivo."
    else:
        linhas = [f":rotating_light: *Resumo do dia {data_str}* — prejuízo confirmado: {_fmt_brl(total)} em {len(prejuizos)} venda(s) (de {len(rows)} processo(s) fechado(s))"]
        for r, v in prejuizos[:15]:
            linhas.append(f"• Pedido {r['order_id']} — {r.get('item_title') or 'Produto'} — {_fmt_brl(v)}")
        texto = "\n".join(linhas)

    return 1 if enviar(canal, texto) else 0


def teste(canal: str = CANAL_PADRAO) -> None:
    from src.db.connection import get_db_connection
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COALESCE(SUM(order_total),0) FROM ml_devolucoes WHERE claim_status='opened'")
        n, v = cur.fetchone()
    conn.close()
    ok = enviar(
        canal,
        f":bar_chart: *Painel de Devoluções — Náutica Refrigeração*\n"
        f"Neste momento: *{n} disputas em andamento*, {_fmt_brl(v)} em jogo.\n"
        f"Toda reclamação, mediação, devolução e cancelamento do Mercado Livre chega aqui "
        f"no *#sac* com categoria, motivo, valor e prazo estimado.\n"
        f"<https://ntc-mta.streamlit.app|Abrir o painel completo>")
    print(f"✓ mensagem de teste enviada ao {canal}" if ok else "✗ não enviou — confira o Bot Token")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--resumo-diario", action="store_true", dest="resumo")
    ap.add_argument("--quadro", action="store_true", help="publica/atualiza o Quadro Kanban")
    ap.add_argument("--canal", default=CANAL_PADRAO,
                    help=f"canal do Slack (default {CANAL_PADRAO})")
    args = ap.parse_args()
    if not slack_client._token():
        print("slack: sem Bot Token (SLACK_BOT_TOKEN ou arquivo local) — nada a fazer")
        return
    if args.test:
        teste(args.canal)
    if args.quadro:
        ok = publicar_quadro(args.canal)
        print("✓ quadro atualizado" if ok else "quadro: falhou")
        return
    if args.resumo:
        n = resumo_diario(args.canal)
        print("✓ resumo diário enviado" if n else "resumo diário: nada a enviar")
        return
    if args.once or not args.test:
        n = notificar_processos(args.canal)
        print(f"✓ {n} processo(s) notificado(s) em {args.canal}")


if __name__ == "__main__":
    main()
