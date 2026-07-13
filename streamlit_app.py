"""MTA — Painel de Devoluções (Streamlit Cloud).
=================================================
Renderiza o painel completo lendo TUDO do Neon (pedidos, transações MP,
saldos coletados da plataforma, validações ML API). Nenhum RPA roda aqui —
o cloud só lê o que os processos locais (sync/coleta/polling) mantêm fresco.

Secrets necessários (Settings → Secrets no Streamlit Cloud):
    ML_NEON_URL = "postgresql://…"
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

st.set_page_config(page_title="MTA — Painel de Devoluções", page_icon="📊",
                   layout="wide", initial_sidebar_state="collapsed")


@st.cache_data(ttl=600, show_spinner=False)
def _gerar_html() -> str:
    """Gera o painel a partir do Neon (cache 10 min)."""
    import processar_relatorios_mp as rel
    from src.db.connection import get_db_connection

    conn = get_db_connection()
    try:
        df_mp, df_port = rel._load_and_enrich(conn)
        # resumo da conciliação ML API (badge do topo)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE concorda_ml) FROM mp_validation_results")
                tot, okc = cur.fetchone()
            validation = {"verificados": tot, "concordantes": okc, "divergem": tot - okc,
                          "com_cmv": 0, "pct_paridade": round(okc / tot * 100, 1) if tot else 0.0}
        except Exception:
            validation = None
    finally:
        conn.close()

    kpi = rel._kpis(df_mp, df_port)
    out = Path(tempfile.mkdtemp()) / "painel.html"
    rel._build_html_dashboard(df_mp, df_port, kpi, out, validation=validation)
    return out.read_text(encoding="utf-8")


col1, col2 = st.columns([6, 1])
with col2:
    if st.button("🔄 Atualizar dados", use_container_width=True):
        _gerar_html.clear()

with st.spinner("Carregando painel do Neon (até ~1 min na primeira vez)…"):
    try:
        html = _gerar_html()
    except Exception as exc:
        st.error(f"Falha ao gerar o painel: {type(exc).__name__}: {exc}")
        st.stop()

st.components.v1.html(html, height=2400, scrolling=True)

st.caption(
    "Saldo Final por pedido = 'Total' do detalhe no Meli (fonte: coleta auditada da plataforma; "
    "disputas recentes sem confirmação aparecem como 'em conciliação'). "
    "Sync de pedidos e coleta rodam nos processos locais (ml_live_poll)."
)
