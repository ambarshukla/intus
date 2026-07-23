"""Running the repeatable transforms that build the warehouse from staging.

Separate from the migration runner, because transforms and migrations are
different things that only look alike. A migration changes *structure*, runs
once, and is checksummed so it can never change afterwards. A transform changes
*data*, runs on every load, and must be idempotent — rerunning it on unchanged
staging must leave the warehouse exactly as it was. Conflating them produces
either migrations nobody dares re-run or transforms that silently apply twice.

The whole set runs in one transaction. A half-built star schema — dimensions
updated, facts not — is a state no report should ever be able to observe.

Each run gets a row in ``warehouse.transform_run``, and its id is published to
the SQL through a session setting (``intus.run_id``) rather than by string
interpolation. That keeps the transform files parameter-free and readable, and
means no code path splices a value into SQL text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg

TRANSFORM_DIR = Path(__file__).resolve().parents[2] / "transform"

_FILENAME = re.compile(r"^(\d{2})_([a-z0-9_]+)\.sql$")

#: Session setting the transform SQL reads via current_setting().
RUN_ID_SETTING = "intus.run_id"


class TransformError(RuntimeError):
    """A transform file is malformed, or the transform set failed to run."""


@dataclass(frozen=True, slots=True)
class TransformStep:
    order: str
    name: str
    path: Path
    sql: str


@dataclass(frozen=True, slots=True)
class TransformResult:
    run_id: int
    steps: tuple[str, ...]
    exceptions: int


def discover(transform_dir: Path | None = None) -> tuple[TransformStep, ...]:
    """Every transform on disk, in execution order."""
    directory = transform_dir or TRANSFORM_DIR
    if not directory.is_dir():
        raise TransformError(f"transform directory not found: {directory}")

    steps: list[TransformStep] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if match is None:
            raise TransformError(f"{path.name}: transforms must be named NN_lower_snake_case.sql")
        order, name = match.groups()
        steps.append(
            TransformStep(order=order, name=name, path=path, sql=path.read_text(encoding="utf-8"))
        )

    if not steps:
        raise TransformError(f"no transform files in {directory}")
    return tuple(steps)


def _source_metadata(connection: psycopg.Connection) -> tuple[int | None, str | None, date | None]:
    """Which extract is currently in staging, from the most recent load audit."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT manifest_seed, manifest_scale, as_of_date
            FROM staging.load_audit
            ORDER BY load_id DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
    return row if row else (None, None, None)


def run(connection: psycopg.Connection, transform_dir: Path | None = None) -> TransformResult:
    """Execute every transform in order, in a single transaction."""
    steps = discover(transform_dir)
    seed, scale, as_of = _source_metadata(connection)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO warehouse.transform_run (source_seed, source_scale, source_as_of)
            VALUES (%s, %s, %s)
            RETURNING run_id
            """,
            (seed, scale, as_of),
        )
        run_id = cursor.fetchone()[0]
    connection.commit()

    try:
        with connection.transaction(), connection.cursor() as cursor:
            # SET LOCAL so the setting is scoped to this transaction and
            # cannot leak into a later one on a pooled connection.
            cursor.execute(f"SET LOCAL {RUN_ID_SETTING} = '{run_id}'")
            for step in steps:
                cursor.execute(step.sql)

            cursor.execute(
                "SELECT count(*) FROM warehouse.dq_exception WHERE run_id = %s", (run_id,)
            )
            exceptions = cursor.fetchone()[0]

            cursor.execute(
                """
                UPDATE warehouse.transform_run
                SET finished_at = now(), status = 'succeeded'
                WHERE run_id = %s
                """,
                (run_id,),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        # Recorded outside the failed transaction, or the record would roll
        # back with it and the failure would leave no trace at all.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE warehouse.transform_run
                SET finished_at = now(), status = 'failed'
                WHERE run_id = %s
                """,
                (run_id,),
            )
        connection.commit()
        raise

    return TransformResult(
        run_id=run_id,
        steps=tuple(f"{step.order}_{step.name}" for step in steps),
        exceptions=exceptions,
    )
