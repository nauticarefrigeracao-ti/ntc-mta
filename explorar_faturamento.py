"""Explora a seção Faturamento do Mercado Livre — acha o informe/relatório
que decompõe as tarifas de devolução por pedido (insumo que falta no motor).

Usa o perfil RPA já logado (_rpa_meli_profile). Navega, tira screenshots de
cada tela relevante e tenta baixar qualquer relatório de faturamento/tarifas
disponível para o período jan-jul/2026.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from validar_amostras_meli import ensure_login, PROFILE_DIR

# perfil PRÓPRIO (cópia da sessão) — o polling daemon usa PROFILE_DIR
# concorrentemente; SingletonLock do Chrome barra 2 processos no mesmo dir
EXPLORE_PROFILE = PROFILE_DIR  # daemon pausado — usar direto, sessão já logada

OUT = ROOT / "_rpa_faturamento"
OUT.mkdir(exist_ok=True)
DOWNLOAD_DIR = ROOT / "tmp_csvs"
DOWNLOAD_DIR.mkdir(exist_ok=True)

URLS = [
    "https://www.mercadolivre.com.br/faturamento",
    "https://www.mercadolivre.com.br/faturamento/detalhe",
    "https://www.mercadolivre.com.br/faturamento/relatorios",
    "https://www.mercadolivre.com.br/vendas/faturamento",
]


def main() -> None:
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    kwargs = dict(headless=False, viewport={"width": 1500, "height": 950},
                  args=["--disable-blink-features=AutomationControlled"],
                  accept_downloads=True)
    try:
        ctx = pw.chromium.launch_persistent_context(str(EXPLORE_PROFILE), channel="chrome", **kwargs)
    except Exception:
        ctx = pw.chromium.launch_persistent_context(str(EXPLORE_PROFILE), **kwargs)
    page = ensure_login(ctx.pages[0] if ctx.pages else ctx.new_page())

    # navega pelo menu lateral real do painel de Vendas — acha o item "Faturamento"
    page.goto("https://www.mercadolivre.com.br/vendas/omni/lista", timeout=30000)
    page.wait_for_timeout(3500)
    page.screenshot(path=str(OUT / "00_vendas.png"), full_page=True)

    def _achar_e_clicar(termos):
        for el in page.locator("a, button, [role=menuitem]").all()[:200]:
            try:
                txt = (el.inner_text(timeout=200) or "").strip().lower()
            except Exception:
                continue
            if any(t in txt for t in termos):
                print(f"  clicando em: '{txt}'")
                try:
                    el.click(timeout=3000)
                    page.wait_for_timeout(3000)
                    return True
                except Exception:
                    continue
        return False

    # o item fica dentro do menu hambúrguer (colapsado) — abre antes de clicar
    try:
        page.locator("button[aria-label*='menu' i], [class*=hamburger], svg[class*=menu]").first.click(timeout=2000)
        page.wait_for_timeout(1000)
        print("  menu hambúrguer aberto")
    except Exception:
        try:
            page.locator("aside button, nav button").first.click(timeout=2000)
            page.wait_for_timeout(1000)
        except Exception:
            print("  não achei botão de menu — tentando clicar direto")
    page.screenshot(path=str(OUT / "menu_viewport.png"))
    print("  screenshot do viewport (menu aberto): menu_viewport.png")

    # "Faturamento" no sidebar é um item colapsável (chevron) — clica para expandir
    fat_item = page.get_by_text("Faturamento", exact=True).first
    fat_item.click(timeout=3000)
    page.wait_for_timeout(1200)
    page.screenshot(path=str(OUT / "menu_faturamento_expandido.png"))

    # submenu real: "Tarifas e pagamentos" e "Emissor de NF-e"
    tarifas = page.get_by_text("Tarifas e pagamentos", exact=True).first
    tarifas.click(timeout=3000)
    page.wait_for_timeout(3500)
    print("  URL Tarifas e pagamentos:", page.url)
    page.screenshot(path=str(OUT / "tarifas_pagamentos.png"))
    print("  conteúdo:", page.inner_text("body")[:1200].replace("\n", " | "))

    # abre o detalhe de uma fatura FECHADA (Junho) e vai na aba Relatórios
    try:
        with ctx.expect_page(timeout=4000) as pop_info:
            page.get_by_text("Ir para detalhe", exact=True).nth(1).click(timeout=6000)
        page = pop_info.value
        page.wait_for_load_state(timeout=15000)
    except Exception:
        pass  # não abriu popup — segue na mesma page
    page.wait_for_timeout(3000)
    print("  URL detalhe fatura:", page.url)
    page.screenshot(path=str(OUT / "detalhe_fatura.png"))

    rel_tab = page.get_by_text("Relatórios", exact=True).first
    rel_tab.click(timeout=3000)
    page.wait_for_timeout(3000)
    print("  URL aba Relatórios:", page.url)
    page.screenshot(path=str(OUT / "aba_relatorios.png"), full_page=True)
    print("  conteúdo aba Relatórios:", page.inner_text("body")[:2000].replace("\n", " | "))

    termos = ("baixar", "download", "relatório", "relatorio", "informe", "extrato", "exportar", "gerar")
    achados = []
    for el in page.locator("a, button").all()[:300]:
        try:
            txt = (el.inner_text(timeout=150) or "").strip()
        except Exception:
            continue
        if txt and any(t in txt.lower() for t in termos):
            achados.append(txt)
    print("  botões/links relevantes na aba Relatórios:", achados[:25])

    # "Faturamento do Mercado Livre" = detalha tarifas de venda, ENVIOS e anúncios
    # — exatamente o insumo que falta no motor (frete de devolução por pedido)
    linha = page.locator("tr", has_text="Faturamento do Mercado Livre").first
    with page.expect_download(timeout=20000) as dl_info:
        linha.get_by_text("Baixar", exact=True).click(timeout=5000)
    download = dl_info.value
    dest = DOWNLOAD_DIR / f"faturamento_ml_junho2026_{download.suggested_filename}"
    download.save_as(str(dest))
    print(f"  ✓ BAIXADO: {dest}  ({dest.stat().st_size:,} bytes)")

    # repete para os demais meses — cards vêm em ordem: [0]=Julho(atual, sem detalhe
    # fechado), [1]=Junho(já feito), [2]=Maio, [3]=Abril… "Mostrar mais meses" expande
    for idx, mes_label in ((2, "Maio"), (3, "Abril"), (4, "Marco")):
        try:
            page.goto("https://myaccount.mercadolivre.com.br/billing/resume", timeout=20000)
            page.wait_for_timeout(2500)
            try:
                page.get_by_text("Mostrar mais meses", exact=True).click(timeout=2000)
                page.wait_for_timeout(1500)
            except Exception:
                pass
            botoes = page.get_by_text("Ir para detalhe", exact=True)
            n_antes = len(ctx.pages)
            botoes.nth(idx).click(timeout=5000)
            page.wait_for_timeout(2500)
            p2 = ctx.pages[-1] if len(ctx.pages) > n_antes else page
        except Exception as exc:
            print(f"  ✗ {mes_label} (abrir detalhe) falhou: {type(exc).__name__}: {exc}")
            continue
        p2.wait_for_timeout(2500)
        print(f"  [{mes_label}] URL:", p2.url)
        try:
            p2.get_by_text("Relatórios", exact=True).first.click(timeout=3000)
            p2.wait_for_timeout(2000)
            p2.screenshot(path=str(OUT / f"debug_{mes_label}.png"), full_page=True)
            linha2 = p2.locator("tr", has_text="Faturamento do Mercado Livre").first
            print(f"  [{mes_label}] linha achada:", linha2.count() > 0 if hasattr(linha2, 'count') else '?')
            with p2.expect_download(timeout=20000) as dl2:
                linha2.get_by_text("Baixar", exact=True).click(timeout=5000)
            d2 = dl2.value
            dest2 = DOWNLOAD_DIR / f"faturamento_ml_{mes_label.lower()}2026_{d2.suggested_filename}"
            d2.save_as(str(dest2))
            print(f"  ✓ BAIXADO {mes_label}: {dest2}  ({dest2.stat().st_size:,} bytes)")
        except Exception as exc:
            print(f"  ✗ {mes_label} (baixar) falhou: {type(exc).__name__}: {exc}")
            try:
                p2.screenshot(path=str(OUT / f"debug_falha_{mes_label}.png"), full_page=True)
            except Exception:
                pass
        finally:
            if p2 is not page:
                try:
                    p2.close()
                except Exception:
                    pass

    if True:
        print("  URL após clique:", page.url)
        page.screenshot(path=str(OUT / "01_faturamento.png"), full_page=True)
        body = page.inner_text("body")[:1500]
        print("  conteúdo:", body.replace("\n", " | ")[:900])

        # procura seletor de período e botão de download/relatório
        _achar_e_clicar(("baixar", "download", "relatório", "relatorio", "informe", "extrato", "exportar"))
        page.wait_for_timeout(2000)
        page.screenshot(path=str(OUT / "02_apos_download_click.png"), full_page=True)
        print("  URL final:", page.url)
        print("  conteúdo final:", page.inner_text("body")[:1200].replace("\n", " | "))
    else:
        print("  item 'Faturamento' não encontrado no menu — listando todos os itens visíveis:")
        for el in page.locator("nav a, aside a, [class*=menu] a").all()[:40]:
            try:
                txt = (el.inner_text(timeout=200) or "").strip()
                href = el.get_attribute("href") or ""
                if txt:
                    print(f"    - {txt}  ({href})")
            except Exception:
                pass

    ctx.close()
    pw.stop()
    print(f"\nScreenshots salvos em: {OUT}")


if __name__ == "__main__":
    main()
