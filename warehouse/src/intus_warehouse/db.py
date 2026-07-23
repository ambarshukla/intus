"""Connecting to the warehouse.

One place that knows how to build a DSN, so no module grows its own opinion
about where the database lives.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

#: Matches infra/docker-compose.yml. Port 5433 rather than the usual 5432
#: because a sibling project on the same machine already binds 5432 — and
#: connecting successfully to the wrong database is a far worse failure than
#: failing to connect at all.
DEFAULT_DSN = "postgresql://intus:intus_local_dev@127.0.0.1:5433/intus"

#: Environment variable that overrides the DSN. CI sets this to point at its
#: service container.
DSN_ENV_VAR = "INTUS_PG_DSN"


def dsn() -> str:
    """The DSN to use, from the environment or the local-dev default."""
    return os.environ.get(DSN_ENV_VAR, DEFAULT_DSN)


@contextmanager
def connect(*, autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """A connection, closed on exit.

    Not autocommit by default: every operation here is either a migration or a
    bulk load, and both want all-or-nothing semantics. A half-applied migration
    is the single worst state a schema can be in.
    """
    with psycopg.connect(dsn(), autocommit=autocommit) as connection:
        yield connection


def server_version(connection: psycopg.Connection) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT version()")
        row = cursor.fetchone()
    return row[0] if row else "unknown"


def is_available() -> bool:
    """Whether a database is reachable — used to skip DB tests, not to branch logic."""
    try:
        with psycopg.connect(dsn(), connect_timeout=2):
            return True
    except psycopg.Error:
        return False
