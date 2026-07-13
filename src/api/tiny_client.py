"""Read-only Tiny API v2 client.

SECURITY: This module MUST NEVER call incluir/alterar/excluir endpoints.
Only produtos.pesquisa.php and produto.obter.estoque.php are permitted.
Token is read from st.secrets["TINY_TOKEN"] or TINY_TOKEN env var — never hardcoded.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Any

_EAN13_RE = re.compile(r'^\d{13}$')
_SKU_RE   = re.compile(r'^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]{4,20}$')
_NR_RE    = re.compile(r'^NR-?\d+$', re.IGNORECASE)


def is_ean13(code: str | None) -> bool:
    """True when code is exactly 13 digits (GTIN/EAN-13 barcode)."""
    if not code:
        return False
    return bool(_EAN13_RE.match(code.strip()))


def is_nr_code(code: str | None) -> bool:
    """True when code is a Náutica internal code: NR prefix + digits (NR2032, NR-2032)."""
    if not code:
        return False
    return bool(_NR_RE.match(code.strip()))


def normalize_nr(code: str) -> str:
    """Normalize NR code: uppercase + remove hyphen → NR2032."""
    return code.strip().upper().replace("-", "")


def looks_like_sku(code: str | None) -> bool:
    """True when code looks like an internal SKU (letters+digits, 4-20 chars).

    Rejects pure-digit codes (EAN/order ids) and pure-letter codes.
    """
    if not code:
        return False
    code = code.strip()
    if not code or code.isdigit():
        return False
    return bool(_SKU_RE.match(code))


_BASE    = "https://api.tiny.com.br/api2/"
_TIMEOUT   = 4   # seconds — fail fast; was 10s
_CACHE_TTL = 300  # 5 min

_cache: dict[str, tuple[float, Any]] = {}
_MISS = object()


def _token() -> str:
    """Read Tiny token from st.secrets or TINY_TOKEN env var. Never hardcoded."""
    try:
        import streamlit as st
        tok = st.secrets.get("TINY_TOKEN") or ""
        if tok:
            return tok
    except Exception:
        pass
    return os.environ.get("TINY_TOKEN", "")


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _post(endpoint: str, params: dict) -> dict | None:
    body = urllib.parse.urlencode({"token": _token(), "formato": "JSON", **params})
    req  = urllib.request.Request(
        _BASE + endpoint,
        data=body.encode("utf-8"),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# ── cache ─────────────────────────────────────────────────────────────────────

def _cache_get(key: str) -> Any:
    entry = _cache.get(key)
    if entry is None:
        return _MISS
    ts, val = entry
    if time.monotonic() - ts >= _CACHE_TTL:
        del _cache[key]
        return _MISS
    return val


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.monotonic(), value)


def clear_cache() -> None:
    """Flush in-memory cache (useful in tests or after manual stock update)."""
    _cache.clear()


# ── status check ──────────────────────────────────────────────────────────────

def _status_ok(resp: dict | None) -> bool:
    if resp is None:
        return False
    return (resp.get("retorno", {}).get("status", "") or "").upper() == "OK"


# ── parsers ───────────────────────────────────────────────────────────────────

def _parse_produto(raw: dict) -> dict:
    return {
        "id":               str(raw.get("id", "")),
        "nome":             (raw.get("nome") or "").strip(),
        "codigo":           raw.get("codigo", ""),
        "gtin":             raw.get("gtin", ""),
        "preco":            float(raw.get("preco") or 0),
        "preco_custo":      float(raw.get("preco_custo") or 0),
        "preco_custo_medio":float(raw.get("preco_custo_medio") or 0),
        "localizacao":      raw.get("localizacao", "") or "",
        "unidade":          raw.get("unidade", ""),
        "situacao":         raw.get("situacao", ""),
    }


def _parse_estoque(raw: dict) -> dict:
    deps_raw = raw.get("depositos") or []
    depositos: dict[str, dict] = {}
    for entry in deps_raw:
        d = entry.get("deposito", {})
        nome  = d.get("nome", "")
        saldo = int(d.get("saldo") or 0)
        comercial = (d.get("desconsiderar") or "N").upper() != "S"
        depositos[nome] = {"saldo": saldo, "comercial": comercial}

    def _dep(fragment: str) -> int:
        for k, v in depositos.items():
            if fragment.lower() in k.lower():
                return v["saldo"]
        return 0

    saldo_comercial = sum(v["saldo"] for v in depositos.values() if v["comercial"])
    saldo_reserva   = sum(v["saldo"] for v in depositos.values() if not v["comercial"])

    return {
        "id":              str(raw.get("id", "")),
        "nome":            (raw.get("nome") or "").strip(),
        "codigo":          raw.get("codigo", ""),
        "saldo_total":     int(raw.get("saldo") or 0),
        "depositos":       depositos,
        "saldo_comercial": saldo_comercial,
        "saldo_reserva":   saldo_reserva,
        "bau_garantia":    _dep("baú"),
        "loja_fisica":     _dep("loja"),
        "ml_full":         _dep("full"),
    }


def _extract_produtos(resp: dict) -> list[dict]:
    items = resp.get("retorno", {}).get("produtos") or []
    result = []
    for item in items:
        p = item.get("produto") or item
        if isinstance(p, dict):
            result.append(_parse_produto(p))
    return result


# ── public API ────────────────────────────────────────────────────────────────

def lookup_by_gtin(ean: str | None) -> dict | None:
    """Return product dict for an EAN-13/GTIN, or None if not found."""
    if not ean or not str(ean).strip():
        return None
    key = f"gtin:{ean.strip()}"
    cached = _cache_get(key)
    if cached is not _MISS:
        return cached
    resp = _post("produtos.pesquisa.php", {"gtin": ean.strip()})
    if not _status_ok(resp):
        _cache_set(key, None)
        return None
    prods = _extract_produtos(resp)
    result = prods[0] if prods else None
    _cache_set(key, result)
    return result


def lookup_by_sku(codigo: str | None) -> dict | None:
    """Return product dict for exact SKU (case-insensitive), or None."""
    if not codigo or not str(codigo).strip():
        return None
    codigo = codigo.strip()
    key = f"sku:{codigo.upper()}"
    cached = _cache_get(key)
    if cached is not _MISS:
        return cached
    resp = _post("produtos.pesquisa.php", {"pesquisa": codigo})
    if not _status_ok(resp):
        _cache_set(key, None)
        return None
    prods = _extract_produtos(resp)
    match = next(
        (p for p in prods if (p.get("codigo") or "").upper() == codigo.upper()),
        None,
    )
    _cache_set(key, match)
    return match


def get_estoque(produto_id: str | None) -> dict | None:
    """Return stock dict for a Tiny produto_id, or None."""
    if not produto_id or not str(produto_id).strip():
        return None
    key = f"estoque:{produto_id}"
    cached = _cache_get(key)
    if cached is not _MISS:
        return cached
    resp = _post("produto.obter.estoque.php", {"id": str(produto_id)})
    if not _status_ok(resp):
        _cache_set(key, None)
        return None
    raw = resp.get("retorno", {}).get("produto")
    if not raw:
        _cache_set(key, None)
        return None
    result = _parse_estoque(raw)
    _cache_set(key, result)
    return result


def get_produto_foto(produto_id: str | None) -> str | None:
    """Return first photo URL for a product from Tiny, or None.

    Calls produto.obter.php. Result is cached for _CACHE_TTL seconds.
    Designed to run in a background thread — use peek_foto() on the render side.
    """
    if not produto_id or not str(produto_id).strip():
        return None
    key = f"foto:{produto_id}"
    cached = _cache_get(key)
    if cached is not _MISS:
        return cached
    resp = _post("produto.obter.php", {"id": str(produto_id)})
    if not _status_ok(resp):
        _cache_set(key, None)
        return None
    raw = resp.get("retorno", {}).get("produto") or {}
    # Tiny v2 usa "anexos" (não "fotos") para imagens de produto.
    # Cada item: {"anexo": "https://s3.amazonaws.com/tiny-anexos-us/..."}
    anexos = raw.get("anexos") or []
    url = None
    for item in anexos:
        if isinstance(item, dict) and item.get("anexo"):
            url = item["anexo"]
            break
    _cache_set(key, url)
    return url


def peek_foto(produto_id: str | None) -> str | None:
    """Return cached foto URL without making any HTTP call.

    Returns None on cache miss — never blocks. Call after prefetch() warms cache.
    """
    if not produto_id:
        return None
    val = _cache_get(f"foto:{produto_id}")
    return None if val is _MISS else val


def _parse_nf_summary(raw: dict) -> dict:
    cliente = raw.get("cliente") or {}
    return {
        "id":                  str(raw.get("id", "")),
        "tipo":                raw.get("tipo", ""),
        "numero":              str(raw.get("numero", "")),
        "serie":               str(raw.get("serie", "")),
        "data_emissao":        raw.get("data_emissao", ""),
        "cliente_nome":        (raw.get("nome") or "").strip(),
        "cliente_cnpj":        (cliente.get("cpf_cnpj") or "").strip(),
        "valor":               str(raw.get("valor") or "0"),
        "situacao":            str(raw.get("situacao", "")),
        "descricao_situacao":  raw.get("descricao_situacao", ""),
        "chave_acesso":        raw.get("chave_acesso", ""),
        "codigo_rastreamento": raw.get("codigo_rastreamento", "") or "",
        "vendedor":            raw.get("nome_vendedor", ""),
    }


def _parse_nf_item(raw: dict) -> dict:
    return {
        "id_produto":     str(raw.get("id_produto", "")),
        "codigo":         raw.get("codigo", ""),
        "descricao":      (raw.get("descricao") or "").strip(),
        "unidade":        raw.get("unidade", "UN"),
        "quantidade":     float(raw.get("quantidade") or 0),
        "valor_unitario": float(raw.get("valor_unitario") or 0),
        "valor_total":    float(raw.get("valor_total") or 0),
    }


def _parse_nf_full(raw: dict, tipo: str = "") -> dict:
    cliente = raw.get("cliente") or {}
    summary = _parse_nf_summary({
        "id":                  raw.get("id"),
        "tipo":                tipo,
        "numero":              raw.get("numero"),
        "serie":               raw.get("serie"),
        "data_emissao":        raw.get("data_emissao"),
        "nome":                (cliente.get("nome") or "").strip(),
        "cliente":             cliente,
        "valor":               raw.get("valor_nota"),
        "situacao":            raw.get("situacao"),
        "descricao_situacao":  raw.get("descricao_situacao"),
        "chave_acesso":        raw.get("chave_acesso"),
        "codigo_rastreamento": raw.get("codigo_rastreamento"),
        "nome_vendedor":       raw.get("nome_vendedor"),
    })
    itens: list[dict] = []
    for item in (raw.get("itens") or []):
        i = item.get("item", item)
        if isinstance(i, dict):
            itens.append(_parse_nf_item(i))
    return {
        **summary,
        "natureza_operacao": raw.get("natureza_operacao", ""),
        "data_saida":        raw.get("data_saida", ""),
        "valor_produtos":    str(raw.get("valor_produtos") or "0"),
        "valor_frete":       str(raw.get("valor_frete") or "0"),
        "forma_pagamento":   raw.get("forma_pagamento", ""),
        "itens":             itens,
    }


def search_nfs(
    tipo: str = "",
    data_ini: str = "",
    data_fim: str = "",
    numero: str = "",
    pagina: int = 1,
) -> list[dict]:
    """Return list of NF summaries from Tiny.

    tipo: 'S'=saída, 'E'=entrada, ''=qualquer.
    At least one of tipo, data_ini, data_fim, or numero must be provided — API requirement.
    Returns [] when no params given (would fail on API anyway).
    """
    if not any([tipo, data_ini, data_fim, numero]):
        return []
    params: dict[str, str] = {"pagina": str(pagina)}
    if tipo:
        params["tipoNota"] = tipo
    if data_ini:
        params["dataInicial"] = data_ini
    if data_fim:
        params["dataFinal"] = data_fim
    if numero:
        params["numero"] = numero
    resp = _post("notas.fiscais.pesquisa.php", params)
    if not _status_ok(resp):
        return []
    items = resp.get("retorno", {}).get("notas_fiscais") or []
    return [
        _parse_nf_summary(i.get("nota_fiscal", i))
        for i in items
        if isinstance(i, dict)
    ]


def get_nf(nf_id: str | None, tipo: str = "") -> dict | None:
    """Return full NF with items for a Tiny nota_fiscal id, or None."""
    if not nf_id or not str(nf_id).strip():
        return None
    key = f"nf:{nf_id}"
    cached = _cache_get(key)
    if cached is not _MISS:
        return cached
    resp = _post("nota.fiscal.obter.php", {"id": str(nf_id)})
    if not _status_ok(resp):
        _cache_set(key, None)
        return None
    raw = resp.get("retorno", {}).get("nota_fiscal")
    if not raw:
        _cache_set(key, None)
        return None
    result = _parse_nf_full(raw, tipo=tipo)
    _cache_set(key, result)
    return result


def lookup_nf_by_numero(numero: str | None) -> dict | None:
    """Find NF by número, return full NF with items or None.

    Does search_nfs(numero=) → gets id → get_nf(id). Both results cached.
    """
    if not numero or not str(numero).strip():
        return None
    numero = str(numero).strip()
    key = f"nf_num:{numero}"
    cached = _cache_get(key)
    if cached is not _MISS:
        return cached
    nfs = search_nfs(numero=numero)
    if not nfs:
        _cache_set(key, None)
        return None
    summary = nfs[0]
    full = get_nf(summary["id"], tipo=summary["tipo"])
    _cache_set(key, full)
    return full


def search_products(query: str | None) -> list[dict]:
    """Return list of matching products for a free-text query."""
    if not query or not str(query).strip():
        return []
    resp = _post("produtos.pesquisa.php", {"pesquisa": query.strip()})
    if not _status_ok(resp):
        return []
    return _extract_produtos(resp)


def prefetch(code: str) -> None:
    """Warm the cache for `code` (EAN-13 or SKU) including stock.

    Designed to run in a background daemon thread — results go into the
    module-level _cache so the next lookup_by_gtin/get_estoque call is instant.
    Safe to call concurrently: worst case two threads both hit the API once.
    """
    try:
        code = code.strip()
        if is_ean13(code):
            prod = lookup_by_gtin(code)
        elif looks_like_sku(code):
            prod = lookup_by_sku(code)
        else:
            return
        if prod and prod.get("id"):
            get_estoque(prod["id"])
            get_produto_foto(prod["id"])
    except Exception:
        pass  # background — never raise
