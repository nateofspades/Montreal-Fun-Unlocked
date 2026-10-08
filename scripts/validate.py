#!/usr/bin/env python3
"""Validate generated data before commit or GitHub Pages deployment."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

try:
    from scripts.collect import CSV_FIELDS, CollectionError, csv_row, parse_instant, validate_dataset
except ModuleNotFoundError:  # Direct execution: python scripts/validate.py
    from collect import CSV_FIELDS, CollectionError, csv_row, parse_instant, validate_dataset


def validate_artifacts(data_dir: Path) -> dict[str, Any]:
    try:
        dataset = json.loads((data_dir / "events.json").read_text(encoding="utf-8"))
        metadata_file = json.loads((data_dir / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CollectionError(f"cannot read generated JSON: {error}") from error
    validate_dataset(dataset)
    metadata = dataset["metadata"]
    if metadata_file != metadata:
        raise CollectionError("metadata.json does not match events.json metadata")
    if metadata.get("timezone") != "America/Toronto":
        raise CollectionError("display timezone must be America/Toronto")
    try:
        window_from = parse_instant(metadata["windowFrom"])
        window_to = parse_instant(metadata["windowTo"])
        parse_instant(metadata["lastSuccessfulCollection"])
    except (KeyError, ValueError, TypeError) as error:
        raise CollectionError("collection timestamps are missing or invalid") from error
    if window_to - window_from != timedelta(days=60) or metadata.get("windowDays") != 60:
        raise CollectionError("collection window is not exactly 60 days")
    for event in dataset["showtimes"]:
        starts_at = parse_instant(event["startsAt"])
        if not window_from <= starts_at <= window_to:
            raise CollectionError(f"showtime {event['id']} falls outside the requested window")
    offsets = metadata.get("offsetsRetrieved")
    pages = metadata.get("pagesRetrieved")
    if not isinstance(offsets, list) or not offsets or offsets[0] != 0:
        raise CollectionError("pagination offsets must begin at zero")
    if pages != len(offsets) or any(not isinstance(x, int) for x in offsets):
        raise CollectionError("pagination page count does not match offsets")
    if any(current <= previous for previous, current in zip(offsets, offsets[1:])):
        raise CollectionError("pagination offsets are not strictly increasing")
    try:
        with (data_dir / "events.csv").open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != CSV_FIELDS:
                raise CollectionError("CSV columns do not match the published schema")
            csv_rows = list(reader)
    except (OSError, KeyError) as error:
        raise CollectionError(f"cannot read generated CSV: {error}") from error
    expected_rows = [
        {key: "" if value is None else str(value) for key, value in csv_row(event).items()}
        for event in dataset["showtimes"]
    ]
    if csv_rows != expected_rows:
        raise CollectionError("CSV rows do not exactly match JSON showtimes")
    return {
        "showtimeCount": len(expected_rows),
        "pagesRetrieved": pages,
        "duplicatesRemoved": metadata.get("duplicatesRemoved", 0),
        "paginationComplete": True,
        "lastSuccessfulCollection": metadata["lastSuccessfulCollection"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", nargs="?", type=Path, default=Path("docs/data"))
    args = parser.parse_args()
    try:
        print(json.dumps(validate_artifacts(args.data_dir), sort_keys=True))
        return 0
    except CollectionError as error:
        print(f"validation failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
