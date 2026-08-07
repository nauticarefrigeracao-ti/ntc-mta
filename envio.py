"""A viagem do pacote -- o que o Mercado Livre mostra e o card nao mostrava.

Comparacao das duas telas em 07/08/2026. No Meli:

    Em preparacao   3 ago 14:51
    A caminho       4 ago 14:36 · 4 ago 18:29 · 5 ago 01:34 · 6 ago 15:21 …
    Entregue        7 ago 10:27

No card do Slack: "Etiqueta gerada", e mais nada. Uma linha contra uma
historia.

O QUE A API ENTREGA -- medido sondando 12 endpoints em 07/08/2026

    /shipments/{id}/history          as etapas, com data e hora
    /shipments/{id}/status_history   as mesmas, com HORAS UTEIS em cada uma
    /shipments/{id}/delays           o atraso que o proprio ML declara
    /shipments/{id}/carrier          transportadora e link de rastreio
    /shipments/{id}/lead_time        previsao de entrega (ja usavamos)
    /shipments/{id}/costs            frete cobrado

O QUE ELA NAO ENTREGA

    Os eventos granulares da transportadora ("Saiu do centro de distribuicao
    de Guarulhos, 4 ago 14:36"). A tela do Meli mostra; nenhum dos 12
    endpoints devolve. Fica registrado como "nao encontrado no que sondei",
    nao como "nao existe" -- a diferenca importa para quem procurar de novo.

Entao o card mostra a viagem em ETAPAS, nao o rastro entre cidades. E menos
que a tela do Meli, e dizer isso e melhor do que fingir paridade.

DOIS ATRASOS QUE NAO SAO A MESMA COISA

    handling_delayed    o comprador ainda nao postou
    shipping_delayed    a transportadora estourou o prazo

Tratar os dois como "atrasado" manda a Maria cobrar quem nao deve.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

# O relogio da Maria. O ML devolve -04:00 nestes campos; lido cru, a hora
# aparece uma hora adiantada na tela dela.
BRT = timezone(timedelta(hours=-3))

# Quantas etapas cabem no card sem esconder o que ela precisa decidir.
MAX_ETAPAS = 4

_ROTULOS = {
    ("handling", None): "🏭 Em preparação",
    ("ready_to_ship", "ready_to_print"): "🏷️ Etiqueta pronta",
    ("ready_to_ship", "printed"): "🏷️ Etiqueta impressa pelo comprador",
    ("ready_to_ship", None): "🏷️ Pronto para postar",
    ("shipped", None): "🚚 Despachado",
    ("delivered", None): "📬 Entregue",
    ("not_delivered", None): "↩️ Não entregue",
    ("returned", None): "↩️ Devolvido ao remetente",
    ("cancelled", None): "🚫 Cancelado",
    ("pending", None): "⏳ Aguardando",
}

_ATRASOS = {
    "handling_delayed":
        "o comprador ainda não postou o pacote no prazo que o Mercado Livre "
        "deu a ele",
    "shipping_delayed_original_promise":
        "a transportadora passou do prazo de entrega prometido",
    "shipping_delayed":
        "a transportadora passou do prazo de entrega prometido",
}


def _instante(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        d = v
    else:
        if not v:
            return None
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    return d.astimezone(BRT) if d.tzinfo else d.replace(tzinfo=BRT)


def rotulo_da_etapa(status: Optional[str], substatus: Optional[str]) -> str:
    """O que aconteceu, em portugues. A Maria nao le `ready_to_ship`.

    O substatus refina de proposito: `ready_to_print` e `printed` sao
    momentos diferentes -- num a etiqueta ainda nao saiu, no outro o
    comprador ja a tem na mao e o pacote pode ser postado a qualquer hora.
    """
    s = str(status or "")
    sub = str(substatus) if substatus else None
    return (_ROTULOS.get((s, sub))
            or _ROTULOS.get((s, None))
            or f"• {s.replace('_', ' ')}".rstrip())


def etapas_do_envio(historico: Optional[Iterable[Mapping[str, Any]]]) -> list[dict]:
    """As etapas da viagem, em ordem, com data e hora daqui.

    Linha com data quebrada e PULADA, nao fatal: uma linha estranha nao pode
    apagar a viagem inteira.
    """
    saida = []
    for e in historico or []:
        quando = _instante(e.get("date"))
        if quando is None:
            continue
        saida.append({
            "quando": quando,
            "status": e.get("status"),
            "rotulo": rotulo_da_etapa(e.get("status"), e.get("substatus")),
        })
    return sorted(saida, key=lambda x: x["quando"])


def parado_ha(etapas: list, agora: Optional[datetime] = None) -> Optional[timedelta]:
    """Ha quanto tempo nada acontece com este pacote.

    E o que revela caso travado: cinco dias entre "etiqueta impressa" e hoje
    nao aparece em lugar nenhum hoje, e e exatamente o caso que ninguem
    olhou.
    """
    if not etapas:
        return None
    agora = agora or datetime.now(BRT)
    # Relogio atrasado nao pode virar tempo negativo no relatorio.
    return max(timedelta(0), agora - etapas[-1]["quando"])


def atraso_declarado(
        atrasos: Optional[Iterable[Mapping[str, Any]]]) -> Optional[dict]:
    """O atraso que o PROPRIO Mercado Livre reconhece -- o mais recente.

    Vale mais que o nosso calculo: e o que a plataforma vai considerar se a
    conversa virar disputa.
    """
    validos = []
    for a in atrasos or []:
        quando = _instante(a.get("date"))
        if quando is None:
            continue
        tipo = str(a.get("type") or "")
        validos.append({"quando": quando, "tipo": tipo,
                        "texto": _ATRASOS.get(tipo,
                                              f"o Mercado Livre marcou "
                                              f"{tipo.replace('_', ' ')}")})
    if not validos:
        return None
    return max(validos, key=lambda x: x["quando"])


def linha_do_envio(etapas: list, maximo: int = MAX_ETAPAS) -> str:
    """A viagem, como aparece no card.

    Corta as mais ANTIGAS, nunca as recentes: o que importa e onde o pacote
    esta agora, nao de onde ele saiu.
    """
    if not etapas:
        return ""
    recentes = etapas[-maximo:]
    omitidas = len(etapas) - len(recentes)
    linhas = [f"{e['quando']:%d/%m %H:%M} · {e['rotulo']}" for e in recentes]
    if omitidas:
        linhas.insert(0, f"_(+{omitidas} etapa"
                         f"{'s' if omitidas != 1 else ''} anterior"
                         f"{'es' if omitidas != 1 else ''})_")
    return "\n".join(linhas)
