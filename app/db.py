import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


@contextmanager
def transaction(tenant: str | None):
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant or "",))
        conn.execute("SET LOCAL statement_timeout='15s'")
        yield conn
