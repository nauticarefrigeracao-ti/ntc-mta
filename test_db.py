import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from src.db.connection import db_conn

with db_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public'")
        for t,c in cur.fetchall():
            if 'saldo' in c or 'mp' in t or 'mp' in c or 'total' in c:
                print(f'{t}.{c}')
