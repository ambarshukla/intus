"""A minimal forward-only SQL migration runner.

Roughly a hundred lines standing in for Flyway or Alembic, and that is a
deliberate trade. The alternatives are better tools, but this phase exists to
demonstrate SQL and warehouse design; a migration framework would add a
dependency, a configuration file and a vocabulary without changing a single
line of the SQL that actually matters. The rules it enforces are the three that
make migrations trustworthy:

**Ordered and recorded.** Files are ``NNN_name.sql``, applied in numeric order,
each recorded in ``public.schema_migration``. Applying twice is a no-op.

**Checksummed.** The SHA-256 of each file is stored on application. Editing a
migration that has already run is an error, not a silent divergence — the
schema in front of you would no longer match the file that claims to have built
it, and every environment would drift differently.

**Transactional.** Each migration runs in its own transaction. Postgres has
transactional DDL, so a migration that fails halfway leaves nothing behind.
That is the property that makes a half-applied schema unrepresentable rather
than merely unlikely.

Forward-only, with no ``down`` scripts. Down-migrations are reassuring and
rarely correct: the interesting failures involve data, which a schema rollback
cannot restore. Rolling forward with a new migration is the honest fix.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"

_FILENAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")

_MIGRATION_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS public.schema_migration (
    version     text        PRIMARY KEY,
    name        text        NOT NULL,
    checksum    text        NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """A migration is malformed, or the recorded history disagrees with the files."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    name: str
    path: Path
    sql: str

    @property
    def checksum(self) -> str:
        # Newlines normalised before hashing: the repo stores LF, but a Windows
        # checkout with autocrlf would otherwise produce a different checksum
        # for a byte-identical migration and report tampering that never
        # happened.
        return hashlib.sha256(self.sql.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def discover(sql_dir: Path | None = None) -> tuple[Migration, ...]:
    """Every migration on disk, in application order."""
    directory = sql_dir or SQL_DIR
    if not directory.is_dir():
        raise MigrationError(f"migration directory not found: {directory}")

    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if match is None:
            raise MigrationError(f"{path.name}: migrations must be named NNN_lower_snake_case.sql")
        version, name = match.groups()
        migrations.append(
            Migration(
                version=version,
                name=name,
                path=path,
                sql=path.read_text(encoding="utf-8"),
            )
        )

    versions = [migration.version for migration in migrations]
    duplicates = sorted({v for v in versions if versions.count(v) > 1})
    if duplicates:
        raise MigrationError(f"duplicate migration version(s): {duplicates}")

    return tuple(migrations)


def applied(connection: psycopg.Connection) -> dict[str, str]:
    """Version → checksum for everything already applied."""
    with connection.cursor() as cursor:
        cursor.execute(_MIGRATION_TABLE_DDL)
        cursor.execute("SELECT version, checksum FROM public.schema_migration")
        return dict(cursor.fetchall())


def pending(connection: psycopg.Connection, sql_dir: Path | None = None) -> tuple[Migration, ...]:
    """Migrations not yet applied, verifying that applied ones are unchanged."""
    on_disk = discover(sql_dir)
    already = applied(connection)

    for migration in on_disk:
        recorded = already.get(migration.version)
        if recorded is not None and recorded != migration.checksum:
            raise MigrationError(
                f"{migration.path.name} has changed since it was applied "
                f"(recorded {recorded[:12]}, now {migration.checksum[:12]}). "
                "Migrations are immutable once applied; add a new one instead."
            )

    unknown = sorted(set(already) - {migration.version for migration in on_disk})
    if unknown:
        raise MigrationError(
            f"database has migration(s) with no file: {unknown}. "
            "The database is ahead of this checkout."
        )

    return tuple(migration for migration in on_disk if migration.version not in already)


def run(connection: psycopg.Connection, sql_dir: Path | None = None) -> tuple[Migration, ...]:
    """Apply every pending migration, returning those applied.

    Each migration is committed before the next begins. The explicit
    ``commit()`` is load-bearing and easy to omit: on a non-autocommit
    connection ``connection.transaction()`` opens a *savepoint* inside the
    surrounding transaction rather than a transaction of its own, so without
    it every migration would be one uncommitted unit. A failure in the last
    migration would then roll back all the earlier ones, and the "each
    migration is atomic" property would be exactly backwards — the whole run
    would be atomic instead, which is the thing transactional DDL is supposed
    to save you from.
    """
    to_apply = pending(connection, sql_dir)

    # Make the bookkeeping table itself durable before relying on it.
    connection.commit()

    for migration in to_apply:
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(migration.sql)
            cursor.execute(
                "INSERT INTO public.schema_migration (version, name, checksum) VALUES (%s, %s, %s)",
                (migration.version, migration.name, migration.checksum),
            )
        connection.commit()

    return to_apply
