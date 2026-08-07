@AGENTS.md

## Claude Code

Isto aqui é só o que muda para o Claude Code. As regras do projeto estão em
`AGENTS.md` — este arquivo não tem conteúdo próprio de propósito: duas cópias
da mesma regra divergem em uma semana, e aí ninguém sabe qual vale.

- **Cota se economiza no contexto, não na conversa.** `/clear` ao trocar de
  assunto, `/compact` no meio da tarefa, subagente para busca ampla, modelo
  caro só onde há alavancagem. Medir com `python -m harness.cota`.
- **Recibo junto do commit para produção:** `python -m harness.recibo`.
