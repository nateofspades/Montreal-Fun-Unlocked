#!/usr/bin/env python3
"""Collect and validate Montréal Has Things showtimes with conservative requests."""

from __future__ import annotations

import argparse
import csv
import email.utils
import hashlib
import json
import os
import random
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

API_URL = "https://montreal.hasthings.com/api/events"
SOURCE_DOCS = "https://montreal.hasthings.com/feeds"
USER_AGENT = "MontrealFunUnlocked/1.0 (+https://github.com/nateofspades/montreal-fun-unlocked; contact via GitHub)"
CACHE_SECONDS = 300
REQUEST_DELAY_SECONDS = 2.0
MAX_ATTEMPTS = 4
FIXED_EST = timezone(timedelta(hours=-5), name="EST")
REQUIRED_FIELDS = {
    "id", "title", "category", "status", "startsAt", "startTimeKnown",
    "nightDate", "venue", "url", "sources",
}
CSV_FIELDS = [
    "id", "event_id", "slug", "title", "starts_at", "start_time_known",
    "ends_at", "doors_at", "night_date", "category", "status",
    "age_restriction", "confidence", "importance", "outdoors", "venue_name",
    "venue_slug", "neighbourhood", "url", "ticket_url", "sources", "tags",
]


class CollectionError(RuntimeError):
    pass


@dataclass
class CollectionResult:
    items: list[dict[str, Any]]
    pages: int
    offsets: list[int]
    reported_totals: list[int]
    duplicates_removed: int
    pagination_complete: bool
    license: dict[str, Any]
    weather: Any


def parse_instant(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("instant must be a string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def fixed_est_date(instant: datetime | None = None) -> date:
    instant = instant or datetime.now(timezone.utc)
    return instant.astimezone(FIXED_EST).date()


def retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            current = now or datetime.now(timezone.utc)
            return max(0.0, (parsed - current).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def cache_paths(cache_dir: Path, url: str) -> tuple[Path, Path]:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{key}.json", cache_dir / f"{key}.meta.json"


def read_fresh_cache(cache_dir: Path, url: str, ttl: int = CACHE_SECONDS) -> dict[str, Any] | None:
    body_path, meta_path = cache_paths(cache_dir, url)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        age = time.time() - float(meta["fetchedAtEpoch"])
        if 0 <= age < ttl:
            return json.loads(body_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None
    return None


def save_cache(cache_dir: Path, url: str, payload: dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    body_path, meta_path = cache_paths(cache_dir, url)
    body_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    meta_path.write_text(json.dumps({"url": url, "fetchedAtEpoch": time.time()}), encoding="utf-8")


def request_json(
    url: str,
    cache_dir: Path,
    *,
    sleep: Callable[[float], None] = time.sleep,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    cached = read_fresh_cache(cache_dir, url)
    if cached is not None:
        return cached

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            with opener(request, timeout=45) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise CollectionError("API response is not a JSON object")
            save_cache(cache_dir, url, payload)
            return payload
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code == 429:
                delay = retry_after_seconds(error.headers.get("Retry-After"))
                if delay is None:
                    delay = min(60.0, 2 ** (attempt + 2))
                delay = max(REQUEST_DELAY_SECONDS, delay)
            elif 500 <= error.code < 600:
                delay = min(60.0, 2 ** (attempt + 2)) + random.uniform(0, 1)
            else:
                raise CollectionError(f"persistent HTTP {error.code} for {url}") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            delay = min(60.0, 2 ** (attempt + 2)) + random.uniform(0, 1)
        if attempt + 1 < MAX_ATTEMPTS:
            sleep(delay)
    raise CollectionError(f"request failed after {MAX_ATTEMPTS} attempts: {url}: {last_error}")


def collect_pages(fetch: Callable[[int], dict[str, Any]]) -> CollectionResult:
    offset = 0
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    offsets: list[int] = []
    totals: list[int] = []
    duplicates = 0
    license_block: dict[str, Any] = {}
    weather: Any = None

    for _page_guard in range(10000):
        page = fetch(offset)
        page_items = page.get("items")
        if not isinstance(page_items, list):
            raise CollectionError(f"page at offset {offset} has no items list")
        if page.get("offset", offset) != offset:
            raise CollectionError(f"response offset mismatch: requested {offset}, got {page.get('offset')}")
        if page.get("count", len(page_items)) != len(page_items):
            raise CollectionError(f"response count mismatch at offset {offset}")
        offsets.append(offset)
        if isinstance(page.get("total"), int):
            totals.append(page["total"])
        if page.get("license"):
            license_block = page["license"]
        if page.get("weather") is not None:
            weather = page["weather"]
        for event in page_items:
            event_id = event.get("id") if isinstance(event, dict) else None
            if not event_id:
                raise CollectionError(f"showtime without stable id at offset {offset}")
            if event_id in seen:
                duplicates += 1
                continue
            seen.add(event_id)
            items.append(event)
        next_offset = page.get("nextOffset")
        if next_offset is None:
            return CollectionResult(items, len(offsets), offsets, totals, duplicates, True, license_block, weather)
        if not isinstance(next_offset, int) or next_offset <= offset:
            raise CollectionError(f"non-monotonic nextOffset {next_offset!r} after {offset}")
        offset = next_offset
    raise CollectionError("pagination exceeded safety limit")


def validate_dataset(dataset: dict[str, Any], previous: dict[str, Any] | None = None) -> None:
    metadata = dataset.get("metadata")
    items = dataset.get("showtimes")
    if not isinstance(metadata, dict) or not metadata.get("paginationComplete"):
        raise CollectionError("pagination is not complete")
    if not isinstance(items, list) or not items:
        raise CollectionError("dataset is unexpectedly empty")
    ids: set[str] = set()
    previous_night: date | None = None
    previous_known_start: datetime | None = None
    for index, event in enumerate(items):
        if not isinstance(event, dict) or not REQUIRED_FIELDS.issubset(event):
            raise CollectionError(f"showtime {index} is missing required fields")
        event_id = event["id"]
        if event_id in ids:
            raise CollectionError(f"duplicate showtime id: {event_id}")
        ids.add(event_id)
        try:
            starts_at = parse_instant(event["startsAt"])
        except (ValueError, TypeError) as error:
            raise CollectionError(f"invalid startsAt for {event_id}") from error
        if starts_at.tzinfo is None:
            raise CollectionError(f"startsAt is not timezone-aware for {event_id}")
        try:
            night = date.fromisoformat(event["nightDate"])
        except (ValueError, TypeError) as error:
            raise CollectionError(f"invalid nightDate for {event_id}") from error
        if previous_night and night < previous_night:
            raise CollectionError("showtimes are not sorted by local night")
        previous_night = night
        if not isinstance(event["startTimeKnown"], bool):
            raise CollectionError(f"startTimeKnown is not boolean for {event_id}")
        if event["startTimeKnown"]:
            if previous_known_start and starts_at < previous_known_start:
                raise CollectionError("known-time showtimes are not sorted chronologically")
            previous_known_start = starts_at
        if not isinstance(event["venue"], dict):
            raise CollectionError(f"venue is not an object for {event_id}")
    if metadata.get("uniqueShowtimeCount") not in (None, len(items)):
        raise CollectionError("metadata showtime count does not match dataset")
    if previous:
        old_items = previous.get("showtimes", [])
        if len(old_items) >= 100 and len(items) < len(old_items) * 0.6:
            raise CollectionError(f"suspicious listing drop: {len(old_items)} to {len(items)}")


def csv_safe(value: Any) -> Any:
    """Prevent source-controlled text from becoming a spreadsheet formula."""
    if isinstance(value, str) and value.lstrip(" ").startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + value
    return value


def csv_row(event: dict[str, Any]) -> dict[str, Any]:
    venue = event.get("venue") or {}
    row = {
        "id": event.get("id"), "event_id": event.get("eventId"), "slug": event.get("slug"),
        "title": event.get("title"), "starts_at": event.get("startsAt"),
        "start_time_known": event.get("startTimeKnown"), "ends_at": event.get("endsAt"),
        "doors_at": event.get("doorsAt"), "night_date": event.get("nightDate"),
        "category": event.get("category"), "status": event.get("status"),
        "age_restriction": event.get("ageRestriction"), "confidence": event.get("confidence"),
        "importance": event.get("importance"), "outdoors": event.get("outdoors"),
        "venue_name": venue.get("name"), "venue_slug": venue.get("slug"),
        "neighbourhood": venue.get("neighbourhood"), "url": event.get("url"),
        "ticket_url": event.get("ticketUrl"),
        "sources": " | ".join(event.get("sources") or []),
        "tags": " | ".join(tag.get("label", "") for tag in event.get("tags") or [] if isinstance(tag, dict)),
    }
    return {key: csv_safe(value) for key, value in row.items()}


def write_dataset_atomically(dataset: dict[str, Any], json_path: Path, csv_path: Path, metadata_path: Path, previous: dict[str, Any] | None = None) -> None:
    validate_dataset(dataset, previous=previous)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=json_path.parent) as tmp:
        tmp_dir = Path(tmp)
        json_tmp = tmp_dir / "events.json"
        csv_tmp = tmp_dir / "events.csv"
        meta_tmp = tmp_dir / "metadata.json"
        json_tmp.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        meta_tmp.write_text(json.dumps(dataset["metadata"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with csv_tmp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(csv_row(event) for event in dataset["showtimes"])
        targets = ((json_tmp, json_path), (csv_tmp, csv_path), (meta_tmp, metadata_path))
        originals = {target: target.read_bytes() if target.exists() else None for _, target in targets}
        try:
            for source, target in targets:
                os.replace(source, target)
        except OSError:
            # Restore the complete previous publication if any replacement in
            # the group fails. Validation already happened before this point.
            for target, content in originals.items():
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(content)
            raise


def load_dataset(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def build_url(start: datetime, end: datetime, offset: int, limit: int = 500) -> str:
    query = urllib.parse.urlencode({
        "from": iso_utc(start), "to": iso_utc(end), "limit": limit,
        "offset": offset, "sort": "time", "dir": "asc",
    })
    return f"{API_URL}?{query}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/data"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/hasthings"))
    parser.add_argument("--skip-if-success-today", action="store_true")
    parser.add_argument("--force", action="store_true", help="allow another collection on the same fixed-EST day")
    parser.add_argument("--result-file", type=Path)
    args = parser.parse_args(argv)

    json_path = args.output_dir / "events.json"
    previous = load_dataset(json_path)
    now = datetime.now(timezone.utc)
    today_est = fixed_est_date(now).isoformat()
    previous_date = (previous or {}).get("metadata", {}).get("collectionDateFixedEst")
    if args.skip_if_success_today and not args.force and previous_date == today_est:
        result = {"refreshed": False, "reason": "already-successful-today", "collectionDateFixedEst": today_est}
        if args.result_file:
            args.result_file.write_text(json.dumps(result) + "\n", encoding="utf-8")
        print(json.dumps(result))
        return 0

    # Align to a minute boundary. The source can return a showtime at the start
    # of the requested minute, so this keeps strict range validation stable.
    start = now.replace(second=0, microsecond=0)
    end = start + timedelta(days=60)
    last_network_request = 0.0

    def fetch(offset: int) -> dict[str, Any]:
        nonlocal last_network_request
        url = build_url(start, end, offset)
        if read_fresh_cache(args.cache_dir, url) is None:
            elapsed = time.monotonic() - last_network_request
            if last_network_request and elapsed < REQUEST_DELAY_SECONDS:
                time.sleep(REQUEST_DELAY_SECONDS - elapsed)
            elif offset > 0 and not last_network_request:
                # A prior page may have come from a fresh cache created by an
                # interrupted run. Stay conservative before the next request.
                time.sleep(REQUEST_DELAY_SECONDS)
            payload = request_json(url, args.cache_dir)
            last_network_request = time.monotonic()
            return payload
        return request_json(url, args.cache_dir)

    try:
        collected = collect_pages(fetch)
        successful_at = datetime.now(timezone.utc).replace(microsecond=0)
        metadata = {
            "title": "Montreal Fun Unlocked",
            "source": API_URL,
            "sourceDocumentation": SOURCE_DOCS,
            "lastSuccessfulCollection": iso_utc(successful_at),
            "collectionDateFixedEst": fixed_est_date(successful_at).isoformat(),
            "windowFrom": iso_utc(start),
            "windowTo": iso_utc(end),
            "windowDays": 60,
            "timezone": "America/Toronto",
            "uniqueShowtimeCount": len(collected.items),
            "duplicatesRemoved": collected.duplicates_removed,
            "pagesRetrieved": collected.pages,
            "offsetsRetrieved": collected.offsets,
            "reportedTotalsByPage": collected.reported_totals,
            "paginationComplete": collected.pagination_complete,
            "license": collected.license,
            "attribution": "Data: Has Things (hasthings.com)",
        }
        dataset = {"metadata": metadata, "showtimes": collected.items}
        write_dataset_atomically(dataset, json_path, args.output_dir / "events.csv", args.output_dir / "metadata.json", previous=previous)
        result = {"refreshed": True, "showtimeCount": len(collected.items), "pages": collected.pages, "duplicatesRemoved": collected.duplicates_removed, "lastSuccessfulCollection": metadata["lastSuccessfulCollection"]}
        if args.result_file:
            args.result_file.write_text(json.dumps(result) + "\n", encoding="utf-8")
        print(json.dumps(result))
        return 0
    except CollectionError as error:
        print(f"collection failed; previous dataset preserved: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
