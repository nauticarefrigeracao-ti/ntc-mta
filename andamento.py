"""Canal #andamento -- o que esta sendo feito, para quem paga a conta.

Pedido em 05/08/2026, na reuniao de conciliacao: a diretoria quer acompanhar
o que esta em andamento, o que ficou pronto e quanto tempo levou, sem precisar
abrir Slack tecnico nem perguntar.

Quem le e o Gabriel e a Thayna. Eles nao sabem -- e nao precisam saber -- o
que e paginacao, invariante ou id de envio. Precisam saber tres coisas:

    o que melhorou para a empresa, desde quando, e se ja esta funcionando.

Por isso o texto nao tem jargao, e isso e cobrado por TESTE, nao por bom
senso: palavra tecnica na mensagem quebra a suite. "Corrigido bug no
agregador" nao informa nada; "o prejuizo do mes estava R$ 144 maior do que e
de verdade" informa.

Uso:
    python andamento.py --publicar          # le itens.json e publica
    python andamento.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

sys.path.insert(0, str(Path(__file__).parent))

import slack_client

CANAL = "#andamento"
ARQ_ITENS = Path(__file__).with_name("andamento_itens.json")

# Palavras que ja vazaram para mensagem lida pela diretoria, ou que vazariam
# na primeira distracao. Se o chefe precisa perguntar o que significa, a
# atualizacao nao cumpriu o papel.
JARGAO = (
    "commit", "deploy", "bug", "api", "endpoint", "query", "sql", "cache",
    "token", "canvas", "webhook", "backend", "frontend", "refatorar",
    "invariante", "paginacao", "shipment", "payload", "script", "log",
    "pipeline", "branch", "merge", "rollback", "patch", "hotfix",
)


def fmt_duracao_humana(minutos: Optional[float]) -> str:
    """Tempo como uma pessoa fala.

    None = ainda correndo. Devolver "0" ali diria que foi instantaneo.
    """
    if minutos is None:
        return "em andamento"
    m = int(minutos)
    if m <= 0:
        return "menos de 1 minuto"
    if m < 60:
        return f"{m} minuto" if m == 1 else f"{m} minutos"
    if m < 60 * 24:
        h, resto = divmod(m, 60)
        return f"{h}h{resto:02d}" if resto else f"{h}h"
    dias, resto = divmod(m, 60 * 24)
    horas = resto // 60
    txt = f"{dias} dia" if dias == 1 else f"{dias} dias"
    return f"{txt} e {horas}h" if horas else txt


def linha_de_item(item: Mapping[str, Any]) -> str:
    """Uma linha da atualizacao.

    Sem o ganho declarado, a linha nao sai: "ajustes internos" e ruido, e
    ruido no canal da diretoria treina a diretoria a nao ler o canal.
    """
    ganho = (item.get("ganho") or "").strip()
    if not ganho:
        raise ValueError(
            f"item {item.get('titulo')!r} sem 'ganho': toda atualizacao "
            "precisa dizer o que mudou para a empresa, em uma frase")

    tempo = fmt_duracao_humana(item.get("minutos"))
    if item.get("estado") == "pronto":
        return f"• *{item['titulo']}* — já está no ar. {ganho}. _(levou {tempo})_"
    return f"• *{item['titulo']}* — {ganho}. _({tempo})_"


def montar_atualizacao(itens: list, data_str: str) -> str:
    prontos = [i for i in itens if i.get("estado") == "pronto"]
    fazendo = [i for i in itens if i.get("estado") != "pronto"]

    L = [f"📌 *Como está o trabalho — {data_str}*", ""]
    if not itens:
        L.append("_Sem novidades desde a última atualização._")
        return "\n".join(L)

    if prontos:
        L.append("*Pronto e funcionando*")
        L += [linha_de_item(i) for i in prontos]
        L.append("")
    if fazendo:
        L.append("*Em andamento*")
        L += [linha_de_item(i) for i in fazendo]
    return "\n".join(L)


# --- I/O -------------------------------------------------------------------

def carregar_itens(caminho: Path = ARQ_ITENS) -> list:
    if not caminho.exists():
        return []
    return json.loads(caminho.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canal", default=CANAL)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    itens = carregar_itens()
    hoje = datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y")
    texto = montar_atualizacao(itens, hoje)

    if args.dry_run:
        print(texto)
        return 0

    cid = slack_client.garantir_canal(args.canal)
    if not cid:
        print(f"nao consegui criar/abrir {args.canal}")
        return 1
    slack_client.definir_proposito(
        cid, "O que a equipe está fazendo, o que já está no ar e quanto "
             "tempo levou. Sem termo técnico.")
    if not slack_client.post_message(cid, texto):
        print("falha ao publicar")
        return 1
    print(f"atualização publicada em {args.canal}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
