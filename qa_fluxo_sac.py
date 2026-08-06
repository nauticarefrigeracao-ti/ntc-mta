"""QA do fluxo do SAC contra o Slack de verdade -- os 12 caminhos, um a um.

Os testes de `test_fluxo_exaustivo.py` varrem o grafo em memoria. Este script
faz o resto: percorre **cada um dos 12 caminhos** clicando de verdade, e a
cada clique volta ao Slack para LER a mensagem e conferir que a tela mudou
como devia. Regra da casa: nada e considerado validado ate ser visto na
interface com que a pessoa interage.

O que ele confere em cada degrau:

    banco   a marcacao existe, com etapa, quem e o canal certo
    estado  o degrau reconstruido bate com o esperado
    tela    o card no Slack mostra o rotulo do estado novo
    botoes  os botoes na tela sao exatamente os do degrau novo
    tempo   a marcacao aparece na linha do tempo, com hora

E no fim confere a coisa mais importante: **o cofrinho continua zerado**.
Doze casos fechados em canal de ensaio nao podem ter virado dinheiro.

SEGURANCA: recusa rodar no canal oficial. O ensaio marca e desmarca casos
reais dezenas de vezes; no #sac isso apareceria na tela da Maria.

Uso:
    python qa_fluxo_sac.py                 # roda tudo e limpa no fim
    python qa_fluxo_sac.py --manter        # deixa as marcacoes para inspecao
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional

sys.path.insert(0, str(Path(__file__).parent))

import card_maria
import cofrinho
import sac_fluxo
import slack_client
import socket_sac

CANAL_PADRAO = "#sac-teste"
QUEM = "qa-fluxo"

# Pausa entre cliques. chat.update e Tier 3 (50/min) e o ensaio faz ~120
# chamadas; sem folga a bateria morreria em 429 no meio e o relatorio diria
# "falhou" onde o software esta certo.
PAUSA = 0.6


_conexao = None


def conexao():
    """Uma conexao viva com o Neon -- reconectando quando ele derruba.

    O Neon fecha conexao ociosa, e a varredura dos 12 caminhos leva ~10
    minutos com pausa entre cliques. Na primeira rodada o QA morreu no
    terceiro caminho com "SSL connection has been closed unexpectedly",
    depois de dois caminhos VERDES -- ou seja, o software estava certo e
    quem caiu foi o ensaio. Um teste que morre no meio nao vale como prova.
    """
    global _conexao
    from src.db.connection import get_db_connection

    try:
        if _conexao is not None:
            with _conexao.cursor() as c:
                c.execute("SELECT 1")
            return _conexao
    except Exception:
        try:
            _conexao.close()
        except Exception:
            pass
        _conexao = None
    _conexao = get_db_connection()
    return _conexao


def caminhos() -> list[list[str]]:
    """Os 12 percursos completos, derivados do proprio fluxo."""
    def andar(estado, feito):
        saidas = [a["id"] for a in sac_fluxo.acoes_de(estado)
                  if a["id"] not in sac_fluxo.NEUTRAS]
        if not saidas:
            return [list(feito)]
        out = []
        for acao in sorted(saidas):
            out += andar(sac_fluxo.aplicar(estado, acao), feito + [acao])
        return out
    return andar(sac_fluxo.ESTADO_INICIAL, [])


def rotulos_dos_botoes(blocks: Optional[list]) -> list[str]:
    """Os botoes que estao na tela agora."""
    for b in blocks or []:
        if b.get("type") == "actions":
            return [e.get("text", {}).get("text", "")
                    for e in b.get("elements", [])]
    return []


def sem_emoji(texto: str) -> str:
    """O texto sem emoji, para comparar o que importa: as palavras.

    O Slack guarda `📦` e devolve `:package:` no `conversations.history`. A
    tela mostra o emoji -- confirmado em print --, mas a API mostra o codigo.
    Comparar os dois ao pe da letra acusava divergencia em TODOS os degraus,
    onde nao havia nenhuma: o verificador e que estava errado.
    """
    t = re.sub(r":[a-z0-9_+\-]+:", " ", texto or "")
    t = "".join(c for c in t if ord(c) < 0x2000 or c.isalnum())
    return " ".join(t.split())


def _texto_de(no: Any) -> str:
    """O texto de um nó de bloco.

    O Slack usa a mesma chave `text` para duas formas: string direta em
    `context`, e objeto aninhado em `button`. Tratar as duas como string
    derrubava a leitura no primeiro card com botão.
    """
    if isinstance(no, str):
        return no
    if isinstance(no, Mapping):
        return _texto_de(no.get("text"))
    return ""


def texto_da_mensagem(blocks: Optional[list]) -> str:
    partes = []
    for b in blocks or []:
        t = _texto_de(b.get("text"))
        if not t and b.get("elements"):
            t = "\n".join(_texto_de(e) for e in b["elements"])
        partes.append(t)
    return "\n".join(partes)


def ler_mensagem(channel_id: str, ts: str) -> Optional[dict]:
    """Le do Slack a mensagem exata, como ela esta na tela agora."""
    r = slack_client._api("conversations.history", {
        "channel": channel_id, "latest": ts, "oldest": ts,
        "inclusive": "true", "limit": 1}, get=True)
    msgs = (r or {}).get("messages") or []
    return msgs[0] if msgs else None


def limpar_ensaio(claim_id: int, channel_id: str) -> int:
    """Apaga as marcacoes DESTE canal. Nunca toca no canal oficial."""
    conn = conexao()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sac_timeline "
                    "WHERE claim_id = %s AND channel_id = %s",
                    (claim_id, channel_id))
        n = cur.rowcount
    conn.commit()
    return n


def _evento(claim_id: int, acao: str, channel: str, ts: str,
            observacao: Optional[str] = None) -> dict:
    """O mesmo formato que `socket_sac.interpretar` produz de um clique real.

    O transporte WebSocket ja foi validado com clique humano; o que este
    ensaio exercita e tudo o que vem depois dele.
    """
    return {"envelope_id": f"qa-{claim_id}-{acao}", "acao": acao,
            "claim_id": claim_id, "quem": QUEM, "channel": channel,
            "ts": ts, "trigger_id": None, "observacao": observacao}


def conferir_degrau(claim_id: int, channel: str, ts: str,
                    acao: str, esperado: str) -> list[str]:
    """Confere banco, estado, tela, botoes e linha do tempo. Devolve falhas."""
    falhas: list[str] = []

    timeline = card_maria.buscar_timelines(
        conexao(), [claim_id], channel).get(claim_id, [])
    if not any(e["etapa"] == acao for e in timeline):
        falhas.append(f"banco: marcação '{acao}' não foi gravada")
    if any(e.get("channel_id") != channel for e in timeline):
        falhas.append("banco: marcação gravada em canal errado")

    estado = sac_fluxo.estado_de(timeline)
    if estado != esperado:
        falhas.append(f"estado: esperado '{esperado}', reconstruído '{estado}'")

    msg = ler_mensagem(channel, ts)
    if not msg:
        falhas.append("tela: não consegui reler a mensagem no Slack")
        return falhas

    texto = texto_da_mensagem(msg.get("blocks"))
    texto_cru = sem_emoji(texto)
    rotulo = sac_fluxo.rotulo_do_estado(esperado)
    if sem_emoji(rotulo) not in texto_cru:
        falhas.append(f"tela: card não mostra '{rotulo}'")

    esperados = [sem_emoji(a["rotulo"]) for a in sac_fluxo.acoes_de(esperado)]
    na_tela = [sem_emoji(r) for r in rotulos_dos_botoes(msg.get("blocks"))]
    if na_tela != esperados:
        falhas.append(f"botões: tela {na_tela} ≠ esperado {esperados}")

    marca = sac_fluxo._ETAPAS.get(acao, acao)
    if sem_emoji(marca) not in texto_cru:
        falhas.append(f"linha do tempo: '{marca}' não aparece no card")

    return falhas


def rodar_caminho(claim_id: int, channel: str, ts: str,
                  caminho: list[str]) -> dict:
    """Percorre um caminho inteiro, conferindo degrau por degrau."""
    limpar_ensaio(claim_id, channel)
    card_maria.registrar  # noqa: B018  (documenta a dependencia)

    estado = sac_fluxo.ESTADO_INICIAL
    falhas: list[str] = []
    for acao in caminho:
        estado = sac_fluxo.aplicar(estado, acao)
        obs = "ensaio automático" if acao == "observacao" else None
        socket_sac.aplicar_clique(_evento(claim_id, acao, channel, ts, obs))
        time.sleep(PAUSA)
        for f in conferir_degrau(claim_id, channel, ts, acao, estado):
            falhas.append(f"[{acao}] {f}")

    # Um clique a mais no fim: caso finalizado nao aceita nada.
    socket_sac.aplicar_clique(_evento(claim_id, "recebi", channel, ts))
    time.sleep(PAUSA)
    depois = sac_fluxo.estado_de(card_maria.buscar_timelines(
        conexao(), [claim_id], channel).get(claim_id, []))
    if depois != "finalizado":
        falhas.append(f"[extra] clique em caso finalizado mudou o estado "
                      f"para '{depois}'")

    return {"caminho": " → ".join(caminho), "claim_id": claim_id,
            "falhas": falhas}


def rodar_caos(claim_id: int, channel: str, ts: str) -> dict:
    """O que os 12 caminhos NAO cobrem.

    `observacao` e `supervisor` nao aparecem nos percursos porque nao movem o
    caso -- e era justamente por isso que estavam sem prova ponta a ponta.
    Aqui eles sao clicados de verdade, junto do clique fora de ordem.
    """
    limpar_ensaio(claim_id, channel)
    falhas: list[str] = []

    def estado_agora():
        return sac_fluxo.estado_de(card_maria.buscar_timelines(
            conexao(), [claim_id], channel).get(claim_id, []))

    socket_sac.aplicar_clique(_evento(claim_id, "recebi", channel, ts))
    time.sleep(PAUSA)

    texto = "cliente ligou dizendo que a peca chegou quebrada"
    socket_sac.aplicar_clique(
        _evento(claim_id, "observacao", channel, ts, texto))
    time.sleep(PAUSA)
    if estado_agora() != "recebido":
        falhas.append("observacao moveu o caso de degrau")
    msg = ler_mensagem(channel, ts)
    if texto not in texto_da_mensagem((msg or {}).get("blocks")):
        falhas.append("observacao nao apareceu na linha do tempo do card")

    socket_sac.aplicar_clique(_evento(claim_id, "supervisor", channel, ts))
    time.sleep(PAUSA)
    if estado_agora() != "recebido":
        falhas.append("encaminhar ao supervisor moveu o caso de degrau")

    # Clique fora de ordem: card velho de outra aba, ou dedo escorregando.
    antes = estado_agora()
    socket_sac.aplicar_clique(_evento(claim_id, "reembolsado", channel, ts))
    time.sleep(PAUSA)
    if estado_agora() != antes:
        falhas.append("clique fora de ordem avancou o caso")

    socket_sac.aplicar_clique(_evento(claim_id, "estoque", channel, ts))
    time.sleep(PAUSA)
    if estado_agora() != "no_estoque":
        falhas.append("o caso nao seguiu depois do clique recusado")

    return {"caminho": "caos: observacao + supervisor + clique fora de ordem",
            "claim_id": claim_id, "falhas": falhas}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canal", default=CANAL_PADRAO)
    ap.add_argument("--manter", action="store_true",
                    help="não limpa as marcações no fim")
    args = ap.parse_args()

    if args.canal == card_maria.CANAL:
        print(f"recuso rodar em {args.canal}: o ensaio marca e desmarca casos "
              f"reais dezenas de vezes, e isso apareceria na tela da Maria.",
              file=sys.stderr)
        return 1

    from src.db.connection import get_db_connection

    conn = conexao()
    cid = slack_client.garantir_canal(args.canal)
    if not cid:
        print(f"não consegui abrir {args.canal}", file=sys.stderr)
        return 1

    cur = conexao().cursor()
    cur.execute("SELECT claim_id, ts FROM sac_cards WHERE channel_id = %s "
                "ORDER BY claim_id", (cid,))
    cards = cur.fetchall()
    if not cards:
        print(f"nenhum card em {args.canal} — rode "
              f"`python card_maria.py --publicar --canal {args.canal}` antes.",
              file=sys.stderr)
        return 1

    todos = caminhos()
    print(f"QA do fluxo em {args.canal} — {len(todos)} caminhos, "
          f"{len(cards)} card(s) disponíveis\n")

    relatorios = []
    for i, caminho in enumerate(todos):
        claim_id, ts = cards[i % len(cards)]
        r = rodar_caminho(claim_id, cid, ts, caminho)
        relatorios.append(r)
        marca = "OK  " if not r["falhas"] else "FALHA"
        print(f"  {marca} {r['caminho']}")
        for f in r["falhas"]:
            print(f"         {f}")

    rc = rodar_caos(cards[0][0], cid, cards[0][1])
    relatorios.append(rc)
    print()
    print(f"  {'OK  ' if not rc['falhas'] else 'FALHA'} {rc['caminho']}")
    for f in rc["falhas"]:
        print(f"         {f}")

    # O teste que importa mais: doze casos fechados em canal de ensaio nao
    # podem ter virado dinheiro no placar de quem opera.
    oficial = slack_client.garantir_canal(card_maria.CANAL)
    hoje = datetime.now(cofrinho.BRT).date()
    placar = cofrinho.acumular(
        cofrinho.carregar(conexao(), hoje.year, hoje.month),
        hoje.year, hoje.month, canal_oficial=oficial)
    vazou = placar["n_positivo"] or placar["n_negativo"]
    print(f"\n  {'FALHA' if vazou else 'OK  '} cofrinho oficial intacto: "
          f"{placar['n_positivo']} positivo(s), {placar['n_negativo']} "
          f"negativo(s)")

    if not args.manter:
        apagadas = sum(limpar_ensaio(c, cid) for c, _ in cards)
        card_maria.publicar(canal=args.canal)
        print(f"  limpeza: {apagadas} marcação(ões) de ensaio apagadas, "
              f"cards restaurados")

    com_falha = [r for r in relatorios if r["falhas"]]
    print(f"\n{len(relatorios) - len(com_falha)}/{len(relatorios)} caminhos "
          f"sem divergência" + ("" if not vazou else " · COFRINHO VAZOU"))
    # Job que nao fez o trabalho sai com codigo 1.
    return 1 if (com_falha or vazou) else 0


if __name__ == "__main__":
    sys.exit(main())
