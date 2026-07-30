# ntc-mta — SAC e conciliação (Náutica Refrigeração)

Notificador do SAC no Slack (#sac, #sac-fechamento), conciliação financeira das
devoluções e o placar de confiança dos números. Banco: Neon/Postgres,
compartilhado com o repositório **B.I**.

**O processo de trabalho é o mesmo dos dois repositórios e está em
`harness/PADRAO.md` (repo B.I). Leia antes de mudar qualquer coisa.**

---

## Regras que não se negociam

**Medir antes de afirmar.** Presumir "order_id com menos de 15 dígitos =
shipment" gerou uma invariante que acusava 5.099 pedidos válidos e que acusaria
o resultado da própria correção, para sempre. Medido na API do ML (30/07/2026):

| Dígitos | O que é | Quantos |
|---|---|---|
| 10 | pedido antigo legítimo | 5.099 |
| 11 | shipment | 2.922 |
| 16 (`2000…`) | pedido novo | 10.092 |

**Falhar alto.** Job que devia enviar e não enviou sai com código 1. O bot saiu
de #sac (`not_in_channel`), o notificador contou 0 enviadas, saiu 0 e o Actions
ficou **verde por 4 dias** enquanto a Maria não recebia nada. Ver
`status_saida()`.

**Link nunca vai para o ar sem ser aberto.** Um 404 na cara do chefe custa
credibilidade que código nenhum recupera.

**Número errado é pior que número faltando.** Ninguém desconfia de um número. O
fechamento contava o mesmo caso duas vezes (JOIN com `slack_notificados`, que
tem PK `(claim_id, status)`) e inflava o prejuízo em 45%. Deduplicar por
`claim_id` na origem **e** na composição.

**CTA é link, nunca botão.** Botão do Block Kit — mesmo só com `url` — faz o
Slack exigir Interactivity URL, que exigiria servidor sempre ligado (isto roda
em cron). Sem ela, o Slack estampa "app não configurado para respostas
interativas" ao lado de todo CTA.

**Segredo nunca no chat, log ou commit.** `os.environ` vem **antes** de
`st.secrets`: `st.secrets.get()` levanta quando não há `secrets.toml` (o caso do
GitHub Actions) e mata o fallback, deixando o token vazio e tudo em 401.

**Custo é orçamento.** Invariante é aritmética sobre dado já carregado — roda em
milissegundos. Modelo (caro) só entra quando há achado.

---

## Invariantes ativas

`confianca.py` — roda a cada push e às 8h BRT. Sai 1 quando algo não se
sustenta:

- **cobertura de conciliação é catraca**: pode subir, nunca cair (tolerância
  0,5 p.p. — invariante que grita à toa vira ruído);
- todo `order_id` tem formato de pedido (10 ou 16 dígitos);
- nenhum caso entra duas vezes na conta do fechamento;
- categorias somam o total exibido.

## Comandos

```bash
python -m pytest tests/ -q              # suite (163)
python confianca.py                     # invariantes contra dados reais
python confianca.py --slack             # publica o placar no #sac-fechamento
python resolver_order.py --dry-run      # mede antes de gravar
python slack_notify.py --quadro         # atualiza o Quadro Kanban
```

## Estrutura

- `slack_notify.py` — notificador, Quadro Kanban e fechamento diário
- `slack_client.py` — Web API (post, update, canais); nunca expõe token
- `confianca.py` — invariantes e placar · `resolver_order.py` — shipment→order
- `src/api/ml_client.py` — API do ML · `src/db/` — conexão Neon
