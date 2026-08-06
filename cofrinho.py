"""Os dois cofrinhos da Thayna -- o positivo e o negativo, no mes.

Ela desenhou dois potes no canto da folha: um enche quando a venda fica de
pe, outro quando o dinheiro sai. Diario, acumulando ate o fim do mes.

**O maior risco desta feature nao e tecnico, e de leitura.** Ja existe um
numero de dinheiro do SAC no Slack: o prejuizo conciliado com o extrato do
Mercado Livre (julho fechou em R$ 5.930,84, defendido caso a caso). Dois
numeros de dinheiro no mesmo lugar, sem dizer o que sao, viram a pergunta
"entao qual dos dois e o certo?" -- e ai os dois perdem valor. Sao coisas
diferentes de proposito:

    cofrinho = valor da VENDA EM DISPUTA, marcado pela Maria ao fechar o
               caso. Placar do dia dela, sai na hora.
    balanco  = PREJUIZO REAL, batido com o extrato do ML. Chega dias depois,
               ja descontando tarifa, frete e o que o ML cobriu.

A propria mensagem carrega essa distincao, e ha teste exigindo isso.

Tres invariantes de dado:

**O dia e o do DESFECHO, nao o da abertura.** Caso aberto em 28/07 e recusado
em 06/08 e de agosto. Somar pela abertura joga dinheiro de agosto em julho --
mes que ja foi apresentado e fechado.

**23h nao vira amanha.** A marcacao e TIMESTAMPTZ e o banco devolve em UTC.
Uma recusa as 23h30 de 06/08 lida como UTC cai em 07/08 -- o mesmo defeito
que ja comeu um dia da previsao de entrega.

**Caso sem valor nao vale zero.** Somar zero calado faz o cofrinho mentir
para baixo, e ninguem descobre. Ele sai separado, para alguem olhar.

Uso:
    python cofrinho.py --dry-run
    python cofrinho.py --publicar
"""
from __future__ import annotations

import argparse
import calendar
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

sys.path.insert(0, str(Path(__file__).parent))

import card_maria
from slack_notify import _fmt_brl

CANAL = "#sac"
CHAVE_PAINEL = "cofrinho"

# O Brasil nao tem horario de verao desde 2019, entao o offset fixo e exato e
# nao depende do tzdata estar instalado na maquina que roda o job.
BRT = timezone(timedelta(hours=-3))

# Marcacoes que decidem o dinheiro. "finalizar" e arquivamento: vem depois e
# nao muda o dia em que o caso foi ganho ou perdido.
DESFECHOS = {"recusado": "positivo", "reembolsado": "negativo"}

MAX_DIAS_NA_MENSAGEM = 10


def valor_em_jogo(caso: Mapping[str, Any]) -> Optional[float]:
    """O valor da venda em disputa. None quando nao sabemos -- nunca zero.

    Zero calado faz o cofrinho mentir para baixo e ninguem descobre.
    """
    try:
        v = float(caso.get("valor"))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _quando(texto: Any) -> Optional[datetime]:
    if isinstance(texto, datetime):
        d = texto
    else:
        if not texto:
            return None
        try:
            d = datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    # Sem fuso, o carimbo ja e local. Com fuso, traz para o dia da Maria.
    return d.astimezone(BRT) if d.tzinfo else d


def dia_do_desfecho(timeline: Iterable[Mapping[str, Any]]) -> Optional[date]:
    """O dia em que o caso foi ganho ou perdido, no horario de Brasilia."""
    for ev in reversed(list(timeline or [])):
        if str(ev.get("etapa") or "") in DESFECHOS:
            d = _quando(ev.get("quando"))
            return d.date() if d else None
    return None


def _sinal(timeline: Iterable[Mapping[str, Any]]) -> Optional[str]:
    for ev in reversed(list(timeline or [])):
        s = DESFECHOS.get(str(ev.get("etapa") or ""))
        if s:
            return s
    return None


def acumular(casos: Iterable[Mapping[str, Any]], ano: int, mes: int) -> dict:
    """Soma os dois potes do mes, e o detalhe por dia."""
    r: dict = {"positivo": 0.0, "negativo": 0.0, "saldo": 0.0,
               "n_positivo": 0, "n_negativo": 0, "por_dia": {},
               "sem_valor": []}

    for c in casos or []:
        timeline = c.get("timeline") or []
        sinal = _sinal(timeline)
        if not sinal:
            continue
        dia = dia_do_desfecho(timeline)
        if not dia or (dia.year, dia.month) != (ano, mes):
            continue

        valor = valor_em_jogo(c)
        if valor is None:
            # Nao entra na soma NEM na contagem: "1 caso, R$ 0,00" faria
            # alguem concluir que a venda valia nada.
            r["sem_valor"].append(c.get("claim_id"))
            continue

        r[sinal] += valor
        r[f"n_{sinal}"] += 1
        d = r["por_dia"].setdefault(
            dia, {"positivo": 0.0, "negativo": 0.0,
                  "n_positivo": 0, "n_negativo": 0})
        d[sinal] += valor
        d[f"n_{sinal}"] += 1

    r["saldo"] = r["positivo"] - r["negativo"]
    return r


def _mes_por_extenso(d: date) -> str:
    nomes = ("janeiro", "fevereiro", "março", "abril", "maio", "junho",
             "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
    return nomes[d.month - 1]


def blocos_do_cofrinho(resumo: Mapping[str, Any], hoje: date) -> list[dict]:
    """O placar do mes, do jeito que a Thayna desenhou: dois potes."""
    def sec(t):
        return {"type": "section", "text": {"type": "mrkdwn", "text": t}}

    blocos: list[dict] = [{
        "type": "header",
        "text": {"type": "plain_text",
                 "text": f"🐷 Cofrinho de {_mes_por_extenso(hoje)} "
                         f"— até {hoje:%d/%m}",
                 "emoji": True},
    }]

    if not (resumo["n_positivo"] or resumo["n_negativo"]
            or resumo["sem_valor"]):
        blocos.append(sec("_Nenhum caso fechado ainda neste mês._"))
    else:
        blocos.append(sec(
            f"🟢 *Seguramos*  {_fmt_brl(resumo['positivo'])}  "
            f"_({resumo['n_positivo']} caso"
            f"{'s' if resumo['n_positivo'] != 1 else ''})_\n"
            f"🔴 *Escapou*  {_fmt_brl(resumo['negativo'])}  "
            f"_({resumo['n_negativo']} caso"
            f"{'s' if resumo['n_negativo'] != 1 else ''})_\n"
            f"────────────\n"
            f"*Saldo*  {_fmt_brl(resumo['saldo'])}"))

        de_hoje = resumo["por_dia"].get(hoje)
        if de_hoje:
            blocos.append({"type": "context", "elements": [{
                "type": "mrkdwn",
                "text": f"*Hoje ({hoje:%d/%m}):*  "
                        f"🟢 {_fmt_brl(de_hoje['positivo'])} "
                        f"({de_hoje['n_positivo']})  ·  "
                        f"🔴 {_fmt_brl(de_hoje['negativo'])} "
                        f"({de_hoje['n_negativo']})"}]})
        else:
            blocos.append({"type": "context", "elements": [{
                "type": "mrkdwn",
                "text": f"*Hoje ({hoje:%d/%m}):* nenhum caso fechado ainda."}]})

        dias = sorted(resumo["por_dia"], reverse=True)[:MAX_DIAS_NA_MENSAGEM]
        if dias:
            linhas = [
                f"{d:%d/%m} · 🟢 {_fmt_brl(resumo['por_dia'][d]['positivo'])} "
                f"· 🔴 {_fmt_brl(resumo['por_dia'][d]['negativo'])}"
                for d in dias]
            blocos.append({"type": "context", "elements": [
                {"type": "mrkdwn", "text": "\n".join(linhas)}]})

    if resumo["sem_valor"]:
        n = len(resumo["sem_valor"])
        blocos.append(sec(
            f"⚠️ {n} caso{'s' if n != 1 else ''} fechado"
            f"{'s' if n != 1 else ''} *sem valor* no sistema — fora da conta "
            f"acima, para não puxar o placar para baixo calado. "
            f"Claims: `{', '.join(str(c) for c in resumo['sem_valor'][:8])}`"))

    blocos.append({"type": "divider"})
    blocos.append({"type": "context", "elements": [{
        "type": "mrkdwn",
        "text": "_Isto é o *valor da venda em disputa*, marcado aqui no "
                "momento em que o caso é fechado. Não é o balanço: o "
                "*prejuízo real*, conciliado com o extrato do Mercado Livre "
                "(já com tarifa, frete e o que o ML cobriu), sai no "
                "*#sac-fechamento* e chega dias depois. Os dois números não "
                "são a mesma coisa e não vão bater._"}]})
    return blocos[:50]


# --- I/O -------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS sac_paineis (
    chave       TEXT PRIMARY KEY,
    channel_id  TEXT NOT NULL,
    ts          TEXT NOT NULL,
    atualizado  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

SQL = """
    SELECT d.claim_id, d.item_title,
           COALESCE(i.valor, d.order_total) AS valor
    FROM ml_devolucoes d
    LEFT JOIN (
        SELECT order_id, SUM(unidades * preco_unitario) AS valor
        FROM order_items GROUP BY order_id
    ) i ON i.order_id = d.order_id::text
    WHERE d.claim_id IN (
        SELECT DISTINCT claim_id FROM sac_timeline WHERE etapa = ANY(%s)
    )
"""


def carregar(conn, ano: int, mes: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(SQL, (list(DESFECHOS),))
    casos = card_maria._linhas(cur)
    timelines = card_maria.buscar_timelines(
        conn, [c["claim_id"] for c in casos])
    for c in casos:
        c["timeline"] = timelines.get(c["claim_id"], [])
    return casos


def publicar(canal: str = CANAL, dry_run: bool = False) -> int:
    from src.db.connection import get_db_connection

    import slack_client

    hoje = datetime.now(BRT).date()
    conn = get_db_connection()
    resumo = acumular(carregar(conn, hoje.year, hoje.month),
                      hoje.year, hoje.month)
    blocos = blocos_do_cofrinho(resumo, hoje)

    if dry_run:
        for b in blocos:
            t = (b.get("text") or {}).get("text") or ""
            if not t and b.get("elements"):
                t = b["elements"][0].get("text", "")
            print(t or f"[{b['type']}]")
        return 0

    with conn.cursor() as cur:
        cur.execute(_DDL)
    conn.commit()

    cid = slack_client.garantir_canal(canal)
    if not cid:
        print(f"não consegui abrir {canal}", file=sys.stderr)
        return 1

    resumo_txt = (f"Cofrinho de {_mes_por_extenso(hoje)}: "
                  f"seguramos {_fmt_brl(resumo['positivo'])}, "
                  f"escapou {_fmt_brl(resumo['negativo'])}")

    cur = conn.cursor()
    cur.execute("SELECT ts FROM sac_paineis WHERE chave = %s AND channel_id = %s",
                (CHAVE_PAINEL, cid))
    linha = cur.fetchone()

    if linha:
        # Um painel por mes, atualizado no lugar -- senao o canal acumula um
        # placar novo por dia e ninguem sabe qual e o de valer.
        if slack_client.update_message(cid, linha[0], resumo_txt, blocks=blocos):
            print(f"cofrinho atualizado: {resumo_txt}")
            return 0
        print("falha ao atualizar o cofrinho", file=sys.stderr)
        return 1

    r = slack_client.post_message_full(cid, resumo_txt, blocks=blocos)
    if not r or not r.get("ts"):
        print("falha ao publicar o cofrinho", file=sys.stderr)
        return 1
    with conn.cursor() as c2:
        c2.execute("""
            INSERT INTO sac_paineis (chave, channel_id, ts) VALUES (%s,%s,%s)
            ON CONFLICT (chave) DO UPDATE
              SET ts = EXCLUDED.ts, channel_id = EXCLUDED.channel_id,
                  atualizado = now()
        """, (CHAVE_PAINEL, cid, r["ts"]))
    conn.commit()
    print(f"cofrinho publicado: {resumo_txt}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--publicar", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--canal", default=CANAL)
    args = ap.parse_args()

    if not (args.publicar or args.dry_run):
        ap.print_help()
        return 0
    return publicar(canal=args.canal, dry_run=not args.publicar)


if __name__ == "__main__":
    sys.exit(main())
