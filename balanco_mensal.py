"""Balanço MENSAL do SAC — Canvas no #sac-fechamento, para a diretoria.

O fechamento diário (slack_notify.resumo_diario) responde "como foi ontem".
Este responde "como foi o mês" — a leitura que o chefe usa para decidir.

Duas decisões de projeto que valem ser ditas:

1. **Calcula do banco, nunca somando as mensagens diárias.** Mensagem é
   apresentação; banco é fato. Se um dia o job falhou, ou alguém editou o
   texto, somar mensagem propagaria o erro para o número do mês.

2. **Cobertura declarada.** Em julho/2026, 72 dos 292 casos fechados (25%)
   ainda não tinham saldo apurado. Publicar "prejuízo do mês: R$ 4.508" sem
   dizer que um quarto da base está fora seria apresentar número incompleto
   como se fosse fechado — exatamente o tipo de erro que custa credibilidade
   quando alguém confere.

Uso:
    python balanco_mensal.py                    # mês anterior
    python balanco_mensal.py --mes 2026-07      # mês específico
    python balanco_mensal.py --canal "#sac-teste"
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

sys.path.insert(0, str(Path(__file__).parent))

import slack_client
from slack_notify import CANAL_FECHAMENTO, _fmt_brl

_MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
          "agosto", "setembro", "outubro", "novembro", "dezembro")

# Abaixo disto o número é parcial demais para ser lido como resultado.
COBERTURA_MINIMA = 60.0


def nome_do_mes(ano: int, mes: int) -> str:
    return f"{_MESES[mes - 1]}/{ano}"


def periodo_do_mes(ano: int, mes: int) -> tuple[datetime, datetime]:
    """[início, fim) do mês — fim exclusivo evita contar o dia 1 do mês
    seguinte, que é o erro clássico de intervalo fechado."""
    ini = datetime(ano, mes, 1, tzinfo=timezone.utc)
    fim = (datetime(ano + 1, 1, 1, tzinfo=timezone.utc) if mes == 12
           else datetime(ano, mes + 1, 1, tzinfo=timezone.utc))
    return ini, fim


def resumir_mes(casos: Iterable[Mapping[str, Any]]) -> dict:
    """Consolida o mês. Deduplica por claim_id: o mesmo caso em duas linhas
    inflaria o prejuízo do chefe — defeito que já aconteceu no diário."""
    vistos, unicos = set(), []
    for c in casos:
        ident = c.get("claim_id")
        if ident is not None:
            if ident in vistos:
                continue
            vistos.add(ident)
        unicos.append(c)

    neg = zer = rev = sem = 0
    prejuizo = revertido = receita = reembolsado = 0.0
    for c in unicos:
        s = c.get("saldo")
        receita += float(c.get("order_total") or 0)
        reembolsado += float(c.get("amount_refunded") or 0)
        if s is None:
            sem += 1
            continue
        s = float(s)
        if s < 0:
            neg += 1
            prejuizo += s
        elif s > 0:
            rev += 1
            revertido += s
        else:
            zer += 1

    total = len(unicos)
    com_saldo = total - sem
    return {
        "casos": total,
        "negativos": neg,
        "zerados": zer,
        "revertidos": rev,
        "sem_saldo": sem,
        "prejuizo": round(prejuizo, 2),
        "revertido": round(revertido, 2),
        "saldo": round(prejuizo + revertido, 2),
        "receita": round(receita, 2),
        "reembolsado": round(reembolsado, 2),
        "cobertura_pct": round(100.0 * com_saldo / total, 1) if total else 0.0,
    }


def meses_a_publicar(hoje: Optional[datetime] = None,
                     quantos: int = 2) -> list[tuple[int, int]]:
    """Os meses que a rodada de hoje reabre, do mais recente para o mais antigo.

    Por que mais de um: a apuração de saldo do Mercado Livre chega DEPOIS do
    caso encerrar. Em 03/08/2026, julho ainda tinha 72 de 292 casos (25%) sem
    saldo. Publicar só uma vez, no dia 1, congelaria o mês num número parcial
    — e o chefe leria como fechado o que ainda ia mudar.
    """
    if quantos < 1:
        raise ValueError("quantos precisa ser >= 1 — rodada que publica zero "
                         "meses sai verde sem ter feito nada")
    hoje = hoje or datetime.now(timezone.utc)
    saida, ano, mes = [], hoje.year, hoje.month
    for _ in range(quantos):
        saida.append((ano, mes))
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    return saida


def variacao(atual: float, anterior: float) -> Optional[float]:
    """Variação percentual. None quando a base é zero — dividir por zero para
    exibir '∞%' não informa nada."""
    if not anterior:
        return None
    return round(100.0 * (atual - anterior) / abs(anterior), 1)


def montar_canvas_mensal(mes_label: str, r: Mapping[str, Any],
                         historico: list) -> str:
    """Markdown do Canvas mensal."""
    def brl(v):
        return _fmt_brl(v) if v is not None else "—"

    L = [f"# 📊 Balanço do SAC — {mes_label}", ""]

    if not r.get("casos"):
        L.append("_Nenhum caso encerrado neste mês._")
        return "\n".join(L)

    saldo = r.get("saldo")
    sinal = "🟢" if (saldo or 0) >= 0 else "🔴"
    L.append(f"## {sinal} Saldo do mês: **{brl(saldo)}**")
    L.append("")

    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| Casos encerrados | **{r['casos']}** |")
    L.append(f"| Receita das vendas | {brl(r.get('receita'))} |")
    L.append(f"| Prejuízo confirmado | {brl(r.get('prejuizo'))} |")
    L.append(f"| Revertido a favor | {brl(r.get('revertido'))} |")
    L.append(f"| Reembolsado pelo ML | {brl(r.get('reembolsado'))} |")
    L.append("")

    L.append("## Como os casos terminaram")
    L.append(f"- 🔴 **{r['negativos']}** com prejuízo — {brl(r.get('prejuizo'))}")
    L.append(f"- ⚪ **{r['zerados']}** o Mercado Livre cobriu (saldo zero)")
    L.append(f"- 🟢 **{r['revertidos']}** revertidos a favor — {brl(r.get('revertido'))}")
    L.append("")

    # Cobertura: o número é parcial? Diga antes que alguém descubra.
    cob = float(r.get("cobertura_pct") or 0)
    L.append("## Confiança do número")
    if cob >= COBERTURA_MINIMA:
        L.append(f"**{cob:.0f}% dos casos** já têm o saldo apurado no Mercado "
                 f"Livre. Os outros {r['sem_saldo']} entram quando a "
                 f"conciliação fechar.")
    else:
        L.append(f"⚠️ **Número parcial** — só {cob:.0f}% dos casos têm saldo "
                 f"apurado. Faltam {r['sem_saldo']} de {r['casos']}; o "
                 f"resultado final tende a mudar.")
    L.append("")

    if historico:
        L.append("## Meses anteriores")
        L.append("| Mês | Casos | Prejuízo |")
        L.append("|---|---|---|")
        for h in historico[:6]:
            L.append(f"| {h.get('mes')} | {h.get('casos')} | "
                     f"{brl(h.get('prejuizo'))} |")
        ant = historico[0].get("prejuizo")
        if ant and r.get("prejuizo") is not None:
            v = variacao(abs(float(r["prejuizo"])), abs(float(ant)))
            if v is not None:
                direcao = "acima" if v > 0 else "abaixo"
                L.append("")
                L.append(f"_Prejuízo {abs(v):.0f}% {direcao} do mês anterior._")
        L.append("")

    L.append("---")
    L.append(f"_Gerado automaticamente do banco · "
             f"{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')} UTC_")
    return "\n".join(L)


# --- coleta e publicação ---------------------------------------------------

def coletar_mes(ano: int, mes: int) -> tuple[dict, list]:
    from src.db.connection import get_db_connection, dict_cursor

    ini, fim = periodo_do_mes(ano, mes)
    conn = get_db_connection()
    try:
        with dict_cursor(conn) as cur:
            cur.execute("""
                SELECT d.claim_id, d.order_id, d.order_total,
                       d.amount_refunded, s.total AS saldo
                FROM ml_devolucoes d
                LEFT JOIN meli_page_saldos s ON s.order_id = d.order_id
                WHERE d.claim_status = 'closed'
                  AND d.date_updated ~ '^[0-9]{4}-'
                  AND d.date_updated::timestamptz >= %s
                  AND d.date_updated::timestamptz <  %s
            """, (ini, fim))
            casos = cur.fetchall()

            cur.execute("""
                SELECT TO_CHAR(d.date_updated::timestamptz, 'YYYY-MM') AS mes,
                       COUNT(DISTINCT d.claim_id) AS casos,
                       COALESCE(SUM(s.total) FILTER (WHERE s.total < 0), 0) AS prejuizo
                FROM ml_devolucoes d
                LEFT JOIN meli_page_saldos s ON s.order_id = d.order_id
                WHERE d.claim_status = 'closed'
                  AND d.date_updated ~ '^[0-9]{4}-'
                  AND d.date_updated::timestamptz < %s
                  AND d.date_updated::timestamptz >= %s - interval '6 months'
                GROUP BY 1 ORDER BY 1 DESC
            """, (ini, ini))
            historico = cur.fetchall()
    finally:
        conn.close()

    return resumir_mes(casos), [dict(h) for h in historico]


def publicar(ano: int, mes: int, canal: str = CANAL_FECHAMENTO) -> bool:
    from src.db.connection import get_db_connection

    resumo, historico = coletar_mes(ano, mes)
    markdown = montar_canvas_mensal(nome_do_mes(ano, mes), resumo, historico)

    cid = slack_client.garantir_canal(canal)
    if not cid:
        print(f"balanco: nao resolveu o canal {canal}", file=sys.stderr)
        return False

    # Um Canvas por MES: o de julho nao pode ser sobrescrito pelo de agosto,
    # senao o historico some da tela justamente para quem quer comparar.
    chave = f"{canal}:{ano}-{mes:02d}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS slack_canvas_mensal (
                    chave TEXT PRIMARY KEY,
                    canvas_id TEXT NOT NULL,
                    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            cur.execute("SELECT canvas_id FROM slack_canvas_mensal WHERE chave = %s",
                        (chave,))
            linha = cur.fetchone()

        canvas_id = linha[0] if linha else None
        if canvas_id and slack_client.canvas_editar(canvas_id, markdown):
            with conn.cursor() as cur:
                cur.execute("UPDATE slack_canvas_mensal SET atualizado_em = "
                            "CURRENT_TIMESTAMP WHERE chave = %s", (chave,))
            conn.commit()
            print(f"balanco de {nome_do_mes(ano, mes)} atualizado em {canal}")
            return True

        novo = slack_client.canvas_criar(
            cid, markdown, titulo=f"Balanço {nome_do_mes(ano, mes)}")
        if not novo:
            print("balanco: FALHOU ao criar o canvas", file=sys.stderr)
            return False
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO slack_canvas_mensal (chave, canvas_id) VALUES (%s,%s) "
                "ON CONFLICT (chave) DO UPDATE SET canvas_id = EXCLUDED.canvas_id, "
                "atualizado_em = CURRENT_TIMESTAMP", (chave, novo))
        conn.commit()
        print(f"balanco de {nome_do_mes(ano, mes)} publicado em {canal}")
        return True
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mes", help="AAAA-MM (um mês específico)")
    ap.add_argument("--meses", type=int, default=2,
                    help="quantos meses reabrir a partir do corrente "
                         "(padrão 2: o mês em curso e o anterior, que ainda "
                         "recebe saldo atrasado)")
    ap.add_argument("--canal", default=CANAL_FECHAMENTO)
    ap.add_argument("--dry-run", action="store_true",
                    help="mostra o markdown sem publicar")
    args = ap.parse_args()

    if args.mes:
        alvos = [tuple(int(x) for x in args.mes.split("-"))]
    else:
        alvos = meses_a_publicar(quantos=args.meses)

    if args.dry_run:
        for ano, mes in alvos:
            resumo, historico = coletar_mes(ano, mes)
            print(montar_canvas_mensal(nome_do_mes(ano, mes), resumo, historico))
            print()
        return 0

    # FALHAR ALTO: um mês que não publicou não pode ser encoberto pelo outro
    # que publicou. A rodada segue (o mês seguinte ainda vale), mas sai 1.
    falhou = False
    for ano, mes in alvos:
        if not publicar(ano, mes, args.canal):
            print(f"balanco: FALHOU em {nome_do_mes(ano, mes)}", file=sys.stderr)
            falhou = True
    return 1 if falhou else 0


if __name__ == "__main__":
    sys.exit(main())
