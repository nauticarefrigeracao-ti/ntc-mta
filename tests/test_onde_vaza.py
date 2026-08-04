"""A pergunta que o chefe faz e hoje exige SQL: qual produto me custa dinheiro?

Medição de maio a julho/2026 sobre os casos com prejuízo apurado:

    motivo                          casos   prejuízo
    Produto não funciona              96    R$ 9.937,78     <- 77% de tudo
    Produto incompleto                16    R$ 1.130,72
    Produto diferente do anúncio      13    R$   907,17
    Prazo de entrega ultrapassado     11    R$   509,82

    SKU
    NR1058-2                          12    R$ 1.737,60
    NR1064-1                           7    R$   922,05
    NR1058-1                           6    R$   864,90

Um motivo responde por 77% do prejuízo — e não é motivo de SAC. É de produto,
fornecedor ou anúncio. O SAC paga a conta de uma decisão tomada antes dele.
A família NR1058 sozinha: 18 casos, R$ 2.602,50.

Isso vai no Canvas mensal, em markdown, no canal que o chefe já abre. Sem
componente visual novo — a regra do Design System vale.
"""
import pytest

from balanco_mensal import montar_canvas_mensal, secao_onde_vaza

BASE = {
    "casos": 292, "negativos": 67, "zerados": 147, "revertidos": 77,
    "sem_saldo": 1, "prejuizo": -6102.43, "revertido": 21928.87,
    "saldo": 15826.44, "receita": 92582.71, "reembolsado": 36790.14,
    "cobertura_pct": 99.7,
}

MOTIVOS = [{"chave": "Produto não funciona", "casos": 96, "prejuizo": -9937.78},
           {"chave": "Produto incompleto", "casos": 16, "prejuizo": -1130.72},
           {"chave": "Produto diferente do anúncio", "casos": 13,
            "prejuizo": -907.17}]

SKUS = [{"chave": "NR1058-2", "casos": 12, "prejuizo": -1737.60},
        {"chave": "NR1064-1", "casos": 7, "prejuizo": -922.05},
        {"chave": "NR1058-1", "casos": 6, "prejuizo": -864.90}]


def test_lista_o_motivo_que_mais_custa_primeiro():
    txt = secao_onde_vaza(MOTIVOS, SKUS)
    assert txt.index("Produto não funciona") < txt.index("Produto incompleto")


def test_traz_o_sku_com_o_valor():
    txt = secao_onde_vaza(MOTIVOS, SKUS)
    assert "NR1058-2" in txt and "1.737,60" in txt


def test_diz_quanto_o_primeiro_motivo_concentra():
    """"96 casos" não move ninguém; "77% de todo o prejuízo" move."""
    txt = secao_onde_vaza(MOTIVOS, SKUS)
    assert "77" in txt or "78" in txt


def test_nao_inventa_concentracao_quando_esta_espalhado():
    espalhado = [{"chave": "A", "casos": 5, "prejuizo": -100.0},
                 {"chave": "B", "casos": 5, "prejuizo": -95.0},
                 {"chave": "C", "casos": 5, "prejuizo": -90.0}]
    txt = secao_onde_vaza(espalhado, SKUS)
    assert "%" not in txt.split("SKU")[0] or "35" in txt


def test_sem_dado_nao_publica_secao_vazia():
    """Cabeçalho sem linha embaixo faz o leitor achar que quebrou."""
    assert secao_onde_vaza([], []) == ""


def test_so_motivos_ainda_vale():
    txt = secao_onde_vaza(MOTIVOS, [])
    assert "Produto não funciona" in txt
    assert "SKU" not in txt


def test_a_secao_entra_no_canvas_mensal():
    txt = montar_canvas_mensal("julho/2026", BASE, [],
                               motivos=MOTIVOS, skus=SKUS)
    assert "Produto não funciona" in txt
    assert "NR1058-2" in txt


def test_canvas_sem_a_secao_continua_valido():
    """Retrocompatível: quem chama sem os novos argumentos não quebra."""
    txt = montar_canvas_mensal("julho/2026", BASE, [])
    assert "Prejuízo do mês" in txt


def test_motivo_cru_do_ml_nao_vai_cru_para_o_chefe():
    """PDD9952 não significa nada para quem lê. Já foi defeito aqui antes."""
    cru = [{"chave": "PDD9952", "casos": 4, "prejuizo": -179.10}]
    txt = secao_onde_vaza(cru, [])
    assert "PDD9952" not in txt or "código" in txt.lower()
