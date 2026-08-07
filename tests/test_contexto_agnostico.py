"""O projeto tem que ser legível por outro modelo, não só pelo Claude.

O Lucas tem uma conta Claude Pro e duas Gemini. Se ele trocar de ferramenta,
precisa conseguir dizer "leia o histórico de commits" e o modelo novo entender
o projeto inteiro — as decisões, os defeitos passados, as invariantes.

O problema não era qualidade do que está escrito. Era **nome de arquivo**:
nenhuma ferramenta do Google lê `CLAUDE.md`. E o `AGENTS.md`, que é o padrão
aberto adotado por mais de 60 mil repositórios sob a Linux Foundation, o
Claude Code também não lê sozinho.

A saída é a que a própria documentação da Anthropic recomenda: **uma fonte
única, e os outros arquivos importam ela.**

    "Claude Code reads CLAUDE.md, not AGENTS.md. If your repository already
     uses AGENTS.md for other coding agents, create a CLAUDE.md that imports
     it so both tools read the same instructions without duplicating them."
    https://code.claude.com/docs/en/memory

Estes testes travam a classe de defeito "troquei de modelo e ele não achou as
regras" — do mesmo jeito que `test_deploy_integridade.py` travou "importei
arquivo que não estava no git".

**Por que testar duplicação e não só existência:** dois arquivos com a mesma
regra divergem em uma semana, e aí ninguém sabe qual vale. Pior ainda porque
em algumas ferramentas do Google o `GEMINI.md` tem PRECEDÊNCIA sobre o
`AGENTS.md` no mesmo diretório — uma cópia velha venceria a fonte em silêncio.
"""
import re
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

# Interseção dos dois ecossistemas: a Anthropic recomenda menos de 200 linhas
# ("For each line, ask: 'Would removing this cause Claude to make mistakes?'
# If not, cut it"); o Antigravity do Google impõe teto DURO de 12.000
# caracteres por arquivo de regra. Vale o mais apertado de cada.
MAX_LINHAS = 200
MAX_CHARS = 12_000

# Acima disto, uma linha repetida entre a fonte e um ponteiro é conteúdo
# duplicado, não coincidência de formatação.
MIN_LINHA_SIGNIFICATIVA = 40

PONTEIROS = ("CLAUDE.md", "GEMINI.md")


def _ler(nome: str) -> str:
    p = RAIZ / nome
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _versionado(nome: str) -> bool:
    r = subprocess.run(["git", "ls-files", "--error-unmatch", nome],
                       cwd=RAIZ, capture_output=True, text=True)
    return r.returncode == 0


# --- a fonte única existe e está no git ------------------------------------

def test_agents_md_existe():
    """O nome que Jules, Codex, Cursor, Android Studio e mais 20 ferramentas
    procuram."""
    assert (RAIZ / "AGENTS.md").exists()


def test_agents_md_esta_versionado():
    """O agente novo só vê o que está no git. Arquivo local não existe para
    quem clona — é a mesma regra que já vale para o deploy."""
    assert _versionado("AGENTS.md")


def test_agents_md_cabe_nos_dois_ecossistemas():
    s = _ler("AGENTS.md")
    assert len(s.splitlines()) <= MAX_LINHAS
    assert len(s) <= MAX_CHARS


def test_agents_md_ensina_a_ler_o_historico():
    """A pergunta que o modelo novo faz é "como eu entendo este projeto?".
    Se a resposta não estiver escrita, ele vai ler o histórico inteiro e
    gastar 30% da janela antes da primeira pergunta."""
    s = _ler("AGENTS.md").lower()
    assert "git log" in s
    assert "índice" in s or "indice" in s


# --- os ponteiros apontam, e não copiam ------------------------------------

@pytest.mark.parametrize("nome", PONTEIROS)
def test_ponteiro_existe_e_esta_no_git(nome):
    assert (RAIZ / nome).exists()
    assert _versionado(nome)


@pytest.mark.parametrize("nome", PONTEIROS)
def test_ponteiro_importa_a_fonte(nome):
    """Import, não symlink: no Windows criar symlink exige Administrador ou
    Modo de Desenvolvedor, e a doc da Anthropic manda usar import nesse caso."""
    assert "AGENTS.md" in _ler(nome)


@pytest.mark.parametrize("nome", PONTEIROS)
def test_ponteiro_e_curto(nome):
    """Ponteiro que cresce virou cópia. É assim que a duplicação nasce."""
    assert len(_ler(nome).splitlines()) <= 20


@pytest.mark.parametrize("nome", PONTEIROS)
def test_ponteiro_nao_repete_regra_da_fonte(nome):
    """O teste que realmente impede duas verdades. Se alguém colar uma regra
    aqui, ela vai divergir da fonte — e no Android Studio o GEMINI.md tem
    precedência, então a cópia velha venceria em silêncio."""
    fonte = {l.strip() for l in _ler("AGENTS.md").splitlines()
             if len(l.strip()) >= MIN_LINHA_SIGNIFICATIVA}
    repetidas = [l.strip() for l in _ler(nome).splitlines()
                 if l.strip() in fonte]
    assert not repetidas, f"{nome} repete a fonte: {repetidas[:2]}"


def test_antigravity_tem_ponteiro():
    """O Antigravity não lê AGENTS.md da raiz — a doc oficial só nomeia
    `~/.gemini/GEMINI.md` e `.agents/rules`."""
    p = RAIZ / ".agents" / "rules" / "projeto.md"
    assert p.exists()
    assert "AGENTS.md" in p.read_text(encoding="utf-8")


def test_regras_do_antigravity_cabem_no_limite_dele():
    for p in (RAIZ / ".agents" / "rules").glob("*.md"):
        assert len(p.read_text(encoding="utf-8")) <= MAX_CHARS, p.name


# --- o commit carrega o contexto -------------------------------------------

ASSUNTO = re.compile(
    r"^(feat|fix|docs|refactor|test|chore|perf|build|ci)"
    r"(\([a-z0-9_-]+\))?!?: .{10,}")


def _commits(desde: str = "2026-08-07") -> list[dict]:
    """Só commits novos. Os ~1.200 antigos nasceram antes desta regra e
    quebrariam o CI sem ensinar nada."""
    r = subprocess.run(
        ["git", "log", f"--since={desde}", "--pretty=%H%x1f%s%x1f%b%x1e"],
        cwd=RAIZ, capture_output=True, text=True, encoding="utf-8")
    saida = []
    for bruto in r.stdout.split("\x1e"):
        if not bruto.strip():
            continue
        partes = bruto.strip().split("\x1f")
        if len(partes) >= 3:
            saida.append({"sha": partes[0], "assunto": partes[1],
                          "corpo": partes[2]})
    return saida


def test_assunto_segue_o_padrao():
    """O assunto é o índice: o modelo lê 1.200 deles e decide o que abrir.
    Se ele não disser o defeito, o índice não serve para nada."""
    ruins = [c["sha"][:7] + " " + c["assunto"]
             for c in _commits() if not ASSUNTO.match(c["assunto"])]
    assert not ruins, f"assunto fora do padrão: {ruins}"


def test_feat_e_fix_explicam_o_porque():
    """Corpo de uma linha não reconstrói decisão nenhuma."""
    magros = [c["sha"][:7] for c in _commits()
              if c["assunto"].startswith(("feat", "fix"))
              and len([l for l in c["corpo"].splitlines() if l.strip()]) < 3]
    assert not magros, f"corpo curto demais em: {magros}"


def test_o_indice_do_historico_ainda_cabe_numa_janela():
    """Medido em 07/08/2026: `--oneline` inteiro dá 77,5 KB (~20k tokens) e
    cabe; com corpo completo dá 242,8 KB (~61k), que é 30% de uma janela
    gasta antes da primeira pergunta. Quando o índice passar do teto, a regra
    de leitura precisa mudar — e é melhor descobrir por teste que por sessão
    travada."""
    r = subprocess.run(["git", "log", "--oneline"], cwd=RAIZ,
                       capture_output=True, text=True, encoding="utf-8")
    assert len(r.stdout.encode("utf-8")) <= 120_000
