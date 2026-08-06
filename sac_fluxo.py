"""O fluxo do caso de SAC -- a escada que a Thayna desenhou em 06/08/2026.

    (1) recebido -> (2) estoque | garantia
                 -> (3) mediacao | whatsapp | sem argumento
                 -> (4) reembolsado | recusado
                 -> (5) finalizar

Tres decisoes que este modulo toma, e por que:

**Maquina de estados, nao cinco botoes sempre visiveis.** O card do pos-venda
do Meli mostra a Maria SO o que ela pode fazer agora. Botao que nao faz
sentido no estado atual e convite a erro -- e erro aqui e dinheiro marcado
como reembolsado que nao foi reembolsado.

**O estado sai da timeline, nao de uma coluna.** A Thayna pediu data e hora em
cada marcacao. Numa coluna sobrescrita, a hora de cada passo se perde -- e e
ela que responde "por que esse caso demorou 9 dias?".

**Acao invalida levanta.** `aplicar("a_caminho", "reembolsado")` nao passa
calado: viraria numero errado no cofrinho e no balanco do mes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

ESTADO_INICIAL = "a_caminho"

# O relogio da Maria. O banco grava TIMESTAMPTZ e devolve UTC: no primeiro
# clique real, 15:02 de Praia Grande virou "18:02" no card. Nao e cosmetico
# -- perto da meia-noite, 21h30 vira 00h30 do dia seguinte e a marcacao muda
# de DIA. O Brasil nao tem horario de verao desde 2019, entao o offset fixo e
# exato e nao depende do tzdata da maquina.
BRT = timezone(timedelta(hours=-3))

# Nao movem o caso de degrau -- anotar nao e decidir, e pedir ajuda tambem
# nao. Se avancassem, a Maria perderia o degrau so por escrever um bilhete.
NEUTRAS = ("observacao", "supervisor")

# (destino, rotulo do botao, estilo) por acao, agrupados por degrau.
_ESCADA: dict[str, list[tuple[str, str, str, Optional[str]]]] = {
    "a_caminho": [
        ("recebi", "recebido", "📦 Recebi o produto", "primary"),
    ],
    "recebido": [
        ("estoque", "no_estoque", "📦 Estoque", None),
        ("garantia", "em_garantia", "🔧 Garantia", None),
    ],
    "no_estoque": [
        ("mediacao", "mediacao", "⚖️ Mediação", None),
        ("whatsapp", "whatsapp", "💬 WhatsApp", None),
        ("sem_argumento", "sem_argumento", "🚫 Sem argumento", None),
    ],
    "em_garantia": [
        ("mediacao", "mediacao", "⚖️ Mediação", None),
        ("whatsapp", "whatsapp", "💬 WhatsApp", None),
        ("sem_argumento", "sem_argumento", "🚫 Sem argumento", None),
    ],
    "mediacao": [
        ("reembolsado", "reembolsado", "💸 Reembolsado", None),
        ("recusado", "recusado", "❌ Recusado", "danger"),
    ],
    "whatsapp": [
        ("reembolsado", "reembolsado", "💸 Reembolsado", None),
        ("recusado", "recusado", "❌ Recusado", "danger"),
    ],
    "sem_argumento": [
        ("reembolsado", "reembolsado", "💸 Reembolsado", None),
        ("recusado", "recusado", "❌ Recusado", "danger"),
    ],
    "reembolsado": [
        ("finalizar", "finalizado", "✅ Finalizar", "primary"),
    ],
    "recusado": [
        ("finalizar", "finalizado", "✅ Finalizar", "primary"),
    ],
    "finalizado": [],
}

_ROTULOS = {
    "a_caminho": "A caminho",
    "recebido": "Recebido",
    "no_estoque": "No estoque",
    "em_garantia": "Em garantia",
    "mediacao": "Em mediação",
    "whatsapp": "Falando no WhatsApp",
    "sem_argumento": "Sem argumento",
    "reembolsado": "Reembolsado",
    "recusado": "Recusado",
    "finalizado": "Finalizado",
}

# Como cada marcacao aparece na linha do tempo do card.
_ETAPAS = {
    "recebi": "📦 Recebi o produto",
    "estoque": "📦 Foi para o estoque",
    "garantia": "🔧 Foi para a garantia",
    "mediacao": "⚖️ Abriu mediação",
    "whatsapp": "💬 Falou no WhatsApp",
    "sem_argumento": "🚫 Sem argumento",
    "reembolsado": "💸 Reembolsado",
    "recusado": "❌ Recusado",
    "finalizar": "✅ Finalizado",
    "observacao": "📝 Observação",
    "supervisor": "🆙 Encaminhado ao supervisor",
}

# Desfecho -> cofrinho. Reembolsado e dinheiro que saiu; recusado e venda que
# ficou de pe. O que nao fechou nao entra: contar mediacao aberta como ganho
# ou perda infla o numero que vai para o Gabriel.
_COFRINHO = {"reembolsado": "negativo", "recusado": "positivo"}


# Superficie publica do grafo -- e o que permite varrer TODOS os caminhos e
# TODAS as combinacoes estado x acao sem um teste ter que adivinhar a lista.
ESTADOS = tuple(_ESCADA)
ACOES = tuple(sorted(
    {nome for degraus in _ESCADA.values() for nome, _d, _r, _e in degraus}
    | set(NEUTRAS)))


def eh_terminal(estado: str) -> bool:
    return estado == "finalizado"


def rotulo_do_estado(estado: str) -> str:
    return _ROTULOS.get(estado, str(estado).replace("_", " "))


def aplicar(estado: str, acao: str) -> str:
    """O degrau seguinte. Levanta se a acao nao existe neste degrau.

    Falhar alto e o ponto: um clique fora de ordem que passa calado vira
    dinheiro errado no balanco, e ninguem descobre ate a conferencia manual.
    """
    if acao in NEUTRAS:
        if estado not in _ESCADA:
            raise ValueError(f"estado desconhecido: {estado!r}")
        return estado
    for nome, destino, _rot, _est in _ESCADA.get(estado, []):
        if nome == acao:
            return destino
    raise ValueError(
        f"'{acao}' não é uma ação possível em '{rotulo_do_estado(estado)}'. "
        f"Disponíveis: {[a['id'] for a in acoes_de(estado)] or 'nenhuma'}"
    )


def acoes_de(estado: str) -> list[dict]:
    """Os botoes que aparecem neste degrau -- e so eles.

    Caso finalizado nao oferece nada: reabrir e decisao de supervisor, nao um
    clique a mais na mesma tela.
    """
    if eh_terminal(estado):
        return []
    acoes = [{"id": nome, "rotulo": rot, "estilo": est}
             for nome, _d, rot, est in _ESCADA.get(estado, [])]
    acoes.append({"id": "observacao", "rotulo": "📝 Observação", "estilo": None})
    acoes.append({"id": "supervisor", "rotulo": "🆙 Supervisor", "estilo": None})
    return acoes


def estado_de(timeline: Iterable[Mapping[str, Any]]) -> str:
    """O degrau atual, reconstruido evento a evento.

    Evento impossivel (que chegou por bug ou clique duplo em corrida) e
    IGNORADO, nao aplicado: o estado para no ultimo degrau valido em vez de
    pular. Perder um passo e ruim; inventar um e pior.
    """
    estado = ESTADO_INICIAL
    for ev in timeline or []:
        try:
            estado = aplicar(estado, str(ev.get("etapa") or ""))
        except ValueError:
            continue
    return estado


def cofrinho(timeline: Iterable[Mapping[str, Any]]) -> Optional[str]:
    """"positivo" · "negativo" · None (ainda em aberto).

    Le o DESFECHO na timeline, nao o estado final: depois de "finalizar" o
    estado e "finalizado" para os dois lados, e o sinal se perderia.
    """
    for ev in reversed(list(timeline or [])):
        sinal = _COFRINHO.get(str(ev.get("etapa") or ""))
        if sinal:
            return sinal
    return None


def _quando(texto: Any) -> Optional[datetime]:
    """O carimbo no fuso de quem clicou.

    Com fuso, converte para BRT. Sem fuso, ja e hora local -- converter de
    novo empurraria tres horas para frente.
    """
    if isinstance(texto, datetime):
        d = texto
    else:
        if not texto:
            return None
        try:
            d = datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    return d.astimezone(BRT) if d.tzinfo else d


def linha_da_timeline(evento: Mapping[str, Any]) -> str:
    """Uma marcacao, como a Maria le no card: o que, quando, quem.

    A Thayna pediu data e hora em cada marcacao com todas as letras -- e sem
    "quem", a pergunta "quem marcou isso?" nao tem resposta.
    """
    etapa = str(evento.get("etapa") or "")
    rot = _ETAPAS.get(etapa, etapa.replace("_", " ").capitalize())

    d = _quando(evento.get("quando"))
    carimbo = f"{d:%d/%m %H:%M}" if d else "sem data"

    partes = [rot, carimbo]
    quem = evento.get("quem")
    if quem:
        partes.append(str(quem))
    linha = " · ".join(partes)

    obs = evento.get("observacao")
    if obs:
        linha += f"\n        _{obs}_"
    return linha
