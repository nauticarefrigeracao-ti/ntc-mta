"""Camada de conexão com Neon PostgreSQL.

Cada chamada a get_db_connection() cria uma nova conexão.
O PgBouncer do Neon (server-side) gerencia o pool — sem pool client-side necessário.

Segurança:
  - Credenciais NUNCA hardcoded — lidas de st.secrets ou variável de ambiente ML_NEON_URL.
  - channel_binding removido (psycopg2 não suporta); sslmode=require mantém encriptação.
  - connect_timeout=10 para cold start Neon (~300ms).

LGPD: nenhum dado pessoal logado aqui. Erros logados sem conteúdo de rows.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

_neon_dsn: str | None = None


def _get_neon_url() -> str:
    try:
        import streamlit as st
        url = st.secrets.get("ML_NEON_URL") or st.secrets.get("DATABASE_URL")
        if url:
            return url
    except Exception:
        pass
    url = os.environ.get("ML_NEON_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise EnvironmentError(
            "ML_NEON_URL não definida. "
            "Adicione em st.secrets (Streamlit Cloud) ou variável de ambiente."
        )
    return url


def _clean_neon_url(url: str) -> str:
    """Remove channel_binding — psycopg2 não suporta."""
    p = urlparse(url)
    qs = parse_qs(p.query, keep_blank_values=True)
    qs.pop("channel_binding", None)
    return urlunparse(p._replace(query=urlencode({k: v[0] for k, v in qs.items()})))


def _get_dsn() -> str:
    global _neon_dsn
    if _neon_dsn is None:
        _neon_dsn = _clean_neon_url(_get_neon_url())
    return _neon_dsn


def get_db_connection() -> psycopg2.extensions.connection:
    """Cria e retorna uma nova conexão com o Neon.

    Chamar conn.close() ao terminar — o PgBouncer recicla a conexão.
    Preferir o context manager db_conn() para garantir commit/rollback/close.
    """
    return psycopg2.connect(_get_dsn(), connect_timeout=10)


def release_connection(conn: psycopg2.extensions.connection) -> None:
    """Fecha a conexão (devolve ao pool do PgBouncer server-side)."""
    try:
        conn.close()
    except Exception:
        pass


@contextmanager
def db_conn() -> Generator[psycopg2.extensions.connection, None, None]:
    """Context manager: abre conexão, commita ou faz rollback, fecha.

    Uso:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    """
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dict_cursor(conn: psycopg2.extensions.connection):
    """Retorna cursor com RealDictCursor — acesso row['col'] em vez de row[0]."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def table_exists(conn: psycopg2.extensions.connection, table_name: str) -> bool:
    """Verifica existência de tabela no schema public (substitui sqlite_master)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table_name,),
        )
        return cur.fetchone() is not None


def column_exists(conn: psycopg2.extensions.connection, table: str, column: str) -> bool:
    """Verifica existência de coluna (substitui PRAGMA table_info)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
            (table, column),
        )
        return cur.fetchone() is not None


def get_sa_engine():
    """SQLAlchemy engine singleton para pandas to_sql / read_sql."""
    try:
        from sqlalchemy import create_engine
    except ImportError:
        raise ImportError("sqlalchemy não instalado. Adicione ao requirements.txt.")

    sa_url = _get_dsn().replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(
        sa_url,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=3,
        max_overflow=2,
    )
