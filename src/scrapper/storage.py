"""Run output: NDJSON as we go, JSON array at the end, plus a manifest.

Writing incrementally matters — a run interrupted at vehicle 900 of 1000 still
leaves 900 usable records on disk, and ``seen.txt`` lets the next run skip them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import Vehicle


def new_run_id() -> str:
    """Filesystem-safe UTC timestamp, so run dirs sort chronologically."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


class RunWriter:
    """Owns one run directory and everything written into it."""

    def __init__(self, run_dir: Path, source: str, run_id: str) -> None:
        self.run_dir = run_dir
        self.source = source
        self.run_id = run_id
        self.started_at = datetime.now(timezone.utc)

        self.vehicles_ndjson = run_dir / "vehicles.ndjson"
        self.vehicles_json = run_dir / "vehicles.json"
        self.errors_ndjson = run_dir / "errors.ndjson"
        self.manifest_json = run_dir / "manifest.json"
        self.seen_txt = run_dir / "seen.txt"

        self.written = 0
        self.errors = 0
        self._seen: set[str] = set()
        self._vehicle_fh = None
        self._error_fh = None
        self._seen_fh = None

    def __enter__(self) -> "RunWriter":
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._seen = self._load_seen()
        # Append mode throughout so re-running into the same directory resumes
        # rather than truncating work already done.
        self._vehicle_fh = self.vehicles_ndjson.open("a", encoding="utf-8")
        self._error_fh = self.errors_ndjson.open("a", encoding="utf-8")
        self._seen_fh = self.seen_txt.open("a", encoding="utf-8")
        return self

    def __exit__(self, *exc_info: object) -> None:
        for handle in (self._vehicle_fh, self._error_fh, self._seen_fh):
            if handle is not None:
                handle.close()
        self._vehicle_fh = self._error_fh = self._seen_fh = None

    def _load_seen(self) -> set[str]:
        if not self.seen_txt.exists():
            return set()
        return {
            line.strip()
            for line in self.seen_txt.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    def already_seen(self, source_id: str) -> bool:
        return source_id in self._seen

    @property
    def seen_count(self) -> int:
        return len(self._seen)

    def write_vehicle(self, vehicle: Vehicle) -> bool:
        """Append one vehicle. Returns False if it was already written."""
        if vehicle.source_id in self._seen:
            return False
        assert self._vehicle_fh is not None and self._seen_fh is not None
        self._vehicle_fh.write(vehicle.model_dump_json() + "\n")
        self._vehicle_fh.flush()
        self._seen.add(vehicle.source_id)
        self._seen_fh.write(vehicle.source_id + "\n")
        self._seen_fh.flush()
        self.written += 1
        return True

    def write_error(self, url: str, reason: str, *, stage: str = "detail") -> None:
        """Record a failure without aborting the run."""
        assert self._error_fh is not None
        payload = {
            "url": url,
            "stage": stage,
            "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self._error_fh.write(json.dumps(payload) + "\n")
        self._error_fh.flush()
        self.errors += 1

    def finalize(self, *, targets: list[str], stats: dict[str, Any]) -> dict[str, Any]:
        """Write vehicles.json and manifest.json. Call once at the end."""
        total = self._write_json_array()

        finished_at = datetime.now(timezone.utc)
        manifest = {
            "source": self.source,
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": round((finished_at - self.started_at).total_seconds(), 2),
            "targets": targets,
            "vehicles_written": self.written,
            "vehicles_total_in_file": total,
            "errors": self.errors,
            "http": stats,
            "files": {
                "ndjson": self.vehicles_ndjson.name,
                "json": self.vehicles_json.name,
                "errors": self.errors_ndjson.name,
            },
        }
        self.manifest_json.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return manifest

    def _write_json_array(self) -> int:
        """Convert the NDJSON into a JSON array, streaming record by record.

        Building the list in memory first would cap the usable run size at
        whatever fits in RAM; a large scrape is exactly when that would bite.
        """
        count = 0
        with self.vehicles_json.open("w", encoding="utf-8") as out:
            out.write("[\n")
            for record in iter_ndjson(self.vehicles_ndjson):
                if count:
                    out.write(",\n")
                out.write(json.dumps(record, ensure_ascii=False))
                count += 1
            out.write("\n]\n")
        return count


def iter_ndjson(path: Path) -> Iterator[dict[str, Any]]:
    """Stream records out of an NDJSON file, skipping blank lines."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)
