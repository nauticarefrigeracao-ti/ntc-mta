"""Read-only Mercado Livre API client.

SECURITY:
- NEVER call write endpoints: refund, open_dispute, return_review_ok/fail, send_message.
- Token is read from Neon DB (ML_NEON_URL secret) → st.secrets["ML_ACCESS_TOKEN"] → env var.
- Token MUST NOT appear in exceptions, return values, or public attributes.
- Local dev: set ML_ACCESS_TOKEN as env var (never commit the value).
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
import urllib.error
from typing import Any

_log = logging.getLogger(__name__)

_ML_BASE = "https://api.mercadolibre.com"
_MP_BASE = "https://api.mercadopago.com"

# Module-level token cache: avoids one Neon roundtrip per ML API call during sync.
# Refreshed when empty or within 60 min of expiry (tokens expire in 6h = 21600s).
_TOKEN_CACHE: dict = {"value": "", "expires": 0.0}
_TOKEN_TTL = 18000.0  # cache 5h; tokens expire in 6h


def _clean_neon_url(url: str) -> str:
    """Strip parameters unsupported by psycopg2 over Neon pooler (e.g. channel_binding)."""
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    try:
        p = urlparse(url)
        qs = parse_qs(p.query, keep_blank_values=True)
        qs.pop("channel_binding", None)
        return urlunparse(p._replace(query=urlencode({k: v[0] for k, v in qs.items()})))
    except Exception:
        return url


def _fetch_token_from_neon(neon_url: str) -> str:
    """Query Neon PostgreSQL for the latest ML access token.
    Auto-discovers the table that has an 'accessToken' column. Returns "" on any error."""
    try:
        import psycopg2  # type: ignore[import]
        clean_url = _clean_neon_url(neon_url)
        conn = psycopg2.connect(clean_url, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name FROM information_schema.columns
                WHERE column_name = 'accessToken' AND table_schema = 'public'
                LIMIT 1
                """
            )
            tbl_row = cur.fetchone()
            if not tbl_row:
                conn.close()
                _log.warning("ml_client: Neon conectado mas nenhuma tabela com coluna 'accessToken' encontrada")
                return ""
            table = tbl_row[0]
            cur.execute(
                f'SELECT "accessToken" FROM "{table}" ORDER BY "updatedAt" DESC LIMIT 1'
            )
            row = cur.fetchone()
        conn.close()
        if row and row[0]:
            _log.info("ml_client: token lido do Neon (tabela=%s, len=%d)", table, len(str(row[0])))
            return str(row[0])
        _log.warning("ml_client: Neon tabela=%s sem token", table)
        return ""
    except Exception as exc:
        # Não loga exc direto: psycopg2 inclui DSN (com senha) na mensagem
        _log.warning("ml_client: falha ao conectar Neon: %s", type(exc).__name__)
        return ""


def _token() -> str:
    """Return current ML access token. Priority: Neon DB → st.secrets → env var.
    Result is cached for 5h to avoid repeated Neon roundtrips during bulk sync.
    Never raises, never hardcodes a value."""
    global _TOKEN_CACHE
    now = time.monotonic()
    if _TOKEN_CACHE["value"] and now < _TOKEN_CACHE["expires"]:
        return _TOKEN_CACHE["value"]

    tok = ""

    # 1. Neon PostgreSQL (auto-rotated tokens — always freshest)
    try:
        import streamlit as st
        neon_url = st.secrets.get("ML_NEON_URL") or os.environ.get("ML_NEON_URL", "")
        if neon_url:
            tok = _fetch_token_from_neon(neon_url)
            if tok:
                _log.info("ml_client: token fonte=Neon")
        else:
            _log.warning("ml_client: ML_NEON_URL não configurado")
    except Exception as exc:
        _log.warning("ml_client: erro ao ler ML_NEON_URL: %s", exc)

    # 2. Static secret fallback
    if not tok:
        try:
            import streamlit as st
            tok = st.secrets.get("ML_ACCESS_TOKEN") or ""
            if tok:
                _log.info("ml_client: token fonte=st.secrets[ML_ACCESS_TOKEN]")
        except Exception:
            pass

    # 3. Env var fallback (local dev)
    if not tok:
        tok = os.environ.get("ML_ACCESS_TOKEN", "")
        if tok:
            _log.info("ml_client: token fonte=env ML_ACCESS_TOKEN")

    if not tok:
        _log.error("ml_client: nenhuma fonte de token disponível — sync vai falhar com 401")

    if tok:
        _TOKEN_CACHE = {"value": tok, "expires": now + _TOKEN_TTL}
    return tok


def _get(path: str | None, base: str = _ML_BASE) -> Any | None:
    """GET authenticated request. Returns parsed JSON or None on any error.
    Never raises — all exceptions are swallowed here."""
    if not path:
        return None
    try:
        tok = _token()
        if not tok:
            _log.error("ml_client: token vazio — todas as chamadas vão falhar (verifique Neon/secrets)")
        url = f"{base}{path}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body = ""
        _log.warning("ml_client: HTTP %d para %s | body: %s", exc.code, path.split("?")[0], body)
        # 401/403 = auth failure → invalidate token cache so next call re-fetches
        if exc.code in (401, 403):
            global _TOKEN_CACHE
            _TOKEN_CACHE = {"value": "", "expires": 0.0}
            _log.warning("ml_client: token inválido (HTTP %d) — cache invalidado, próxima chamada re-busca", exc.code)
        return None
    except Exception as exc:
        _log.warning("ml_client: erro em GET %s: %s", path.split("?")[0], type(exc).__name__)
        return None


def _non_empty(result: Any) -> Any | None:
    """Return None if result is falsy or an empty dict."""
    if result is None:
        return None
    if isinstance(result, dict) and not result:
        return None
    return result


# ─── Public read-only functions ───────────────────────────────────────────────
# Each wraps _get in try/except so external mocks that raise still return None.

def get_claim(claim_id: int | str | None) -> dict | None:
    if not claim_id and claim_id != 0:
        return None
    if isinstance(claim_id, str) and not claim_id.strip():
        return None
    try:
        return _non_empty(_get(f"/post-purchase/v1/claims/{claim_id}"))
    except Exception:
        return None


def get_return(claim_id: int | str | None) -> dict | None:
    """GET /post-purchase/v2/claims/{id}/returns — return entity (reverse logistics).

    Returns the return object linked to a claim, including:
    - id: return_id
    - type: "claim" | "dispute" | "automatic" (ML auto-resolved)
    - status: return status
    - shipment_id: reverse-logistics shipment (different from sale shipment)
    Read-only. Never mutates state.
    """
    if not claim_id and claim_id != 0:
        return None
    if isinstance(claim_id, str) and not claim_id.strip():
        return None
    try:
        return _non_empty(_get(f"/post-purchase/v2/claims/{claim_id}/returns"))
    except Exception:
        return None


def get_order(order_id: int | str | None) -> dict | None:
    if not order_id and order_id != 0:
        return None
    if isinstance(order_id, str) and not order_id.strip():
        return None
    try:
        return _non_empty(_get(f"/orders/{order_id}"))
    except Exception:
        return None


def get_item(item_id: str | None) -> dict | None:
    """GET /items/{item_id} — dados do anúncio (thumbnail, pictures, price).

    Read-only. Usado para a miniatura do produto na fila de triagem.
    """
    if not item_id or not str(item_id).strip():
        return None
    try:
        return _non_empty(_get(f"/items/{item_id}"))
    except Exception:
        return None


def get_orders_page(seller_id: str | None, offset: int = 0, limit: int = 50) -> dict | None:
    if not seller_id:
        return None
    try:
        return _get(f"/orders/search?seller={seller_id}&offset={offset}&limit={limit}")
    except Exception:
        return None


def get_claims_page(
    seller_id: str | None,
    offset: int = 0,
    limit: int = 50,
    claim_type: str = "mediations",
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict | None:
    """Paginate /post-purchase/v1/claims/search.
    type=mediations (default) returns buyer-opened return/refund claims.
    status="opened" filters to active claims only (≈43 records — safe for polling).
    date_from/date_to: ISO-8601 strings (e.g. "2023-01-01T00:00:00.000-03:00") for
    date_created_from/date_created_to — required to bypass ML's 10K offset cap.
    Response: {"data": [...], "paging": {"total": N, "offset": N, "limit": N}}
    """
    if not seller_id:
        return None
    try:
        qs = f"?seller_id={seller_id}&type={claim_type}&offset={offset}&limit={limit}"
        if status:
            qs += f"&status={status}"
        if date_from:
            qs += f"&date_created_from={date_from}"
        if date_to:
            qs += f"&date_created_to={date_to}"
        return _get(f"/post-purchase/v1/claims/search{qs}")
    except Exception:
        return None


def get_returns_page(
    seller_id: str | None,
    offset: int = 0,
    limit: int = 50,
    return_type: str | None = None,
) -> dict | None:
    """GET /post-purchase/v2/returns/search — list return entities for seller.

    return_type='automatic' filters ML-initiated headless returns (no formal claim).
    Response: {"data": [...], "paging": {"total": N, "offset": N, "limit": N}}
    Returns None on any error — endpoint may not be available for all sellers.
    Read-only.
    """
    if not seller_id:
        return None
    try:
        qs = f"?seller_id={seller_id}&offset={offset}&limit={limit}"
        if return_type:
            qs += f"&type={return_type}"
        return _get(f"/post-purchase/v2/returns/search{qs}")
    except Exception:
        return None


def get_shipment(shipment_id: int | str | None) -> dict | None:
    if not shipment_id and shipment_id != 0:
        return None
    if isinstance(shipment_id, str) and not shipment_id.strip():
        return None
    try:
        return _non_empty(_get(f"/shipments/{shipment_id}"))
    except Exception:
        return None


def get_payment(payment_id: int | str | None) -> dict | None:
    if not payment_id and payment_id != 0:
        return None
    if isinstance(payment_id, str) and not payment_id.strip():
        return None
    try:
        return _non_empty(_get(f"/v1/payments/{payment_id}", base=_MP_BASE))
    except Exception:
        return None


def get_seller_reputation(seller_id: str | None) -> dict | None:
    if not seller_id:
        return None
    try:
        return _non_empty(_get(f"/users/{seller_id}"))
    except Exception:
        return None
