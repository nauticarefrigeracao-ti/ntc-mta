# Se o SAC parar — o que fazer

Este guia é para **Thayná e Gabriel**, não para o Lucas. Nenhum passo aqui
precisa de conhecimento técnico. Siga na ordem e pare quando funcionar.

Se em **10 minutos** nada resolver, chame o Lucas. Não é para insistir.

---

## Como sei que parou?

O robô avisa sozinho no **#sac-fechamento**, assim:

> 🔴 **O SAC ficou 2h30 fora do ar** nas últimas 24h...

Se apareceu essa mensagem, siga abaixo.

Se **ninguém avisou nada**, está funcionando. O robô só fala quando há
problema — silêncio é boa notícia.

---

## Sintoma 1 — clico no botão do card e não acontece nada

Ou aparece *"Tivemos alguns problemas de conexão"*.

**O que está acontecendo:** o programa que escuta os cliques parou.

**Importante:** os cliques dados enquanto ele estava parado **se perderam**.
Depois de resolver, peça para a Maria refazer as marcações do período.

### Passo 1 — espere 2 minutos e tente de novo

O programa se reinicia sozinho. Na maior parte das vezes isso basta.

### Passo 2 — ligar de novo pelo painel da Hostinger

1. Entre em **hpanel.hostinger.com** com a conta da empresa
2. Menu **VPS** → clique no servidor
3. Botão **Reiniciar** (ou *Restart*)
4. Espere 3 minutos e teste um botão no Slack

Reiniciar o servidor é seguro. Nenhum dado se perde — tudo mora no banco,
não na máquina.

### Passo 3 — se ainda não voltou

Chame o Lucas. Mande print da mensagem de erro e diga **a que horas** parou.

---

## Sintoma 2 — os cards não aparecem de manhã

**O que está acontecendo:** ou o Mercado Livre está fora, ou o token de
acesso venceu.

**Não tente resolver.** Chame o Lucas. Não há passo seguro aqui, e mexer
errado no token derruba também o painel e o balanço.

---

## Sintoma 3 — o painel ntc-devolucoes.streamlit.app não abre

**O que está acontecendo:** o painel hiberna quando ninguém usa. A primeira
abertura do dia demora.

**O que fazer:** espere 1 minuto e recarregue a página. Se aparecer um botão
azul escrito *"Yes, get this app back up!"*, clique nele.

Se depois de 3 minutos não abrir, chame o Lucas.

---

## Sintoma 4 — número do cofrinho parece errado

**Antes de reportar, confira se não é confusão entre dois números
diferentes.** Eles não batem de propósito:

| onde | o que é | quando chega |
|---|---|---|
| **Cofrinho** (`#sac`) | valor da venda em disputa, marcado pela Maria ao fechar o caso | na hora |
| **Balanço** (`#sac-fechamento`) | prejuízo real, batido com o extrato do Mercado Livre | dias depois |

O balanço já desconta tarifa, frete e o que o ML cobriu. O cofrinho não.
**São números diferentes e não vão bater** — isso está certo.

Se o card tiver o selo 🧪 **ENSAIO**, é o canal de treino: nada ali conta em
lugar nenhum.

---

## O que NÃO fazer

- **Não apague mensagem ou canvas** do `#sac` ou `#sac-fechamento`. O robô
  reescreve os mesmos; apagar faz ele criar duplicado.
- **Não remova o app "SAC Náutica"** do workspace. Reinstalar exige refazer
  16 permissões na mão.
- **Não cancele o plano Slack Pro.** No plano grátis o Quadro do SAC vira
  somente-leitura e o histórico some depois de 90 dias.

---

## Contatos e acessos

| o quê | onde |
|---|---|
| Servidor | hpanel.hostinger.com — conta da empresa |
| Banco de dados | console.neon.tech — conta da empresa |
| Código e automações | github.com/nauticarefrigeracao-ti |
| Slack | app.slack.com — workspace Náutica Refrigeração |

**Todos os acessos estão na conta da empresa, não na pessoal do Lucas.**
