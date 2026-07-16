# Spec de Regras Meli — minerado de 1,257 extratos reais (13/07/2026 13:16)

## Identidades testadas

- **IDENTIDADE CONTÁBIL: Total = Σ seções (produto+parcelamento−tarifa−envios−cancelamentos)**: 1257/1257 (100.0%)
- **Parcelamento líquido = 0 (acréscimo do comprador cobre a taxa)**: 273/273 (100.0%)
- **Reembolso presente → existe estorno de tarifa**: 564/1101 (51.2%)
- **Tarifa de devolução = 2× frete de ida**: 105/121 (86.8%)
- **'Cancelamento de tarifa' = estorno integral da tarifa de venda**: 522/550 (94.9%)
- **Devolução finalizada → Total = −envios**: 138/1020 (13.5%)

## Catálogo de rótulos recorrentes (≥3 ocorrências; nomes de produto filtrados)

| rótulo | freq | exemplo (order_id) |
|---|---|---|
| Tarifa de venda total | 1257 | 2000015881665840 |
| Total | 1257 | 2000015881665840 |
| Envios | 1248 | 2000015881665840 |
| Cancelamentos | 1101 | 2000015881665840 |
| Preço do produto | 965 | 2000015881665840 |
| Tarifa do Mercado Envios (por sua conta) | 640 | 2000015861985966 |
| Cancelamento de tarifa do Mercado Envios (por sua conta) | 410 | 2000016156473820 |
| Taxa de parcelamento e acréscimo | 273 | 2000015881637692 |
| Cancelamento de tarifa de 11% | 221 | 2000015861985966 |
| Tarifa de 10% | 178 | 2000016157813058 |
| Cancelamento de tarifa de 10% | 165 | 2000016172850538 |
| Tarifa de 11% | 158 | 2000016156843750 |
| Tarifa de devolução | 134 | 2000015861985966 |
| Pagamento do Mercado Envios (por conta do comprador) | 84 | 2000016172837234 |
| Tarifa por envios no Mercado Livre (por sua conta e por conta do compr | 84 | 2000016172837234 |
| Cancelamento de pagamento do frete (por conta do comprador) | 69 | 2000016172837234 |
| Cancelamento de tarifa de 13% | 68 | 2000016156473820 |
| Acréscimo no preço (pago pelo comprador) | 65 | 2000015847957242 |
| Taxa de parcelamento | 65 | 2000015847957242 |
| Cancelamento de tarifa do Mercado Envios (por sua conta e por conta do | 59 | 2000015843108810 |
| Tarifa de 13% | 51 | 2000016156473820 |
| Cancelamento de tarifa de 12% | 36 | 2000015857216692 |
| Tarifa de 12% | 32 | 2000015803032474 |
| Placa Geladeira Brastemp W10713439 W10887155 W10887427 127/220v | 31 | 2000015859978158 |
| Cancelamento de Placa Geladeira Brastemp W10713439 W10887155 W10887427 | 22 | 2000015838106948 |
| Estorno | 21 | 2000012314907099 |
| Filtro Antibactéria Antiodor Geladeira Consul Bem Estar | 20 | 2000016800848066 |
| Compressor Embraco 1/3hp R134a Emr100hlr Refrigeradores E Freezers 220 | 18 | 2000015794912194 |
| Placa Led Geladeira Electrolux Db53 Df44 A15560901 | 17 | 2000015810762004 |
| Preço dos produtos | 17 | 2000012048151428 |
| Limpador Desincrustante Limpeza Ar Condicionado E Geladeira | 16 | 2000015803032474 |
| Motor Compressor Embraco Em2p70clp R600a 1/5 127v | 15 | 2000012328336363 |
| Cancelamento de tarifa de 15% | 15 | 2000016795440042 |
| Cancelamento do acréscimo no preço (pago pelo comprador) | 15 | 2000016763246068 |
| Cancelamento de taxa de parcelamento | 15 | 2000016763246068 |
| Controle LG An-mr21ga Para Tv 60up7750psb Preto | 14 | 2000016156473820 |
| Motor Compressor Embraco Em2p70clp R600a 1/5 220v | 14 | 2000015850977694 |
| Cancelamento de Placa Led Geladeira Electrolux Db53 Df44 A15560901 | 14 | 2000015810762004 |
| Cancelamento de tarifa de 11,5% | 13 | 2000015790867552 |
| Cancelamento de Filtro Antibactéria Antiodor Geladeira Consul Bem Esta | 13 | 2000016800848066 |
| Tarifa de 15% | 13 | 2000016795440042 |
| Embraco Motor Compressor Inverter 1/5 Vemx7c R-600a | 12 | 2000016787867218 |
| Inversor Frequencial Embraco 127v Refrig Electrolux Ib54s 127v | 12 | 2000016630120802 |
| Motor Compressor Embraco Emr70hlr 1/5 Hp R134 | 11 | 2000015847014644 |
| Cancelamento de tarifa de 16% | 11 | 2000015833316888 |
| Valvula Otimizadora 5/16f X 1/4m X 3/8m C/ Adapt 1/4 Friven | 11 | 2000017134824624 |
| Compressor Inverter Electrolux Fmsy9c Geladeira R600 127/220v | 11 | 2000016895279016 |
| Cancelamento de Controle LG An-mr21ga Para Tv 60up7750psb Preto | 10 | 2000016156473820 |
| Placa Potência Lavadora Brastemp Bws15 W10912973 W10912972 127v | 10 | 2000015836508560 |
| Cancelamento de Compressor Embraco 1/3hp R134a Emr100hlr Refrigeradore | 10 | 2000015794912194 |
| Placa Interface Lavadora Ms Cwe15a 15kg W10758076 127/220v | 10 | 2000016790905198 |
| Valvula Otimizadora 1/4f X 1/4m X 3/8m C/ Adapt 1/4 Friven | 10 | 2000016929578548 |
| Bactericida Aromatizador Para Ar Condicionado Split 5 Litros | 10 | 2000017389194438 |
| Placa De Potencia Lava Louça Lv08b Le08b Electrolux A08760001 Original | 9 | 2000016152575856 |
| Tarifa de 11,5% | 9 | 2000016131605478 |
| Módulo Inversor Frequencial Embraco 519402004 220v Baby | 9 | 2000015857487838 |
| Tarifa de 16% | 9 | 2000016800848066 |
| Motor Compressor Embraco 1/3 Egas 100hlr R134 127v | 9 | 2000016857892392 |
| Controle Remoto Magic An-mr22gn Tv LG Ebx64334909 Preto | 8 | 2000016134273710 |
| Cancelamento de Motor Compressor Embraco Emr70hlr 1/5 Hp R134 | 8 | 2000015847014644 |
| Cancelamento de Placa Potência Lavadora Brastemp Bws15 W10912973 W1091 | 8 | 2000015836508560 |
| Cancelamento de Limpador Desincrustante Limpeza Ar Condicionado E Gela | 8 | 2000015803032474 |
| Módulo Inversor Embraco 519402003 127v Frequencial Baby | 8 | 2000016952969716 |
| Cancelamento de Placa Interface Lavadora Ms Cwe15a 15kg W10758076 127/ | 8 | 2000016790905198 |
| Tubo De Cobre 3/16 Eluma 15m Recozido Para Ar Condicionado | 8 | 2000016928765732 |
| Cancelamento de Motor Compressor Embraco Em2p70clp R600a 1/5 220v | 8 | 2000016773408332 |
| Cancelamento de Bactericida Aromatizador Para Ar Condicionado Split 5  | 8 | 2000017389194438 |
| Bandeja Compressor Ff Fg Eg W10686819 W10286196 Embraco | 7 | 2000012352159629 |
| Descontos e bônus | 7 | 2000012314907099 |
| Cancelamento de Placa De Potencia Lava Louça Lv08b Le08b Electrolux A0 | 7 | 2000016152575856 |
| Cancelamento de Módulo Inversor Embraco 519402003 127v Frequencial Bab | 7 | 2000016952969716 |
| Motor Compressor 1/4+ Emr80hlr R134 Embraco 127v | 7 | 2000016795135052 |
| Tarifa de 9,5% | 7 | 2000016928765732 |
| Cancelamento de Valvula Otimizadora 5/16f X 1/4m X 3/8m C/ Adapt 1/4 F | 7 | 2000016763246068 |
| Compressor Embraco Em2p60clp R600a Refrigerador Electrolux 127v | 7 | 2000016626873428 |
| Bactericida Ar Condicionado Split 5 Litros Lavanda | 7 | 2000017136385546 |
| Cancelamento de Compressor Inverter Electrolux Fmsy9c Geladeira R600 1 | 7 | 2000016895279016 |
| Termostato Frigobar Geladeira Electrolux R250 R280 Rc13309 | 7 | 2000016812518342 |
| Compressor Embraco 1/5hp R-600a Em2p60clp 220v | 6 | 2000015856268456 |
| Placa Interface Refrigerador Electrolux A96969602 127/220v | 6 | 2000016787811320 |

## Conclusões de engenharia (motor v2)

**Fórmula universal (100,0% em 1.257 casos):**
`SALDO = produto + parcelamento − tarifa_venda − envios − cancelamentos_liq`

Fonte estruturada por componente:
| componente | fonte | acurácia hoje |
|---|---|---|
| produto | API order_items (unit_price × qty) | 99,3% |
| parcelamento | regra: líquido = 0 (100%) | 100% |
| tarifa_venda | API order_items.sale_fee | 86,5% → investigar diferenças |
| envios | frete ida (orders/shipments) + tarifa devolução (2× ida em 86,8% das devoluções físicas; exceções a mapear via /shipments/{id}/costs) | 66,4% |
| cancelamentos_liq | reembolso vivo (API payments.transaction_amount_refunded) − estornos | a implementar |

**Regra dos estornos (derivada algebricamente + sub-linhas):**
- cancelamento PRÉ-ENVIO: estorno integral de tarifa E frete → saldo 0 (verificado: estornos = tarifa+frete exato)
- devolução PÓS-ENTREGA: estorno de tarifa presente em 100% dos casos com sub-linhas expandidas; frete ida + tarifa de devolução ficam com o vendedor
- estorno, quando existe, é integral (94,9%; 5,1% = casos parciais a inspecionar)

**Pendências de insumo estruturado:**
1. `GET /shipments/{id}/costs` (envio ida e reverso) — elimina a regra aproximada do 2×
2. sub-regras da tarifa_venda (13,5% divergem — candidatos: multi-item, campanhas, custo fixo por unidade)
3. coletor: expandir seções recolhidas nos layouts antigos (537 casos sem sub-linhas — agregado ok, decomposição faltando)

**Arquitetura alvo:** motor v2 calcula o saldo de TODA a base com a fórmula acima;
`meli_page_saldos` (RPA) vira exclusivamente QA contínuo — divergência motor×página = alerta
de regra nova a especificar. Painel migra para motor-first quando QA ≥ 99%.
