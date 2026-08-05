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

# Casos que NAO TEM COMO consertar -- verificado na API em 31/07/2026, um por
# um. Sem esta lista a invariante ficaria vermelha para sempre por causa de
# dado que o proprio ML ja nao tem, e invariante que grita sem acao possivel
# vira ruido que ninguem trata (a licao ja custou caro duas vezes).
# Entrar aqui exige evidencia; sair, so quando o ML voltar a responder.
IRRECUPERAVEIS: dict[int, str] = {
    40627136344: "claim 5074537287, de 2021 — /shipments existe mas não "
                 "referencia pedido; /orders dá 404",
    89507585096: "claim 5308227997, de 2024 — shipment e order sumiram "
                 "da API do ML",
}
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


# A coleta de saldos e RPA com sessao de navegador logada: nao roda no CI.
# Depois disto, o numero do mes comeca a nascer parcial sem ninguem perceber.
DIAS_COLETA_ACEITAVEL = 7

# `orders` e alimentada pelo CI a cada 2h. Dois dias sem venda nova ja e
# anormal -- e o custo de descobrir tarde e a cadeia inteira, nao uma tabela.
DIAS_ORDERS_ACEITAVEL = 2


def checar_orders_frescos(dias_desde_ultima_venda: Optional[int]
                          ) -> Optional[Achado]:
    """A tabela-raiz da fila esta viva?

    De 23/07 a 04/08/2026 -- 13 dias -- `orders` ficou congelada. O
    `ml_live_poll.py` rodava na maquina do Lucas e morreu; nenhum workflow do
    GitHub tocava `orders` (o sync cuidava de claims e CMV). Nada ficou
    vermelho, entao ninguem percebeu.

    A cadeia desceu junto, em silencio: 47 de 86 devolucoes de 24/07+ (55%)
    sem pedido em `orders`; o coletor de saldos e o motor de estimativa partem
    `FROM orders` e nunca enfileiraram os novos; sem saldo, o Slack rotulava
    "conciliacao financeira pendente" enquanto a pagina do Mercado Livre ja
    mostrava a venda fechada com o valor -- na cara do chefe.

    `checar_coleta_saldos` cobrava a coleta. Faltava o irmao: tabela-raiz de
    fila tambem tem vigia. Consertar uma vez nao impede a segunda vez.
    """
    if dias_desde_ultima_venda is None:
        return Achado(
            invariante="orders_frescos",
            severidade="quebra",
            resumo="Não consegui ler a data da última venda em `orders`",
            evidencia="orders vazia ou sem data_venda legível — a fila de "
                      "saldo nasce daqui; sem isso a cadeia inteira para",
            acao=("Rode `python scripts/sync_cloud.py --so-orders` e confira "
                  "o workflow ntc-sync.yml."),
        )
    if dias_desde_ultima_venda <= DIAS_ORDERS_ACEITAVEL:
        return None
    return Achado(
        invariante="orders_frescos",
        severidade="quebra",
        resumo="A tabela `orders` parou de receber vendas — a fila de saldo "
               "nasce dela",
        evidencia=f"última venda há {dias_desde_ultima_venda} dias "
                  f"(aceitável: até {DIAS_ORDERS_ACEITAVEL}) — o coletor de "
                  f"saldo e o motor partem FROM orders, então a cadeia "
                  f"inteira para junto, em silêncio",
        acao=("Rode `python scripts/sync_cloud.py --so-orders` agora e veja "
              "por que o ntc-sync.yml parou de atualizar orders. Em 23/07 "
              "isso ficou 13 dias sem ninguém notar."),
    )


def checar_coleta_saldos(dias_desde_ultima: Optional[int]) -> Optional[Achado]:
    """A coleta de saldos esta em dia?

    Em 01/08/2026 descobrimos que estava parada desde 24/07 -- nove dias -- e
    por isso 25% dos casos fechados em julho ficaram sem saldo. O balanco
    mensal sairia parcial e ninguem saberia por que.

    Como a coleta depende da maquina do operador (sessao logada no Meli),
    disciplina nao basta: o sistema tem que cobrar."""
    if dias_desde_ultima is None:
        return Achado(
            invariante="coleta_saldos",
            severidade="quebra",
            resumo="Nunca houve coleta de saldos",
            evidencia="meli_page_saldos sem registro de coleta",
            acao=("Rode `python scripts/coletar_saldos_meli.py --de AAAA-MM-DD "
                  "--ate AAAA-MM-DD` na máquina com a sessão do Meli."),
        )
    if dias_desde_ultima <= DIAS_COLETA_ACEITAVEL:
        return None
    return Achado(
        invariante="coleta_saldos",
        severidade="quebra",
        resumo="A coleta de saldos está atrasada",
        evidencia=f"última coleta há {dias_desde_ultima} dias "
                  f"(aceitável: até {DIAS_COLETA_ACEITAVEL})",
        acao=("Cada dia sem coletar é um caso fechado que entra no mês sem "
              "saldo. Rode `python scripts/coletar_saldos_meli.py` na máquina "
              "com a sessão do Meli."),
    )


def checar_conciliados_nao_caiu(atual: int, anterior: Optional[int]) -> Optional[Achado]:
    """A catraca de verdade: o NUMERO de casos conciliados nunca cai.

    Em 31/07/2026 resolver 2.920 shipments fez a cobertura PERCENTUAL cair de
    9,1% para 7,6% -- nao porque algo piorou, mas porque 2.920 pedidos validos
    entraram no denominador. A catraca percentual acusou uma correcao
    bem-sucedida como regressao, que e o alarme falso que ela existe para
    evitar.

    O absoluto so cai quando ha perda real de dado, entao e ele que serve de
    portao. O percentual continua sendo publicado como termometro de progresso,
    mas nao derruba mais o CI sozinho."""
    if anterior is None:
        return None
    if atual >= anterior:
        return None
    return Achado(
        invariante="conciliados_absoluto",
        severidade="quebra",
        resumo="Casos conciliados DIMINUIRAM",
        evidencia=f"eram {anterior}, agora {atual} ({anterior - atual} a menos)",
        acao=("Dado que ja virou R$ voltou a nao ter saldo. Verifique o ultimo "
              "job que escreveu em meli_page_saldos ou mexeu em order_id."),
    )


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
                 and len(str(o).strip()) not in DIGITOS_ORDER_VALIDOS
                 and int(str(o).strip()) not in IRRECUPERAVEIS
                 if str(o).strip().isdigit()]
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

            # guarda tambem o ABSOLUTO: e ele que serve de catraca. O
            # percentual muda quando o denominador cresce (resolver shipments
            # aumenta o universo de pedidos validos) e acusaria uma correcao
            # bem-sucedida como regressao.
            try:
                cur.execute("ALTER TABLE confianca_placar "
                            "ADD COLUMN IF NOT EXISTS conciliados INTEGER")
                conn.commit()
            except Exception:
                conn.rollback()

            cur.execute("SELECT cobertura, conciliados FROM confianca_placar "
                        "ORDER BY medido_em DESC LIMIT 1")
            linha = cur.fetchone()
            anterior = float(linha[0]) if linha else None
            anterior_abs = int(linha[1]) if linha and linha[1] is not None else None

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

            # A catraca e o ABSOLUTO. O percentual segue publicado como
            # termometro de progresso, mas nao derruba o CI sozinho -- ele cai
            # legitimamente quando o universo cresce.
            # Frescor da coleta de saldos: sem isso, cada dia parado vira um
            # caso fechado que entra no mes sem valor apurado.
            cur.execute("SELECT MAX(coletado_em) FROM meli_page_saldos")
            linha_coleta = cur.fetchone()
            dias_coleta = None
            if linha_coleta and linha_coleta[0]:
                from datetime import datetime as _dt, timezone as _tz
                ult = linha_coleta[0]
                if ult.tzinfo is None:
                    ult = ult.replace(tzinfo=_tz.utc)
                dias_coleta = (_dt.now(_tz.utc) - ult).days

            # Frescor de `orders`: a tabela-raiz da FILA. Ela congelou 13 dias
            # (23/07 a 04/08) e derrubou coletor e motor junto, sem nada ficar
            # vermelho. Consertar uma vez nao impede a segunda.
            cur.execute("SELECT MAX(data_venda) FROM orders")
            linha_orders = cur.fetchone()
            dias_orders = None
            if linha_orders and linha_orders[0]:
                from datetime import datetime as _dt2, timezone as _tz2
                ult_venda = linha_orders[0]
                if getattr(ult_venda, "tzinfo", None) is None:
                    ult_venda = ult_venda.replace(tzinfo=_tz2.utc)
                dias_orders = (_dt2.now(_tz2.utc) - ult_venda).days

            for a in (checar_conciliados_nao_caiu(conciliados or 0, anterior_abs),
                      checar_orders_frescos(dias_orders),
                      checar_coleta_saldos(dias_coleta),
                      checar_order_ids_reais(ids),
                      checar_duplicatas(claims_fechamento)):
                if a:
                    achados.append(a)

            cur.execute("INSERT INTO confianca_placar (cobertura, conciliados) "
                        "VALUES (%s, %s)",
                        (round(cobertura, 2), conciliados or 0))
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
