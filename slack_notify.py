"""Notificador Slack do SAC -- reclamacoes, devolucoes e cancelamentos do ML no #sac.
================================================================================
Le a URL do webhook da variavel de ambiente SLACK_WEBHOOK_URL (uso em GitHub
Actions) ou, em modo local, do arquivo fora do repo:
C:\\Users\\Pichau\\slack_webhook.txt

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
python slack_notify.py --test   # mensagem de resumo (demo)
python slack_notify.py --once   # notifica processos novos ou com mudanca de estado
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WEBHOOK_FILE = Path(r"C:\Users\Pichau\slack_webhook.txt")

_DDL = """
CREATE TABLE IF NOT EXISTS slack_notificados (
    claim_id BIGINT NOT NULL,
    status TEXT NOT NULL,
    avisado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (claim_id, status)
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


def montar_mensagem(row: Mapping[str, Any], saldo: Optional[float] = None,
                     atualizacao: bool = False, agora: Optional[datetime] = None) -> str:
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
    return "\n".join(linhas)


def _fmt_brl(v) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"

# ---------------------------------------------------------------------------
# I/O -- Slack, Neon, CLI
# ---------------------------------------------------------------------------

def _webhook() -> Optional[str]:
    env = os.environ.get("SLACK_WEBHOOK_URL")
    if env and env.startswith("https://hooks.slack.com/"):
        return env
    try:
        url = WEBHOOK_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        return url if url.startswith("https://hooks.slack.com/") else None
    except Exception:
        return None


def enviar(texto: str) -> bool:
    url = _webhook()
    if not url:
        return False
    payload = {"text": texto}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as exc:
        print(f"slack: falha no envio: {type(exc).__name__}: {exc}")
        return False


def _saldo_do_pedido(cur, order_id) -> Optional[float]:
    cur.execute("SELECT total FROM meli_page_saldos WHERE order_id = %s", (order_id,))
    row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _chaves_anteriores(cur, claim_id) -> set[str]:
    cur.execute("SELECT status FROM slack_notificados WHERE claim_id = %s", (claim_id,))
    return {r[0] for r in cur.fetchall()}


def notificar_processos() -> int:
    """Processos novos OU com mudanca de estado ainda nao avisados -> #sac."""
    from src.db.connection import get_db_connection, dict_cursor
    conn = get_db_connection()
    enviadas = 0
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
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
        with conn.cursor() as cur:
            for row in rows:
                anteriores = _chaves_anteriores(cur, row["claim_id"])
                chave = chave_estado(row)
                if not deve_notificar(anteriores, chave):
                    continue
                atualizacao = eh_atualizacao(anteriores)
                saldo = _saldo_do_pedido(cur, row["order_id"]) if row["claim_status"] == "closed" else None
                texto = montar_mensagem(row, saldo, atualizacao)
                if enviar(texto):
                    cur.execute(
                        "INSERT INTO slack_notificados (claim_id, status) VALUES (%s,%s) "
                        "ON CONFLICT DO NOTHING", (row["claim_id"], chave))
                    conn.commit()
                    enviadas += 1
    finally:
        conn.close()
    return enviadas


def resumo_diario() -> int:
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

    return 1 if enviar(texto) else 0


def teste() -> None:
    from src.db.connection import get_db_connection
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COALESCE(SUM(order_total),0) FROM ml_devolucoes WHERE claim_status='opened'")
        n, v = cur.fetchone()
    conn.close()
    ok = enviar(
        f":bar_chart: *Painel de Devoluções — Náutica Refrigeração*\n"
        f"Neste momento: *{n} disputas em andamento*, {_fmt_brl(v)} em jogo.\n"
        f"Toda reclamação, mediação, devolução e cancelamento do Mercado Livre chega aqui "
        f"no *#sac* com categoria, motivo, valor e prazo estimado.\n"
        f"<https://ntc-mta.streamlit.app|Abrir o painel completo>")
    print("✓ mensagem de teste enviada ao #sac" if ok else "✗ não enviou — confira o webhook")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--resumo-diario", action="store_true", dest="resumo")
    args = ap.parse_args()
    if not _webhook():
        print("slack: sem webhook (SLACK_WEBHOOK_URL ou arquivo local) — nada a fazer")
        return
    if args.test:
        teste()
    if args.resumo:
        n = resumo_diario()
        print("✓ resumo diário enviado ao #sac" if n else "resumo diário: nada a enviar")
        return
    if args.once or not args.test:
        n = notificar_processos()
        print(f"✓ {n} processo(s) notificado(s) no #sac")


if __name__ == "__main__":
    main()
