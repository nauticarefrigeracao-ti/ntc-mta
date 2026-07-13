"""Ingestão de relatórios Mercado Pago (after_collection) no Neon PostgreSQL.

Uso standalone:
    from src.services.mp_ingestion import ingest_file, scan_folder

Tabelas criadas automaticamente:
    mp_transactions  — cada linha do relatório MP (upsert por id_transacao)
    mp_import_log    — rastreia quais arquivos já foram importados
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import pandas as pd
import psycopg2

from src.db.connection import get_db_connection

_log = logging.getLogger(__name__)

# ── Buckets de negócio ────────────────────────────────────────────────────────
BUCKET_MAP: dict[str, str] = {
    "bpp_refunded":            "Protegido ML",
    "bpp_covered":             "Protegido ML",
    "partially_bpp_refunded":  "Protegido ML",
    "partially_bpp_covered":   "Protegido ML",
    "ppv_covered_melienvio":   "Protegido ML",
    "reconciled":              "Mediação ML",
    "compensated":             "Mediação ML",
    "not_reconciled":          "Perda Confirmada",
    "refunded":                "Reembolso Direto",
    "by_admin":                "Administrativo",
}

# Mapeamento de colunas do relatório MP → nomes internos
_COL_MAP: dict[str, str] = {
    "Fluxo (flow)":                              "fluxo",
    "Data de criação (date_created)":            "data_criacao",
    "ID (id)":                                   "id_transacao",
    "ID do item (item_id)":                      "item_id",
    "ID do motivo (reason_id)":                  "reason_id",
    "Status (status)":                           "status",
    "Detalhe do status (status_detail)":         "status_detail",
    "Valor (amount)":                            "valor",
    "Nome da contraparte (counterpart_name)":    "contraparte",
    "E-mail da contraparte (counterpart_email)": "contraparte_email",
    "ID do pedido (order_id)":                   "order_id",
    "Valor da transação (operation_amount)":     "valor_operacao",
    "Status da transação (operation_status)":    "status_operacao",
    "Tipo de transação (operation_type)":        "tipo_operacao",
}

# ── DDL ───────────────────────────────────────────────────────────────────────
_DDL_TRANSACTIONS = """
CREATE TABLE IF NOT EXISTS mp_transactions (
    id              BIGSERIAL PRIMARY KEY,
    source_file     TEXT        NOT NULL,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fluxo           TEXT,
    data_criacao    TEXT,
    id_transacao    BIGINT,
    item_id         TEXT,
    reason_id       TEXT,
    status          TEXT,
    status_detail   TEXT,
    categoria       TEXT,
    valor           NUMERIC(14,2),
    order_id        BIGINT,
    valor_operacao  NUMERIC(14,2),
    status_operacao TEXT,
    tipo_operacao   TEXT,
    contraparte     TEXT,
    UNIQUE (id_transacao)
)
"""

_DDL_LOG = """
CREATE TABLE IF NOT EXISTS mp_import_log (
    id            BIGSERIAL PRIMARY KEY,
    filename      TEXT        NOT NULL UNIQUE,
    imported_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rows_total    INTEGER,
    rows_new      INTEGER,
    rows_dup      INTEGER,
    periodo_inicio TEXT,
    periodo_fim    TEXT
)
"""

_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_mp_tx_order_id     ON mp_transactions (order_id)",
    "CREATE INDEX IF NOT EXISTS idx_mp_tx_status_detail ON mp_transactions (status_detail)",
    "CREATE INDEX IF NOT EXISTS idx_mp_tx_data_criacao  ON mp_transactions (data_criacao)",
]


def _ensure_tables(conn: psycopg2.extensions.connection) -> None:
    with conn.cursor() as cur:
        cur.execute(_DDL_TRANSACTIONS)
        cur.execute(_DDL_LOG)
        for idx in _IDX:
            cur.execute(idx)
    conn.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────
_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})")


def _parse_mp_date(s: str | None) -> str | None:
    """Converte 'DD/MM/YYYY HH:MM' → 'YYYY-MM-DD HH:MM' (ISO). Retorna None se inválido."""
    if not s or not isinstance(s, str):
        return None
    m = _DATE_RE.match(s.strip())
    if not m:
        return s  # retorna como está
    d, mo, y, hh, mm = m.groups()
    return f"{y}-{mo}-{d} {hh}:{mm}:00"


def _safe_bigint(v) -> int | None:
    try:
        if v is None or (isinstance(v, float) and v != v):
            return None
        return int(float(str(v).strip()))
    except Exception:
        return None


def _safe_numeric(v) -> float | None:
    try:
        if v is None or (isinstance(v, float) and v != v):
            return None
        s = str(v).strip().replace(",", ".").replace("R$", "").replace("\xa0", "")
        return float(s)
    except Exception:
        return None


# ── Parse do arquivo MP ───────────────────────────────────────────────────────
def _parse_mp_file(path: Path) -> pd.DataFrame:
    """Lê um arquivo after_collection (Excel ou CSV) e retorna DataFrame normalizado."""
    ext = path.suffix.lower()
    try:
        if ext in (".xlsx", ".xls"):
            # xlsx do MP tem stylesheet que quebra o openpyxl — calamine como fallback
            try:
                df = pd.read_excel(path, dtype=str)
            except Exception:
                df = pd.read_excel(path, dtype=str, engine="calamine")
        else:
            for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
                try:
                    df = pd.read_csv(path, dtype=str, encoding=enc)
                    break
                except Exception:
                    continue
            else:
                raise ValueError(f"Não foi possível decodificar {path.name}")
    except Exception as exc:
        raise ValueError(f"Erro ao ler {path.name}: {exc}") from exc

    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={k: v for k, v in _COL_MAP.items() if k in df.columns})

    # Remover apenas linhas sem NENHUMA chave: reembolsos diretos ('refunded')
    # vêm sem order_id no relatório MP, mas têm id_transacao — devem entrar.
    oid = df["order_id"].fillna("").astype(str).str.strip() if "order_id" in df.columns else pd.Series("", index=df.index)
    itx = df["id_transacao"].fillna("").astype(str).str.strip() if "id_transacao" in df.columns else pd.Series("", index=df.index)
    df = df[(oid != "") | (itx != "")]

    return df


# ── Upsert de um arquivo ──────────────────────────────────────────────────────
def ingest_file(
    path: str | Path,
    conn: psycopg2.extensions.connection | None = None,
    force: bool = False,
) -> dict:
    """Ingere um arquivo after_collection no Neon.

    Parâmetros
    ----------
    path  : caminho para o arquivo .xlsx / .csv
    conn  : conexão psycopg2 (opcional – cria uma nova se não fornecida)
    force : se True, reimporta mesmo que o arquivo já esteja no log

    Retorna
    -------
    dict com: filename, rows_total, rows_new, rows_dup, ja_importado
    """
    path = Path(path)
    close_conn = conn is None
    if conn is None:
        conn = get_db_connection()

    try:
        _ensure_tables(conn)

        # Verificar se já foi importado
        with conn.cursor() as cur:
            cur.execute("SELECT id, rows_new FROM mp_import_log WHERE filename = %s", (path.name,))
            prev = cur.fetchone()

        if prev and not force:
            _log.info("mp_ingestion: %s já importado (id=%s, rows_new=%s) – ignorado", path.name, prev[0], prev[1])
            return {"filename": path.name, "rows_total": 0, "rows_new": 0, "rows_dup": 0, "ja_importado": True}

        # Parse
        df = _parse_mp_file(path)
        rows_total = len(df)

        if rows_total == 0:
            _log.warning("mp_ingestion: %s sem linhas úteis após parse", path.name)
            return {"filename": path.name, "rows_total": 0, "rows_new": 0, "rows_dup": 0, "ja_importado": False}

        # Período
        data_col = "data_criacao" if "data_criacao" in df.columns else None
        periodo_inicio = periodo_fim = None
        if data_col:
            datas = df[data_col].dropna().tolist()
            if datas:
                periodo_inicio = min(datas)
                periodo_fim    = max(datas)

        rows_new = 0
        rows_dup = 0

        with conn.cursor() as cur:
            for _, row in df.iterrows():
                id_tx   = _safe_bigint(row.get("id_transacao"))
                oid     = _safe_bigint(row.get("order_id"))
                valor   = _safe_numeric(row.get("valor"))
                v_op    = _safe_numeric(row.get("valor_operacao"))
                sd      = str(row.get("status_detail") or "").strip()
                cat     = BUCKET_MAP.get(sd, "Outro")
                dc      = _parse_mp_date(str(row.get("data_criacao") or ""))

                cur.execute(
                    """
                    INSERT INTO mp_transactions
                        (source_file, fluxo, data_criacao, id_transacao, item_id,
                         reason_id, status, status_detail, categoria, valor,
                         order_id, valor_operacao, status_operacao, tipo_operacao, contraparte)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (id_transacao) DO NOTHING
                    """,
                    (
                        path.name,
                        str(row.get("fluxo") or ""),
                        dc,
                        id_tx,
                        str(row.get("item_id") or ""),
                        str(row.get("reason_id") or ""),
                        str(row.get("status") or ""),
                        sd,
                        cat,
                        valor,
                        oid,
                        v_op,
                        str(row.get("status_operacao") or ""),
                        str(row.get("tipo_operacao") or ""),
                        str(row.get("contraparte") or ""),
                    ),
                )
                if cur.rowcount > 0:
                    rows_new += 1
                else:
                    rows_dup += 1

        # Registrar no log
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mp_import_log (filename, rows_total, rows_new, rows_dup, periodo_inicio, periodo_fim)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (filename) DO UPDATE SET
                    imported_at    = NOW(),
                    rows_total     = EXCLUDED.rows_total,
                    rows_new       = EXCLUDED.rows_new,
                    rows_dup       = EXCLUDED.rows_dup,
                    periodo_inicio = EXCLUDED.periodo_inicio,
                    periodo_fim    = EXCLUDED.periodo_fim
                """,
                (path.name, rows_total, rows_new, rows_dup, periodo_inicio, periodo_fim),
            )
        conn.commit()

        _log.info("mp_ingestion: %s → total=%d  novos=%d  dup=%d", path.name, rows_total, rows_new, rows_dup)
        return {
            "filename":    path.name,
            "rows_total":  rows_total,
            "rows_new":    rows_new,
            "rows_dup":    rows_dup,
            "ja_importado": False,
        }

    except Exception as exc:
        conn.rollback()
        _log.error("mp_ingestion: erro em %s: %s", path.name, exc)
        raise
    finally:
        if close_conn:
            conn.close()


# ── Relatório COLLECTION (vendas) — conciliação de reembolsos ────────────────
# Fonte da verdade offline para: quanto foi devolvido ao comprador
# (amount_refunded) e o estado do pagamento (accredited/refunded).
# LGPD: colunas de dados pessoais do comprador (nome, e-mail, CPF, telefone,
# endereço) são deliberadamente IGNORADAS — só campos financeiros e ids.
_DDL_COLLECTION = """
CREATE TABLE IF NOT EXISTS mp_collection (
    id                 BIGSERIAL PRIMARY KEY,
    source_file        TEXT        NOT NULL,
    imported_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    operation_id       BIGINT,
    data_compra        TEXT,
    order_id           BIGINT,
    sku                TEXT,
    status             TEXT,
    status_detail      TEXT,
    tipo_operacao      TEXT,
    valor_produto      NUMERIC(14,2),
    tarifa_mp          NUMERIC(14,2),
    tarifa_marketplace NUMERIC(14,2),
    frete              NUMERIC(14,2),
    valor_recebido     NUMERIC(14,2),
    valor_devolvido    NUMERIC(14,2),
    claim_id           TEXT,
    UNIQUE (operation_id)
)
"""

_COLLECTION_COL_MAP: dict[str, str] = {
    "Data da compra (date_created)":                        "data_compra",
    "Número da transação do Mercado Pago (operation_id)":   "operation_id",
    "Status da operação (status)":                          "status",
    "Detalhe do status da operação (status_detail)":        "status_detail",
    "Tipo de operação (operation_type)":                    "tipo_operacao",
    "Valor do produto (transaction_amount)":                "valor_produto",
    "Tarifa do Mercado Pago (mercadopago_fee)":             "tarifa_mp",
    "Tarifa pelo uso da plataforma de terceiros (marketplace_fee)": "tarifa_marketplace",
    "Frete (shipping_cost)":                                "frete",
    "Valor total recebido (net_received_amount)":           "valor_recebido",
    "Valor devolvido (amount_refunded)":                    "valor_devolvido",
    "Número da reclamação (claim_id)":                      "claim_id",
    "Número da venda no Mercado Livre (order_id)":          "order_id",
    "SKU do produto (seller_custom_field)":                 "sku",
}


def ingest_collection_file(
    path: str | Path,
    conn: psycopg2.extensions.connection | None = None,
    force: bool = False,
) -> dict:
    """Ingere um relatório collection (vendas) do MP na tabela mp_collection."""
    path = Path(path)
    close_conn = conn is None
    if conn is None:
        conn = get_db_connection()

    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(_DDL_COLLECTION)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_mp_coll_order_id ON mp_collection (order_id)")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT id FROM mp_import_log WHERE filename = %s", (path.name,))
            prev = cur.fetchone()
        if prev and not force:
            return {"filename": path.name, "rows_total": 0, "rows_new": 0, "rows_dup": 0, "ja_importado": True}

        try:
            df = pd.read_excel(path, dtype=str)
        except Exception:
            df = pd.read_excel(path, dtype=str, engine="calamine")
        df.columns = [c.strip() for c in df.columns]
        df = df.rename(columns={k: v for k, v in _COLLECTION_COL_MAP.items() if k in df.columns})
        if "order_id" in df.columns:
            df = df.dropna(subset=["order_id"])
            df = df[df["order_id"].astype(str).str.strip() != ""]

        datas = df["data_compra"].dropna().tolist() if "data_compra" in df.columns else []

        # Batch insert (execute_values) — 21k linhas em ~20 roundtrips ao Neon,
        # em vez de 21k. Linhas sem operation_id (money_transfer etc.) são puladas:
        # não têm chave de dedupe nem vínculo com pedido.
        registros = []
        for _, row in df.iterrows():
            op_id = _safe_bigint(row.get("operation_id"))
            if op_id is None:
                continue
            registros.append((
                path.name,
                op_id,
                _parse_mp_date(str(row.get("data_compra") or "")),
                _safe_bigint(row.get("order_id")),
                str(row.get("sku") or ""),
                str(row.get("status") or ""),
                str(row.get("status_detail") or ""),
                str(row.get("tipo_operacao") or ""),
                _safe_numeric(row.get("valor_produto")),
                _safe_numeric(row.get("tarifa_mp")),
                _safe_numeric(row.get("tarifa_marketplace")),
                _safe_numeric(row.get("frete")),
                _safe_numeric(row.get("valor_recebido")),
                _safe_numeric(row.get("valor_devolvido")),
                str(row.get("claim_id") or ""),
            ))

        from psycopg2.extras import execute_values
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM mp_collection")
            antes = cur.fetchone()[0]
            execute_values(
                cur,
                """
                INSERT INTO mp_collection
                    (source_file, operation_id, data_compra, order_id, sku, status,
                     status_detail, tipo_operacao, valor_produto, tarifa_mp,
                     tarifa_marketplace, frete, valor_recebido, valor_devolvido, claim_id)
                VALUES %s
                ON CONFLICT (operation_id) DO UPDATE SET
                    status          = EXCLUDED.status,
                    status_detail   = EXCLUDED.status_detail,
                    valor_recebido  = EXCLUDED.valor_recebido,
                    valor_devolvido = EXCLUDED.valor_devolvido,
                    source_file     = EXCLUDED.source_file,
                    imported_at     = NOW()
                """,
                registros,
                page_size=1000,
            )
            cur.execute("SELECT COUNT(*) FROM mp_collection")
            rows_new = cur.fetchone()[0] - antes
            rows_dup = len(registros) - rows_new
            cur.execute(
                """
                INSERT INTO mp_import_log (filename, rows_total, rows_new, rows_dup, periodo_inicio, periodo_fim)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (filename) DO UPDATE SET
                    imported_at = NOW(), rows_total = EXCLUDED.rows_total,
                    rows_new = EXCLUDED.rows_new, rows_dup = EXCLUDED.rows_dup
                """,
                (path.name, len(df), rows_new, rows_dup,
                 min(datas) if datas else None, max(datas) if datas else None),
            )
        conn.commit()
        return {"filename": path.name, "rows_total": len(df), "rows_new": rows_new,
                "rows_dup": rows_dup, "ja_importado": False, "tipo": "collection"}
    except Exception as exc:
        conn.rollback()
        _log.error("mp_ingestion: erro em %s: %s", path.name, exc)
        raise
    finally:
        if close_conn:
            conn.close()


# ── Varredura de pasta ────────────────────────────────────────────────────────
_MP_PATTERN = re.compile(r"after_collection.*\.(xlsx?|csv)$", re.IGNORECASE)
_COLLECTION_PATTERN = re.compile(r"^collection.*\.(xlsx?|csv)$", re.IGNORECASE)


def scan_folder(
    folder: str | Path,
    conn: psycopg2.extensions.connection | None = None,
    force: bool = False,
    extra_patterns: Sequence[str] | None = None,
) -> list[dict]:
    """Varre uma pasta em busca de relatórios MP e ingere os não-importados.

    Parâmetros
    ----------
    folder          : pasta com os arquivos
    conn            : conexão psycopg2 (opcional)
    force           : reimportar mesmo arquivos já logados
    extra_patterns  : padrões regex adicionais de filename (além de after_collection*)

    Retorna
    -------
    lista de dicts com resultado por arquivo (filename, rows_new, rows_dup…)
    """
    folder = Path(folder)
    if not folder.exists():
        raise FileNotFoundError(f"Pasta não encontrada: {folder}")

    patterns = [_MP_PATTERN]
    if extra_patterns:
        patterns += [re.compile(p, re.IGNORECASE) for p in extra_patterns]

    arquivos = sorted(
        f for f in folder.iterdir()
        if f.is_file() and any(p.search(f.name) for p in patterns)
    )

    tem_collection = any(
        f.is_file() and _COLLECTION_PATTERN.match(f.name) for f in folder.iterdir()
    )
    if not arquivos and not tem_collection:
        _log.info("mp_ingestion.scan_folder: nenhum arquivo MP encontrado em %s", folder)
        return []

    close_conn = conn is None
    if conn is None:
        conn = get_db_connection()

    try:
        resultados = []
        for arq in arquivos:
            try:
                r = ingest_file(arq, conn=conn, force=force)
                resultados.append(r)
            except Exception as exc:
                _log.error("mp_ingestion: falha em %s: %s", arq.name, exc)
                resultados.append({"filename": arq.name, "erro": str(exc)})

        # relatórios collection (vendas) — nome começa com 'collection'
        for arq in sorted(folder.iterdir()):
            if arq.is_file() and _COLLECTION_PATTERN.match(arq.name):
                try:
                    resultados.append(ingest_collection_file(arq, conn=conn, force=force))
                except Exception as exc:
                    _log.error("mp_ingestion: falha em %s: %s", arq.name, exc)
                    resultados.append({"filename": arq.name, "erro": str(exc)})
        return resultados
    finally:
        if close_conn:
            conn.close()


# ── Lista de arquivos importados ──────────────────────────────────────────────
def list_imported(conn: psycopg2.extensions.connection | None = None) -> pd.DataFrame:
    """Retorna DataFrame com todos os arquivos já importados."""
    close_conn = conn is None
    if conn is None:
        conn = get_db_connection()
    try:
        _ensure_tables(conn)
        return pd.read_sql(
            "SELECT filename, imported_at, rows_total, rows_new, rows_dup, periodo_inicio, periodo_fim "
            "FROM mp_import_log ORDER BY imported_at DESC",
            conn,
        )
    finally:
        if close_conn:
            conn.close()
