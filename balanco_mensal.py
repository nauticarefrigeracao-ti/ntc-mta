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
import re
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


def fmt_cobertura(pct: float, faltando: int) -> str:
    """Cobertura como texto — nunca "100%" com caso faltando.

    291 de 292 é 99,66%, e `:.0f` arredondava para "100%" enquanto o texto
    logo abaixo dizia "os outros 1 entram quando a conciliação fechar". Duas
    frases contraditórias na mesma seção. É uma mentira pequena — e é sempre
    a pequena que alguém confere.
    """
    if faltando <= 0:
        return "100%"
    # Vírgula decimal: o texto é lido em português, por quem confere em reais.
    return f"{min(pct, 99.9):.1f}".replace(".", ",") + "%"


def secao_onde_vaza(motivos: list, skus: list) -> str:
    """"Qual produto está me custando dinheiro?" — hoje isso exige SQL.

    Medição de maio a julho/2026: **um** motivo ("Produto não funciona", 96
    casos, R$ 9.937,78) responde por 77% de todo o prejuízo apurado. E não é
    motivo de SAC — é de produto, fornecedor ou anúncio. O SAC paga a conta de
    uma decisão tomada antes dele. A família NR1058 sozinha: 18 casos,
    R$ 2.602,50.

    Vai em markdown, no Canvas que o chefe já abre. Sem componente visual
    novo: a regra do Design System vale aqui também.
    """
    if not motivos and not skus:
        return ""  # cabeçalho sem linha embaixo faz o leitor achar que quebrou

    L = ["## 💸 Onde o dinheiro vaza", ""]

    if motivos:
        total = sum(abs(float(m.get("prejuizo") or 0)) for m in motivos)
        L.append("| Motivo | Casos | Prejuízo |")
        L.append("|---|---|---|")
        for m in motivos[:6]:
            L.append(f"| {motivo_legivel(m.get('chave'))} | {m.get('casos')} | "
                     f"{_fmt_brl(abs(float(m.get('prejuizo') or 0)))} |")
        # "96 casos" não move ninguém; "77% de todo o prejuízo" move.
        topo = abs(float(motivos[0].get("prejuizo") or 0))
        if total and topo / total >= 0.5:
            L.append("")
            L.append(f"_**{motivo_legivel(motivos[0].get('chave'))}** sozinho "
                     f"responde por {100 * topo / total:.0f}% do prejuízo — e "
                     f"não é um problema de atendimento._")
        L.append("")

    if skus:
        L.append("| SKU | Casos | Prejuízo |")
        L.append("|---|---|---|")
        for s in skus[:6]:
            L.append(f"| {s.get('chave')} | {s.get('casos')} | "
                     f"{_fmt_brl(abs(float(s.get('prejuizo') or 0)))} |")
        L.append("")

    return "\n".join(L)


def motivo_legivel(chave: Any) -> str:
    """Código cru do ML não vai para a diretoria.

    229 códigos como `PDD9952` vivem na base. Publicar isso já foi defeito
    aqui — o leitor não tem como saber o que significa, e um relatório que
    exige decodificação não é lido.
    """
    texto = str(chave or "").strip() or "(sem motivo)"
    if re.fullmatch(r"[A-Z]{2,4}\d{3,}", texto):
        return f"código {texto} (sem tradução do ML)"
    return texto


def montar_canvas_mensal(mes_label: str, r: Mapping[str, Any],
                         historico: list,
                         motivos: Optional[list] = None,
                         skus: Optional[list] = None) -> str:
    """Markdown do Canvas mensal.

    O TITULAR É O PREJUÍZO, não um "saldo".
    A versão anterior somava `prejuizo + revertido` e publicava
    "🟢 Saldo do mês: R$ 15.826,44" para julho/2026. Medindo o que é um
    `total > 0` na página do Mercado Livre:

        pedido …126735890 | produto 1.380,97 | cancelamentos 0,00 | +1.164,12

    Cancelamentos zerados: a venda FICOU DE PÉ. Houve reclamação, não houve
    devolução, o dinheiro entrou como em qualquer venda. Isso é receita, não
    ganho do SAC. Somado ao prejuízo, dizia à diretoria que a área de
    devoluções é lucrativa — e é em cima disso que ele decide.
    """
    def brl(v):
        return _fmt_brl(v) if v is not None else "—"

    L = [f"# 📊 Balanço do SAC — {mes_label}", ""]

    if not r.get("casos"):
        L.append("_Nenhum caso encerrado neste mês._")
        return "\n".join(L)

    prejuizo = abs(float(r.get("prejuizo") or 0))
    neg = r.get("negativos") or 0
    sem_custo = (r.get("casos") or 0) - neg
    if neg:
        # Sem o sinal de menos: a palavra "Prejuízo" já diz a direção, e
        # "Prejuízo: R$ -6.102,43" é negativo duplo para quem lê.
        L.append(f"## 🔴 Prejuízo do mês: **{brl(prejuizo)}**")
        L.append("")
        L.append(f"Em **{neg}** dos {r['casos']} casos encerrados. "
                 f"Os outros **{sem_custo}** não custaram nada.")
    else:
        L.append("## ⚪ Sem prejuízo no mês")
        L.append("")
        L.append(f"Nenhum caso custou dinheiro entre os {r['casos']} "
                 f"encerrados.")
    L.append("")

    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| Casos encerrados | **{r['casos']}** |")
    L.append(f"| Prejuízo confirmado | **{brl(prejuizo)}** |")
    L.append(f"| Receita das vendas envolvidas | {brl(r.get('receita'))} |")
    L.append(f"| Reembolsado pelo ML | {brl(r.get('reembolsado'))} |")
    L.append("")

    L.append("## Como os casos terminaram")
    L.append(f"- 🔴 **{r['negativos']}** custaram dinheiro — {brl(prejuizo)}")
    L.append(f"- ⚪ **{r['zerados']}** o Mercado Livre cobriu (saldo zero)")
    L.append(f"- 🟢 **{r['revertidos']}** a venda ficou de pé, sem devolução — "
             f"{brl(r.get('revertido'))} recebidos normalmente")
    L.append("")
    L.append("_A última linha é receita de venda, não ganho da disputa: "
             "houve reclamação, não houve devolução._")
    L.append("")

    # Cobertura: o número é parcial? Diga antes que alguém descubra.
    cob = float(r.get("cobertura_pct") or 0)
    faltando = int(r.get("sem_saldo") or 0)
    L.append("## Confiança do número")
    if cob >= COBERTURA_MINIMA:
        txt = f"**{fmt_cobertura(cob, faltando)} dos casos** já têm o saldo " \
              f"apurado no Mercado Livre."
        if faltando:
            txt += (f" Falta{'m' if faltando > 1 else ''} {faltando}, que "
                    f"entra{'m' if faltando > 1 else ''} quando a conciliação "
                    f"fechar.")
        L.append(txt)
    else:
        L.append(f"⚠️ **Número parcial** — só {fmt_cobertura(cob, faltando)} "
                 f"dos casos têm saldo apurado. Faltam {faltando} de "
                 f"{r['casos']}; o resultado final tende a mudar.")
    L.append("")

    vaza = secao_onde_vaza(motivos or [], skus or [])
    if vaza:
        L.append(vaza)

    if historico:
        L.append("## Meses anteriores")
        L.append("| Mês | Casos | Prejuízo | Apurado |")
        L.append("|---|---|---|---|")
        parcial = False
        for h in historico[:6]:
            hc = h.get("cobertura_pct")
            if hc is not None and float(hc) < COBERTURA_MINIMA:
                parcial = True
            L.append(f"| {h.get('mes')} | {h.get('casos')} | "
                     f"{brl(h.get('prejuizo'))} | "
                     f"{f'{float(hc):.0f}%' if hc is not None else '—'} |")
        if parcial:
            # Janeiro tinha 57% de cobertura e julho tem 99,7%. Ler a
            # diferença como piora do negócio é ler artefato de coleta.
            L.append("")
            L.append("_Meses com apuração **parcial** têm prejuízo "
                     "subestimado — a comparação não é direta._")
        ant = historico[0].get("prejuizo")
        if ant and r.get("prejuizo") is not None:
            v = variacao(prejuizo, abs(float(ant)))
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

def coletar_mes(ano: int, mes: int) -> tuple[dict, list, list, list]:
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

            # A cobertura de cada mês vem junto: janeiro tinha 57% apurado e
            # julho tem 99,7%. Sem declarar isso, a tabela histórica faz
            # artefato de coleta parecer tendência do negócio.
            cur.execute("""
                SELECT TO_CHAR(d.date_updated::timestamptz, 'YYYY-MM') AS mes,
                       COUNT(DISTINCT d.claim_id) AS casos,
                       COALESCE(SUM(s.total) FILTER (WHERE s.total < 0), 0) AS prejuizo,
                       ROUND(100.0 * COUNT(DISTINCT d.claim_id)
                             FILTER (WHERE s.total IS NOT NULL)
                             / NULLIF(COUNT(DISTINCT d.claim_id), 0), 1)
                         AS cobertura_pct
                FROM ml_devolucoes d
                LEFT JOIN meli_page_saldos s ON s.order_id = d.order_id
                WHERE d.claim_status = 'closed'
                  AND d.date_updated ~ '^[0-9]{4}-'
                  AND d.date_updated::timestamptz < %s
                  AND d.date_updated::timestamptz >= %s - interval '6 months'
                GROUP BY 1 ORDER BY 1 DESC
            """, (ini, ini))
            historico = cur.fetchall()

            # Onde o dinheiro vaza. A janela e de 3 MESES, nao do mes: um
            # motivo com 4 casos no mes nao diz nada, mas 96 casos no
            # trimestre dizem para quem decide compra.
            vaza = {}
            for campo, chave in (("reason_label", "motivos"),
                                 ("item_sku", "skus")):
                cur.execute(f"""
                    SELECT COALESCE(NULLIF(TRIM(d.{campo}), ''), '(sem dado)')
                             AS chave,
                           COUNT(*) AS casos,
                           ROUND(SUM(s.total)::numeric, 2) AS prejuizo
                    FROM ml_devolucoes d
                    JOIN meli_page_saldos s ON s.order_id = d.order_id
                    WHERE d.claim_status = 'closed' AND s.total < 0
                      AND d.date_updated ~ '^[0-9]{{4}}-'
                      AND d.date_updated::timestamptz <  %s
                      AND d.date_updated::timestamptz >= %s - interval '3 months'
                    GROUP BY 1 ORDER BY prejuizo ASC LIMIT 6
                """, (fim, fim))
                vaza[chave] = [dict(x) for x in cur.fetchall()]
    finally:
        conn.close()

    return (resumir_mes(casos), [dict(h) for h in historico],
            vaza["motivos"], vaza["skus"])


def publicar(ano: int, mes: int, canal: str = CANAL_FECHAMENTO) -> bool:
    from src.db.connection import get_db_connection

    resumo, historico, motivos, skus = coletar_mes(ano, mes)
    markdown = montar_canvas_mensal(nome_do_mes(ano, mes), resumo, historico,
                                    motivos=motivos, skus=skus)

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
            resumo, historico, motivos, skus = coletar_mes(ano, mes)
            print(montar_canvas_mensal(nome_do_mes(ano, mes), resumo,
                                       historico, motivos=motivos, skus=skus))
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

