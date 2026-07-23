"""The run manifest: what was generated, and proof it can be generated again.

Written alongside the data as ``manifest.json``, recording the inputs (seed,
scale, as-of date), every output file with its SHA-256, and the full
ground-truth list of injected defects.

**The manifest deliberately contains no wall-clock time and no machine
details.** The temptation is to stamp it with ``generated_at`` and the Python
version, as most build metadata does. Doing so would make the manifest differ
between two otherwise identical runs, and the whole point of the file is that
it does *not*: given the same inputs, the manifest is byte-identical, so
regenerability can be checked by comparing manifests rather than by
re-reading gigabytes of CSV. A field that changes every run would quietly cost
that property to record something the shell already knows.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from intus_gen import __version__
from intus_gen.defects import Injection
from intus_gen.emit import WrittenFile
from intus_gen.sensitivity import Dataset, Tier

MANIFEST_FILENAME = "manifest.json"


@dataclass(frozen=True, slots=True)
class RunManifest:
    generator_version: str
    seed: int
    scale: str
    as_of_date: date
    start_date: date
    defects_enabled: bool
    files: tuple[WrittenFile, ...]
    injections: tuple[Injection, ...]
    classification: tuple[dict[str, object], ...]

    @property
    def total_rows(self) -> int:
        return sum(file.rows for file in self.files)

    def to_dict(self) -> dict[str, object]:
        return {
            "generator_version": self.generator_version,
            "seed": self.seed,
            "scale": self.scale,
            "as_of_date": self.as_of_date.isoformat(),
            "start_date": self.start_date.isoformat(),
            "defects_enabled": self.defects_enabled,
            "total_rows": self.total_rows,
            "files": [
                {
                    "dataset": file.dataset,
                    "path": file.path,
                    "rows": file.rows,
                    "bytes": file.bytes,
                    "sha256": file.sha256,
                }
                for file in self.files
            ],
            "classification": list(self.classification),
            "injections": [
                {
                    "defect": injection.defect,
                    "dataset": injection.dataset,
                    "target_key": injection.target_key,
                    "detail": injection.detail,
                }
                for injection in self.injections
            ],
        }

    def write(self, out_dir: Path) -> Path:
        path = out_dir / MANIFEST_FILENAME
        # sort_keys for stability, LF newline so the file hashes the same on
        # Windows and Linux — the same reason emit.py pins its line terminator.
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
        path.write_text(payload + "\n", encoding="utf-8", newline="\n")
        return path


def classification_summary(datasets: Sequence[Dataset]) -> tuple[dict[str, object], ...]:
    """Per-dataset column counts by tier — the catalog in machine-readable form.

    The governance phase reads this to enumerate what must be protected,
    rather than restating the classification in its own tests.
    """
    return tuple(
        {
            "dataset": dataset.name,
            "steward": dataset.steward,
            "max_tier": dataset.max_tier.value,
            "retention_days": dataset.retention_days,
            "primary_key": list(dataset.primary_key),
            "columns_by_tier": {
                tier.value: list(dataset.columns_at(tier))
                for tier in Tier
                if dataset.columns_at(tier)
            },
        }
        for dataset in sorted(datasets, key=lambda dataset: dataset.name)
    )


def build_manifest(
    *,
    seed: int,
    scale: str,
    as_of_date: date,
    start_date: date,
    defects_enabled: bool,
    files: Sequence[WrittenFile],
    injections: Sequence[Injection],
    datasets: Sequence[Dataset],
) -> RunManifest:
    return RunManifest(
        generator_version=__version__,
        seed=seed,
        scale=scale,
        as_of_date=as_of_date,
        start_date=start_date,
        defects_enabled=defects_enabled,
        files=tuple(files),
        # Sorted so the manifest does not depend on the order defects happened
        # to run in, which is an implementation detail rather than a result.
        injections=tuple(sorted(injections, key=lambda i: (i.defect, i.target_key))),
        classification=classification_summary(datasets),
    )
