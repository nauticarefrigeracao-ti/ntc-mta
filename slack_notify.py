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


def status_saida(tentadas: int, enviadas: int) -> int:
    """Codigo de saida do processo: 0 = ok, 1 = falhou (FAIL-LOUD).

    O bot saiu de #sac uma vez (not_in_channel): chat.postMessage passou a
    devolver ok:false, o notificador contou 0 enviadas e saiu com 0 -- o
    GitHub Actions ficou VERDE por 4 dias enquanto a Maria nao recebia nada.
    Qualquer mensagem que devia sair e nao saiu derruba o run, para o alerta
    do Actions chegar em vez do silencio."""
    if tentadas == 0:
        return 0
    return 0 if enviadas >= tentadas else 1


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


def deve_notificar_no_canal(row: Mapping[str, Any]) -> bool:
    """Este caso merece uma MENSAGEM, ou basta estar no Canvas?

    D3 da reuniao de 31/07. Medicao que motivou: das 79 notificacoes de 7
    dias, 60 (76%) eram de casos em DISPUTA -- em que quem decide e o Mercado
    Livre e nao ha nada que a Maria possa fazer. Mensagem para cada uma treina
    ela a ignorar o canal e afoga os poucos que exigem resposta (3 de 40).

    Vira mensagem:
      - aberto em claim/recontact -> a bola esta com a gente, o prazo corre;
      - qualquer caso FECHADO     -> e o desfecho, e o que vira dinheiro.

    Fica so no Canvas:
      - aberto em disputa  -> o ML arbitra, nao ha acao possivel;
      - devolucao em transito -> rastreio mudando nao pede acao.
    """
    if row.get("claim_status") == "closed":
        return True
    return row.get("claim_stage") in ESTAGIOS_COM_LEMBRETE


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
    motivo = motivo_humano(row.get("reason_label"))
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
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _cta_venda(oid)}})
    return texto_fallback, blocks


def montar_mensagem(row: Mapping[str, Any], saldo: Optional[float] = None,
                     atualizacao: bool = False, agora: Optional[datetime] = None) -> tuple[str, list[dict]]:
    """Monta o texto final da notificacao do Slack."""
    cabecalho = "🔄 *Atualização de estado*" if atualizacao else "🚨 *Novo processo*"
    categoria = categorizar(row)
    titulo = row.get("item_title") or "Produto"
    sku = row.get("item_sku") or "—"
    # motivo_humano e nao o rotulo cru: em 31/07 a Maria recebeu
    # "Motivo: PDD9952" no #sac. A traducao existia, mas so o Quadro usava.
    motivo = motivo_humano(row.get("reason_label"))
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
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _cta_venda(oid)}})

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


def _cta_venda(oid) -> str:
    """CTA como link mrkdwn, nunca como botao.

    Botao do Block Kit -- mesmo url-only -- faz o Slack mandar um payload
    block_actions pro app; sem Interactivity URL configurada (exige servidor
    sempre-ligado, e isto roda em cron) o Slack estampa "app nao configurado
    para respostas interativas" ao lado do CTA. Link mrkdwn tem o mesmo
    destino sem interacao nenhuma."""
    return f"➡️ <{_link_venda(oid)}|Abrir a venda {oid} no Mercado Livre>"


def _linha_quadro(row: Mapping[str, Any]) -> str:
    """Uma linha do quadro: produto (SKU) · motivo humano · link CTA."""
    titulo = (row.get("item_title") or "Produto").strip() or "Produto"
    if len(titulo) > 48:
        titulo = titulo[:47].rstrip() + "…"
    sku = row.get("item_sku") or "—"
    motivo = motivo_humano(row.get("reason_label"))
    oid = row.get("order_id")
    return f"• *{titulo}* (SKU {sku}) · _{motivo}_ · <{_link_venda(oid)}|abrir a venda>"


def _ordem_por_idade(row: Mapping[str, Any]):
    """Mais antigo primeiro: quem espera ha mais tempo corre mais risco de
    estourar prazo. Caso sem data vai para o fim, nao quebra a ordenacao."""
    d = row.get("date_created")
    return (d is None, str(d or ""))


def resumo_uma_linha(rows) -> str:
    """Uma frase para o topo do canal: quantos pedem acao AGORA."""
    n = sum(1 for r in rows if classificar_kanban(r) == "a_fazer")
    if n == 0:
        return "Nada pendente da sua parte agora."
    if n == 1:
        return "1 caso esperando você responder no Mercado Livre."
    return f"{n} casos esperando você responder no Mercado Livre."


def montar_canvas_quadro(rows, data_str: str,
                         saldo_dia: Optional[float] = None) -> str:
    """Markdown do Canvas fixo do #sac -- o cockpit da Maria.

    A diferenca para um mural: A FAZER e listado caso a caso, com valor e
    link; AGUARDANDO e FEITO viram CONTADOR. Medido em 31/07, so 8% do que
    ela ve exige acao dela (3 de 40) -- listar os 37 restantes recriaria
    exatamente o afogamento que este quadro veio resolver.
    """
    a_fazer, aguardando, feito = [], [], []
    for r in rows:
        col = classificar_kanban(r)
        (a_fazer if col == "a_fazer" else
         aguardando if col == "aguardando" else feito).append(r)

    a_fazer.sort(key=_ordem_por_idade)

    L = [f"# 🗂️ Quadro do SAC — {data_str}", ""]

    # ── A FAZER: o unico bloco com detalhe ────────────────────────────────
    L.append(f"## 🔴 A Fazer — {len(a_fazer)}")
    if not a_fazer:
        L.append("_Nada pendente da sua parte agora._")
    else:
        L.append("_Responda no Mercado Livre. O prazo está correndo._")
        L.append("")
        for r in a_fazer:
            titulo = (r.get("item_title") or "Produto").strip() or "Produto"
            if len(titulo) > 60:
                titulo = titulo[:59].rstrip() + "…"
            sku = r.get("item_sku") or "—"
            motivo = motivo_humano(r.get("reason_label"))
            oid = r.get("order_id")
            total = r.get("order_total")
            valor = f" · {_fmt_brl(total)}" if total else ""
            L.append(f"- **{titulo}**")
            L.append(f"  SKU {sku} · _{motivo}_{valor}")
            if oid:
                L.append(f"  [Abrir a venda {oid}]({_link_venda(oid)})")
    L.append("")

    # ── AGUARDANDO: contador, nao lista ───────────────────────────────────
    L.append(f"## 🟡 Aguardando — {len(aguardando)}")
    L.append("_O Mercado Livre está arbitrando ou o produto está a caminho. "
             "Só acompanhar._")
    L.append("")

    # ── FEITO ─────────────────────────────────────────────────────────────
    L.append(f"## 🟢 Feito — {len(feito)}")
    if saldo_dia is not None:
        L.append(f"_Saldo do período: {_fmt_brl(saldo_dia)}_")
    else:
        L.append("_Casos encerrados. O balanço em R$ sai no #sac-fechamento._")
    L.append("")
    L.append("---")
    L.append(f"_Atualizado automaticamente · {data_str}_")

    return "\n".join(L)


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
                    "text": (f"*{titulo}*\nSKU: {sku} · Motivo: _{motivo}_\n"
                             f"{_cta_venda(oid)}")
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
# R7 -- fechamento diario (o placar do chefe) -- funcoes puras
# ---------------------------------------------------------------------------

CANAL_FECHAMENTO = "#sac-fechamento"


def classificar_desfecho(saldo) -> str:
    """Desfecho financeiro de um caso fechado, do jeito que o chefe le:
    negativo = prejuizo, zero = a Protecao ao Vendedor cobriu, revertido = o
    ML indenizou acima do custo. Sem saldo conciliado -> 'pendente': saldo
    zero nao e a mesma coisa que zerado (R8), entao nao afirmamos nada."""
    if saldo is None:
        return "pendente"
    v = float(saldo)
    if v < 0:
        return "negativo"
    if v > 0:
        return "revertido"
    return "zero"


def montar_fechamento(rows, data_str: str) -> tuple[str, list[dict]]:
    """Balanco do dia: quanto fechou negativo/zero/revertido e o saldo.

    rows = casos fechados no dia, cada um com 'saldo' (meli_page_saldos.total),
    order_id, item_title, item_sku."""
    # Um claim tem varias chaves de estado em slack_notificados ("closed:a",
    # "closed:b") e o JOIN devolve uma linha por chave -- sem isto o mesmo
    # caso era contado duas vezes e o prejuizo do chefe saia INFLADO.
    vistos, unicos = set(), []
    for r in rows:
        ident = r.get("claim_id") or r.get("order_id")
        if ident is not None and ident in vistos:
            continue
        if ident is not None:
            vistos.add(ident)
        unicos.append(r)
    rows = unicos

    grupos: dict[str, list] = {"negativo": [], "zero": [], "revertido": [], "pendente": []}
    for r in rows:
        grupos[classificar_desfecho(r.get("saldo"))].append(r)

    negativos = sorted(grupos["negativo"], key=lambda r: float(r["saldo"]))
    total_prejuizo = sum(float(r["saldo"]) for r in negativos)
    total_revertido = sum(float(r["saldo"]) for r in grupos["revertido"])
    saldo_dia = total_prejuizo + total_revertido

    blocks: list[dict] = [{
        "type": "header",
        "text": {"type": "plain_text", "text": f"📊 Fechamento do dia — {data_str}", "emoji": True},
    }]

    if not rows:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
                       "text": "Nenhum processo fechou hoje. Dia zerado."}})
        return f"Fechamento {data_str}: nenhum processo fechado.", blocks

    blocks.append({"type": "section", "fields": [
        {"type": "mrkdwn", "text": f"*Saldo do dia*\n{_fmt_brl(saldo_dia)}"},
        {"type": "mrkdwn", "text": f"*Casos fechados*\n{len(rows)}"},
    ]})
    blocks.append({"type": "divider"})
    blocks.append({"type": "section", "fields": [
        {"type": "mrkdwn",
         "text": f"🔴 *Prejuízo* — {len(negativos)}\n{_fmt_brl(total_prejuizo)}"},
        {"type": "mrkdwn",
         "text": f"⚪ *ML cobriu* — {len(grupos['zero'])}\nsem prejuízo nem ganho"},
        {"type": "mrkdwn",
         "text": f"🟢 *Revertido* — {len(grupos['revertido'])}\n+{_fmt_brl(total_revertido)}"},
    ]})

    if negativos:
        blocks.append({"type": "divider"})
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
                       "text": "*Onde o dinheiro saiu* (maior primeiro)"}})
        for r in negativos[:10]:
            titulo = (r.get("item_title") or "Produto").strip() or "Produto"
            if len(titulo) > 44:
                titulo = titulo[:43].rstrip() + "…"
            blocks.append({"type": "section", "text": {"type": "mrkdwn",
                           "text": (f"*{_fmt_brl(r['saldo'])}* · {titulo} (SKU {r.get('item_sku') or '—'})\n"
                                    f"{_cta_venda(r.get('order_id'))}")}})
        if len(negativos) > 10:
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
                           "text": f"_e mais {len(negativos) - 10} caso(s) — veja tudo no painel_"}]})

    if grupos["pendente"]:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
                       "text": (f"_{len(grupos['pendente'])} caso(s) com conciliação financeira pendente — "
                                "ainda não entram no saldo._")}]})

    texto = (f"Fechamento {data_str}: saldo {_fmt_brl(saldo_dia)} — "
             f"{len(negativos)} com prejuízo ({_fmt_brl(total_prejuizo)}), "
             f"{len(grupos['zero'])} cobertos, {len(grupos['revertido'])} revertidos.")
    return texto, blocks


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


def enviar(canal: str, texto: str, blocks: Optional[list] = None) -> bool:
    """Mensagem avulsa (fechamento diario, teste) -- nao esta amarrada a uma
    venda especifica, entao nunca precisa de thread."""
    return bool(slack_client.post_message(canal, texto, blocks=blocks))


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


def notificar_processos(canal: str = CANAL_PADRAO) -> tuple[int, int]:
    """Processos novos, com mudanca de estado, ou ainda sem resposta (lembrete) -> #sac.

    Devolve (tentadas, enviadas). A diferenca entre os dois e o que faz o run
    falhar alto (ver status_saida) em vez de passar como verde silencioso."""
    from src.db.connection import get_db_connection, dict_cursor
    conn = get_db_connection()
    tentadas = 0
    enviadas = 0
    silenciadas = 0  # D3: casos que ficam so no Canvas (disputa/transito)
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
                    # D3: disputa aberta nao vira mensagem -- quem decide e o
                    # ML, nao ha acao possivel. Ela continua visivel no Canvas.
                    # Sem isto, 76% das mensagens nao pediam nada e afogavam as
                    # poucas que pediam. O estado e registrado do mesmo jeito,
                    # para nao reaparecer como "novo" quando virar acionavel.
                    if not deve_notificar_no_canal(row):
                        cur.execute(
                            "INSERT INTO slack_notificados (claim_id, status) "
                            "VALUES (%s,%s) ON CONFLICT DO NOTHING",
                            (row["claim_id"], chave))
                        conn.commit()
                        silenciadas += 1
                        continue
                    saldo = _saldo_do_pedido(cur, row["order_id"]) if row["claim_status"] == "closed" else None
                    texto, blocks = montar_mensagem(row, saldo, atualizacao, agora)
                    tentadas += 1
                    if enviar_na_venda(cur, canal, row["order_id"], texto, blocks=blocks):
                        cur.execute(
                            "INSERT INTO slack_notificados (claim_id, status) VALUES (%s,%s) "
                            "ON CONFLICT DO NOTHING", (row["claim_id"], chave))
                        conn.commit()
                        enviadas += 1
                    else:
                        print(f"slack: FALHOU ao notificar claim {row['claim_id']} "
                              f"(pedido {row['order_id']}) em {canal}", file=sys.stderr)
                    continue
                ultimo_aviso = _ultimo_aviso(cur, row["claim_id"])
                if precisa_lembrete(row, ultimo_aviso, agora):
                    texto, blocks = montar_mensagem_lembrete(row, agora)
                    tentadas += 1
                    if enviar_na_venda(cur, canal, row["order_id"], texto, blocks=blocks):
                        marcador = f"lembrete:{agora.isoformat()}"
                        cur.execute(
                            "INSERT INTO slack_notificados (claim_id, status) VALUES (%s,%s) "
                            "ON CONFLICT DO NOTHING", (row["claim_id"], marcador))
                        conn.commit()
                        enviadas += 1
                    else:
                        print(f"slack: FALHOU ao lembrar claim {row['claim_id']} "
                              f"(pedido {row['order_id']}) em {canal}", file=sys.stderr)
    finally:
        conn.close()
    # Atualiza o Quadro Kanban depois de processar as notificacoes. O quadro e
    # a visao principal da Maria: se ele nao atualizar, o ciclo falhou -- por
    # isso conta como tentativa e derruba o run (nao mais try/except: pass).
    tentadas += 1
    try:
        if publicar_quadro(canal):
            enviadas += 1
        else:
            print(f"slack: FALHOU ao atualizar o Quadro em {canal}", file=sys.stderr)
    except Exception as exc:
        print(f"slack: erro ao atualizar o Quadro em {canal}: {type(exc).__name__}: {exc}",
              file=sys.stderr)

    # O CANVAS entra no mesmo ciclo -- e isto que faz o Kanban andar sozinho.
    # Ele e reconstruido do estado atual do banco (por isso `replace`, nao
    # remendo): quando a Maria responde no ML e o sync traz o novo estado, o
    # card sai de "A Fazer" na proxima execucao, sem ninguem mexer.
    # A latencia real e a soma de duas engrenagens: sync do ML (2h) + este
    # ciclo (5min). Quem manda no atraso e o sync, nao o Canvas.
    tentadas += 1
    try:
        if publicar_canvas(canal):
            enviadas += 1
        else:
            print(f"slack: FALHOU ao atualizar o Canvas em {canal}", file=sys.stderr)
    except Exception as exc:
        print(f"slack: erro ao atualizar o Canvas em {canal}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
    if silenciadas:
        print(f"  {silenciadas} caso(s) sem acao possivel ficaram so no Canvas (D3)")
    return tentadas, enviadas


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


_DDL_CANVAS = """
CREATE TABLE IF NOT EXISTS slack_canvas (
    channel_id TEXT PRIMARY KEY,
    canvas_id  TEXT NOT NULL,
    criado_em  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _resolver_channel_id(canal: str) -> Optional[str]:
    """Aceita '#sac' ou um id ja resolvido. A API de Canvas exige o ID."""
    if not canal:
        return None
    if not canal.startswith("#"):
        return canal
    nome = canal.lstrip("#")
    dados = slack_client.listar_canais()
    for c in dados or []:
        if c.get("name") == nome:
            return c.get("id")
    return None


def publicar_canvas(canal: str = CANAL_PADRAO) -> bool:
    """Cria (uma vez) e mantem atualizado o Canvas fixo do canal.

    Por que Canvas e nao mensagem: mensagem some no rolar. Medido em
    31/07/2026, apenas 8% do que a Maria ve exige acao dela (3 de 40) -- num
    mural, esses 3 ficam soterrados pelos 37 que so aguardam o ML. O Canvas
    fica no topo, no mesmo lugar, e e reescrito a cada ciclo.

    O canvas_id e guardado em slack_canvas: um canal tem UM canvas proprio, e
    tentar criar de novo falharia. Se a linha sumir, recria."""
    from src.db.connection import get_db_connection, dict_cursor

    channel_id = _resolver_channel_id(canal)
    if not channel_id:
        print(f"canvas: nao consegui resolver o canal {canal}", file=sys.stderr)
        return False

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL_CANVAS)
            conn.commit()

        with dict_cursor(conn) as cur:
            cur.execute("""
                SELECT claim_id, order_id, claim_status, claim_stage,
                       reason_label, item_title, item_sku, order_total,
                       date_created
                FROM ml_devolucoes
                WHERE claim_status = 'opened'
                   OR (claim_status = 'closed'
                       AND date_updated ~ '^[0-9]{4}-'
                       AND date_updated::timestamptz > NOW() - interval '24 hours')
                ORDER BY date_updated DESC NULLS LAST
                LIMIT 300
            """)
            rows = cur.fetchall()

        data_str = datetime.now(timezone.utc).strftime("%d/%m")
        markdown = montar_canvas_quadro(rows, data_str)

        with conn.cursor() as cur:
            cur.execute("SELECT canvas_id FROM slack_canvas WHERE channel_id = %s",
                        (channel_id,))
            linha = cur.fetchone()

        canvas_id = linha[0] if linha else None
        if canvas_id:
            if slack_client.canvas_editar(canvas_id, markdown):
                with conn.cursor() as cur:
                    cur.execute("UPDATE slack_canvas SET atualizado_em = "
                                "CURRENT_TIMESTAMP WHERE channel_id = %s",
                                (channel_id,))
                conn.commit()
                return True
            # canvas sumiu (apagado a mao) -- limpa e recria abaixo
            print("canvas: edicao falhou, recriando", file=sys.stderr)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM slack_canvas WHERE channel_id = %s",
                            (channel_id,))
            conn.commit()

        novo = slack_client.canvas_criar(channel_id, markdown)
        if not novo:
            print("canvas: FALHOU ao criar", file=sys.stderr)
            return False
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO slack_canvas (channel_id, canvas_id) VALUES (%s,%s) "
                "ON CONFLICT (channel_id) DO UPDATE SET canvas_id=EXCLUDED.canvas_id,"
                " atualizado_em=CURRENT_TIMESTAMP",
                (channel_id, novo))
        conn.commit()
        return True
    finally:
        conn.close()


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


def resumo_diario(canal: str = CANAL_FECHAMENTO) -> int:
    """R7 -- fechamento do dia anterior no canal do chefe.

    Roda 1x por dia (cedo da manha) via workflow separado, fechando a
    contabilidade do dia anterior antes do ciclo de --once comecar a
    acompanhar o dia atual. Vai para #sac-fechamento, nao para o #sac
    operacional: agir (Maria) e medir (chefe) sao leituras diferentes.
    """
    from src.db.connection import get_db_connection, dict_cursor
    agora = datetime.now(timezone.utc)
    hoje_0h = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    ontem_0h = hoje_0h - timedelta(days=1)
    data_str = ontem_0h.strftime("%d/%m/%Y")

    conn = get_db_connection()
    try:
        with dict_cursor(conn) as cur:
            # DISTINCT ON: slack_notificados tem PK (claim_id, status), entao
            # um claim com varios estados 'closed:*' devolvia uma linha por
            # estado e o caso era somado duas vezes. montar_fechamento ainda
            # deduplica (defesa em profundidade), mas o dado que entra na
            # conta ja tem que ser unico na origem.
            cur.execute(
                "SELECT DISTINCT ON (sn.claim_id) "
                "       sn.claim_id, d.order_id, d.item_title, d.item_sku, s.total AS saldo "
                "FROM slack_notificados sn "
                "JOIN ml_devolucoes d ON d.claim_id = sn.claim_id "
                "LEFT JOIN meli_page_saldos s ON s.order_id = d.order_id "
                "WHERE sn.status LIKE 'closed:%%' "
                "  AND sn.avisado_em >= %s AND sn.avisado_em < %s "
                "ORDER BY sn.claim_id, sn.avisado_em DESC",
                (ontem_0h, hoje_0h))
            rows = cur.fetchall()
    finally:
        conn.close()

    texto, blocks = montar_fechamento(rows, data_str)
    # cria o canal e entra nele se preciso; se faltar permissao, garantir_canal
    # devolve None e seguimos pelo nome (funciona se alguem ja convidou o bot).
    destino = slack_client.garantir_canal(canal) or canal
    return 1 if enviar(destino, texto, blocks=blocks) else 0


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
    ap.add_argument("--canvas", action="store_true", help="cria/atualiza o Canvas fixo do canal")
    ap.add_argument("--canal", default=CANAL_PADRAO,
                    help=f"canal do Slack (default {CANAL_PADRAO})")
    args = ap.parse_args()
    # FAIL-LOUD: sem token nao ha "nada a fazer" -- ha uma configuracao
    # quebrada, e o run tem que ficar vermelho para alguem ver.
    if not slack_client._token():
        print("slack: sem Bot Token (SLACK_BOT_TOKEN ou arquivo local)", file=sys.stderr)
        sys.exit(1)
    if args.test:
        teste(args.canal)
    if args.canvas:
        ok = publicar_canvas(args.canal)
        print("canvas atualizado" if ok else "canvas: FALHOU")
        if not ok:
            sys.exit(1)
    if args.quadro:
        ok = publicar_quadro(args.canal)
        print("✓ quadro atualizado" if ok else "quadro: FALHOU", file=sys.stderr if not ok else None)
        sys.exit(0 if ok else 1)
    if args.resumo:
        # o fechamento tem canal proprio (o placar do chefe); so respeita
        # --canal se foi passado explicitamente.
        canal_fech = args.canal if args.canal != CANAL_PADRAO else CANAL_FECHAMENTO
        n = resumo_diario(canal_fech)
        if not n:
            print(f"fechamento diário: FALHOU ao enviar em {canal_fech}", file=sys.stderr)
            sys.exit(1)
        print(f"✓ fechamento diário enviado em {canal_fech}")
        return
    if args.once or not args.test:
        tentadas, enviadas = notificar_processos(args.canal)
        codigo = status_saida(tentadas, enviadas)
        if codigo:
            print(f"slack: {enviadas}/{tentadas} enviadas em {args.canal} — "
                  f"{tentadas - enviadas} FALHARAM", file=sys.stderr)
            sys.exit(codigo)
        print(f"✓ {enviadas}/{tentadas} enviada(s) em {args.canal}")


if __name__ == "__main__":
    main()



