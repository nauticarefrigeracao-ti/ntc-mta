"""Camada de confianca -- o sistema prova que esta certo, ou fica vermelho.

Motivacao (30/07/2026): tres coisas passaram como "ok" estando erradas no
mesmo dia -- o painel caiu em producao com um import que nunca foi commitado;
o fechamento contava o mesmo caso duas vezes e inflava o prejuizo do chefe em
45%; e um validador de tela deu "OK" fotografando uma tela de login. Em todos,
a AUSENCIA DE ERRO foi lida como sucesso.

Aqui cada invariante e uma afirmacao que tem que ser verdade sobre os dados.
Quebrou -> vira um Achado com evidencia numerica e uma acao concreta. E a
cobertura de conciliacao e uma CATRACA: pode subir, nunca cair.

Custo: tudo aqui e aritmetica sobre dados ja carregados -- roda em
milissegundos, no CI, a cada push. Sem chamada de modelo. Modelo (caro) so
entra quando ha achado.

Uso:
    python confianca.py            # roda a bateria contra o Neon e imprime
    python confianca.py --slack    # publica o placar no Slack
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Iterable, Optional

# Formato do order_id do ML -- MEDIDO na API em 30/07/2026, nao suposto:
#   10 digitos (5.099 casos) -> PEDIDO antigo legitimo  (6/6 abrem /orders/)
#   11 digitos (2.922 casos) -> SHIPMENT                (8/8 em /shipments/)
#   16 digitos (10.092, 2000…) -> pedido novo
#
# A primeira versao usava "< 15 digitos = shipment" e estava errada: acusava
# 5.099 pedidos validos. Pior, um shipment de 11 digitos resolve para um order
# de 10 -- entao a regra antiga acusaria o RESULTADO da propria correcao como
# defeito, e o CI ficaria vermelho para sempre depois de um conserto bem
# sucedido.
DIGITOS_SHIPMENT = 11
DIGITOS_ORDER_VALIDOS = (10, 16)
# Abaixo disto uma variacao de cobertura e ruido de arredondamento, nao queda.
TOLERANCIA_COBERTURA = 0.5


@dataclass(frozen=True)
class Achado:
    """Uma invariante quebrada. Sempre com evidencia e acao -- alerta sem as
    duas coisas vira ruido que ninguem trata."""
    invariante: str
    severidade: str  # "quebra" | "alerta"
    resumo: str
    evidencia: str
    acao: str


def _pct(v: float) -> str:
    return f"{v:.1f}".replace(".", ",")


# --- placar ----------------------------------------------------------------

def cobertura_conciliacao(total: int, conciliados: int) -> float:
    """% dos casos que viram R$ de verdade. Universo vazio = 100%: nao ha o
    que conciliar, e isso nao e falha."""
    if total <= 0:
        return 100.0
    return min(100.0, 100.0 * conciliados / total)


def checar_cobertura_nao_caiu(atual: float, anterior: Optional[float]) -> Optional[Achado]:
    """A catraca. Sem isto, uma mudanca pode reduzir silenciosamente quantos
    casos viram numero e ninguem percebe -- o painel so mostra menos."""
    if anterior is None:
        return None
    if atual >= anterior - TOLERANCIA_COBERTURA:
        return None
    return Achado(
        invariante="cobertura_conciliacao",
        severidade="quebra",
        resumo="A cobertura de conciliação caiu",
        evidencia=f"estava em {_pct(anterior)}%, agora está {_pct(atual)}%",
        acao=("Compare o último commit que tocou sync/conciliação. Menos casos "
              "viram R$ do que antes — o painel vai mostrar um número menor "
              "sem avisar que é por falta de dado."),
    )


# --- invariantes de dado ---------------------------------------------------

def checar_order_ids_reais(order_ids: Iterable) -> Optional[Achado]:
    """R1 do chefe: todo link tem que abrir a venda certa.

    Um shipment gravado no lugar do pedido da 404 para a Maria e nunca casa
    com meli_page_saldos (chaveado por order_id). Ver DIGITOS_SHIPMENT para a
    medicao que define o formato -- pedido antigo (10) e novo (16) sao ambos
    validos e NAO podem ser acusados."""
    suspeitos = [o for o in order_ids
                 if o is not None
                 and len(str(o).strip()) not in DIGITOS_ORDER_VALIDOS]
    if not suspeitos:
        return None
    shipments = [o for o in suspeitos
                 if len(str(o).strip()) == DIGITOS_SHIPMENT]
    if shipments:
        return Achado(
            invariante="order_id_real",
            severidade="quebra",
            resumo="Há links apontando para shipment, não para o pedido",
            evidencia=f"{len(shipments)} caso(s) com shipment gravado como "
                      f"order_id (ex.: {shipments[0]})",
            acao=("Rode `python resolver_order.py` para converter "
                  "shipment→order. Esses casos dão 404 para a Maria e ficam "
                  "fora do saldo."),
        )
    return Achado(
        invariante="order_id_real",
        severidade="quebra",
        resumo="Há order_id em formato desconhecido",
        evidencia=f"{len(suspeitos)} caso(s) fora dos formatos conhecidos "
                  f"(ex.: {suspeitos[0]})",
        acao=("Nem pedido (10 ou 16 dígitos) nem shipment (11). Verifique a "
              "origem antes de confiar no link."),
    )


def checar_duplicatas(identidades: Iterable) -> Optional[Achado]:
    """Um caso contado duas vezes infla o numero do chefe. Foi o que
    aconteceu no fechamento (JOIN com slack_notificados)."""
    itens = list(identidades)
    distintos = len(set(itens))
    sobrando = len(itens) - distintos
    if sobrando <= 0:
        return None
    return Achado(
        invariante="sem_duplicata",
        severidade="quebra",
        resumo="O mesmo caso está sendo contado mais de uma vez",
        evidencia=f"{len(itens)} linhas para {distintos} casos distintos "
                  f"({sobrando} sobrando)",
        acao=("Deduplique por claim_id antes de somar. Número inflado é pior "
              "que número faltando: ninguém desconfia de um número."),
    )


def checar_soma_categorias(total: int, categorias: dict) -> Optional[Achado]:
    """Card na tela que nao fecha com a tabela e um numero que mente."""
    soma = sum(categorias.values())
    if soma == total:
        return None
    return Achado(
        invariante="soma_categorias",
        severidade="quebra",
        resumo="As categorias não somam o total exibido",
        evidencia=f"total={total}, soma das categorias={soma} "
                  f"({categorias})",
        acao=("Há caso fora de todas as categorias ou contado em duas. "
              "Quem lê a tela não tem como perceber."),
    )


# --- consolidacao ----------------------------------------------------------

def severidade_geral(achados: list[Achado]) -> str:
    if any(a.severidade == "quebra" for a in achados):
        return "quebra"
    if achados:
        return "alerta"
    return "ok"


def formatar_placar(cobertura: float, achados: list[Achado]) -> str:
    linhas = [f"Confiança dos números — conciliação em {_pct(cobertura)}%"]
    if not achados:
        linhas.append("Nenhuma invariante quebrada: os números estão limpos.")
        return "\n".join(linhas)
    linhas.append(f"{len(achados)} invariante(s) quebrada(s):")
    for a in achados:
        linhas.append(f"• {a.resumo} — {a.evidencia}")
        linhas.append(f"  → {a.acao}")
    return "\n".join(linhas)


# --- I/O: roda a bateria contra o banco ------------------------------------

def rodar_bateria() -> tuple[float, list[Achado]]:
    """Le o estado real e devolve (cobertura, achados). Sem I/O nas funcoes
    acima -- elas sao puras e testadas isoladamente."""
    from src.db.connection import get_db_connection

    conn = get_db_connection()
    achados: list[Achado] = []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS confianca_placar (
                    medido_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    cobertura NUMERIC(5,2) NOT NULL
                )
            """)
            conn.commit()

            # cobertura: fechados com order VALIDO (antigo ou novo) que ja
            # viraram R$. Antes o filtro era ">= 15 digitos", que jogava fora
            # os 5.099 pedidos antigos de 10 digitos -- a cobertura era medida
            # sobre um universo menor do que o real.
            cur.execute("""
                SELECT COUNT(*), COUNT(s.order_id)
                FROM ml_devolucoes d
                LEFT JOIN meli_page_saldos s ON s.order_id = d.order_id
                WHERE d.claim_status = 'closed'
                  AND LENGTH(d.order_id::text) = ANY(%s)
            """, (list(DIGITOS_ORDER_VALIDOS),))
            total, conciliados = cur.fetchone()
            cobertura = cobertura_conciliacao(total or 0, conciliados or 0)

            cur.execute("SELECT cobertura FROM confianca_placar "
                        "ORDER BY medido_em DESC LIMIT 1")
            linha = cur.fetchone()
            anterior = float(linha[0]) if linha else None

            # invariantes
            cur.execute("SELECT order_id FROM ml_devolucoes WHERE order_id IS NOT NULL")
            ids = [r[0] for r in cur.fetchall()]

            # Mede o que ENTRA NA CONTA do fechamento, com o mesmo DISTINCT ON
            # do codigo de producao. Ler a tabela crua daria falso-positivo:
            # slack_notificados tem PK (claim_id, status), entao varias linhas
            # por claim sao legitimas. Invariante que grita sem motivo vira
            # ruido e as pessoas param de olhar.
            cur.execute("""
                SELECT DISTINCT ON (sn.claim_id) sn.claim_id
                FROM slack_notificados sn
                JOIN ml_devolucoes d ON d.claim_id = sn.claim_id
                WHERE sn.status LIKE 'closed:%%'
                ORDER BY sn.claim_id, sn.avisado_em DESC
            """)
            claims_fechamento = [r[0] for r in cur.fetchall()]

            for a in (checar_cobertura_nao_caiu(cobertura, anterior),
                      checar_order_ids_reais(ids),
                      checar_duplicatas(claims_fechamento)):
                if a:
                    achados.append(a)

            cur.execute("INSERT INTO confianca_placar (cobertura) VALUES (%s)",
                        (round(cobertura, 2),))
            conn.commit()
        return cobertura, achados
    finally:
        conn.close()


def main() -> int:
    publicar = "--slack" in sys.argv
    cobertura, achados = rodar_bateria()
    placar = formatar_placar(cobertura, achados)
    print(placar)

    if publicar:
        import slack_notify
        import slack_client
        canal = slack_notify.CANAL_FECHAMENTO
        destino = slack_client.garantir_canal(canal) or canal
        if not slack_client.post_message(destino, placar):
            print("confianca: FALHOU ao publicar no Slack", file=sys.stderr)
            return 1

    return 1 if severidade_geral(achados) == "quebra" else 0


if __name__ == "__main__":
    sys.exit(main())
