# MTA — Monitor de Transações e Anomalias (PoC)

Painel financeiro de **devoluções, reclamações e cancelamentos** do Mercado Livre / Mercado Pago,
com conciliação ao vivo contra a **ML API** e cruzamento de custo (CMV) via **Tiny ERP**.

## O que a PoC entrega

| Recurso | Descrição |
|---|---|
| **Ingestão MP** | Importa relatórios `after_collection*.xlsx/csv` do Mercado Pago para o Postgres (Neon), com dedupe por transação e log de importação |
| **Painel HTML interativo** | Cards clicáveis, filtro global de período (referenciado ao fim dos dados), gráficos Plotly, modais com detalhe por pedido |
| **Composição do Impacto** | Breakdown dos recortes que formam o valor total: recuperado (+ verde), perda real / taxa retida / frete do prejuízo (− vermelho), impacto líquido no caixa |
| **Cancelamentos reais** | Fonte correta: estado do pedido na base ML (`Cancelada pelo comprador`, `Pacote cancelado…`), com detecção de **anomalias** (cancelamento cujo saldo não zerou) |
| **Conciliação ML API** | Valida cada pedido contra `GET /orders/{id}` (estado vivo do pagamento), cache em `mp_validation_results` |
| **Conciliação collection** | Ingere o relatório `collection*.xlsx` (vendas) em `mp_collection` e cruza `amount_refunded`/`status_detail` por pedido — a API sempre sobrescreve o xlsx (que pode estar defasado) |
| **Recorte "Mantido pelo vendedor"** | Disputa encerrada com pagamento `accredited` e zero reembolso ao comprador **não é perda** — o valor ficou com o vendedor (regra validada pedido a pedido na plataforma) |
| **Polling incremental** | `ml_live_poll.py` mantém o painel sempre atualizado revalidando **só o que pode mudar** (claims abertos, cancelados recentes, estados não-terminais), com TTL por pedido e chamadas paralelas — ciclo típico < 3 min |
| **Validação por amostragem (RPA)** | `validar_amostras_meli.py` pega os N primeiros de cada card e valida contra a ML API **e** a plataforma web (Playwright + login persistente), com screenshot de evidência por pedido e relatório MD com veredito |
| **Excel executivo** | XLSX de 8 abas (resumo, mensal, semestral, por produto, motivos, relatório MP, detalhe por pedido, log) |

## Regras de exibição (negócio)

- **Prejuízo é sempre negativo e vermelho**; valor recuperado é sempre positivo e verde.
- Cancelamento **deve zerar o pedido** — linha com saldo residual ≠ 0 é marcada como anomalia (amarelo).
- Modais mostram o **estado atual na ML API** (`Estado Atual ML` + `Validado em`), não só o snapshot do arquivo.

## Como rodar

```powershell
# 1. dependências
pip install -r requirements.txt

# 2. credenciais (nunca commitadas) — ver .env.example
$env:ML_NEON_URL     = "postgresql://..."   # Postgres (Neon)
$env:ML_ACCESS_TOKEN = "..."                # opcional: fallback do token ML
$env:TINY_TOKEN      = "..."                # opcional: CMV via Tiny

# 3. salvar o after_collection*.xlsx exportado do MP em tmp_csvs\

# 4. gerar o relatório completo (HTML + XLSX + JSON)
python scripts/processar_relatorios_mp.py

# 5. modo vivo: polling incremental da ML API + servidor local
python scripts/ml_live_poll.py --serve 8765
# abre http://localhost:8765/painel_devolucoes_live.html
# o painel avisa no navegador quando um ciclo termina ("Atualizar painel")

# 6. validação por amostragem (10 primeiros de cada card × API × plataforma web)
python scripts/validar_amostras_meli.py --n 10
# 1ª execução abre o Chromium: faça login no ML (sessão fica salva em _rpa_meli_profile/)
# saída: reports/validacao_amostras_YYYY-MM-DD.md + screenshots em _rpa_meli_valida/
```

### Flags úteis

```text
processar_relatorios_mp.py
  --pasta DIR                 pasta dos arquivos MP (padrão tmp_csvs/)
  --force                     reimportar arquivos já logados
  --revalidar                 ignora cache e revalida tudo na ML API
  --validar-base-completa     inclui toda a base ml_devolucoes (~10 min na 1ª vez)

ml_live_poll.py
  --intervalo N               minutos entre ciclos (padrão 15)
  --janela-dias N             janela de cancelamentos vigiados (padrão 45)
  --ttl-min N                 não reconsultar o mesmo pedido antes de N min (padrão 30)
  --workers N                 chamadas paralelas à ML API (padrão 16)
  --serve PORTA               serve reports/ em http://localhost:PORTA
  --once                      um ciclo e sai
```

## Arquitetura

```
tmp_csvs/after_collection*.xlsx ──▶ mp_ingestion ──▶ Neon (mp_transactions)
                                                        │
orders / order_items / ml_devolucoes / tiny_sku_costs ──┤
                                                        ▼
                                    processar_relatorios_mp (análise + KPI)
                                                        │
                    ┌───────────────────────────────────┼──────────────┐
                    ▼                                   ▼              ▼
        relatorio_devolucoes_*.html          relatorio_*.xlsx   relatorio_*.json
        painel_devolucoes_live.html  ◀── ml_live_poll (polling incremental ML API)
```

## Segurança / LGPD

- Nenhuma credencial no código: tudo via `st.secrets` ou variáveis de ambiente.
- Cliente ML **somente leitura** (nunca chama endpoints de escrita).
- Relatórios gerados (`reports/`) e arquivos MP (`tmp_csvs/`) ficam fora do versionamento (`.gitignore`).
