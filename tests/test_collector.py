import json
import io
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.collect import (
    CollectionError,
    collect_pages,
    csv_row,
    fixed_est_date,
    request_json,
    retry_after_seconds,
    validate_dataset,
    write_dataset_atomically,
)


def item(item_id, starts_at="2026-10-08T01:00:00.000Z", *, known=True, status="scheduled"):
    return {
        "id": item_id,
        "eventId": f"event-{item_id}",
        "slug": f"event-{item_id}",
        "title": f"Event {item_id}",
        "category": "music",
        "status": status,
        "startsAt": starts_at,
        "startTimeKnown": known,
        "endsAt": None,
        "doorsAt": None,
        "nightDate": "2026-10-07",
        "ageRestriction": None,
        "importance": 10,
        "outdoors": False,
        "confidence": "confirmed",
        "venue": {"slug": "venue", "name": "Venue", "neighbourhood": None},
        "url": f"https://montreal.hasthings.com/events/event-{item_id}",
        "ticketUrl": None,
        "sources": ["Source"],
        "tags": [],
    }


class CollectorTests(unittest.TestCase):
    def test_csv_cells_neutralize_spreadsheet_formulas(self):
        event = item("formula")
        event["title"] = '=HYPERLINK("https://example.test")'
        event["venue"]["name"] = "+SUM(1,1)"
        row = csv_row(event)
        self.assertEqual(row["title"], '\'=HYPERLINK("https://example.test")')
        self.assertEqual(row["venue_name"], "'+SUM(1,1)")

    def test_reuses_fresh_cached_response_without_a_second_request(self):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_args): self.close()

        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            def opener(_request, timeout):
                calls.append(timeout)
                return Response(b'{"items": []}')
            first = request_json("https://example.test/events", Path(tmp), opener=opener)
            second = request_json("https://example.test/events", Path(tmp), opener=opener)
            self.assertEqual(first, second)
            self.assertEqual(calls, [45])

    def test_429_honors_retry_after_before_bounded_retry(self):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_args): self.close()

        attempts = []
        sleeps = []
        def opener(request, timeout):
            attempts.append((request.full_url, timeout))
            if len(attempts) == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "slow down", {"Retry-After": "3"}, None)
            return Response(b'{"items": []}')
        with tempfile.TemporaryDirectory() as tmp:
            request_json("https://example.test/events", Path(tmp), opener=opener, sleep=sleeps.append)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(sleeps, [3.0])
        self.assertEqual(retry_after_seconds("2"), 2.0)

    def test_429_waits_at_least_two_seconds_with_short_retry_after(self):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_args): self.close()

        attempts = 0
        sleeps = []
        def opener(request, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "slow down", {"Retry-After": "1"}, None)
            return Response(b'{"items": []}')
        with tempfile.TemporaryDirectory() as tmp:
            request_json("https://example.test/events", Path(tmp), opener=opener, sleep=sleeps.append)
        self.assertEqual(sleeps, [2.0])

    def test_follows_next_offset_even_when_total_is_only_a_page_sentinel(self):
        pages = {
            0: {"items": [item("a"), item("b")], "count": 2, "total": 3, "offset": 0, "limit": 2, "nextOffset": 2, "license": {}},
            2: {"items": [item("c"), item("d")], "count": 2, "total": 5, "offset": 2, "limit": 2, "nextOffset": 4, "license": {}},
            4: {"items": [item("e")], "count": 1, "total": 5, "offset": 4, "limit": 2, "nextOffset": None, "license": {}},
        }
        requested = []

        def fetch(offset):
            requested.append(offset)
            return pages[offset]

        result = collect_pages(fetch)
        self.assertEqual(requested, [0, 2, 4])
        self.assertEqual([x["id"] for x in result.items], ["a", "b", "c", "d", "e"])
        self.assertEqual(result.reported_totals, [3, 5, 5])
        self.assertTrue(result.pagination_complete)

    def test_deduplicates_stable_showtime_id_but_preserves_performances(self):
        duplicate = item("show-1")
        pages = {
            0: {"items": [duplicate, item("show-2")], "count": 2, "offset": 0, "limit": 2, "nextOffset": 2, "license": {}},
            2: {"items": [duplicate, item("show-3")], "count": 2, "offset": 2, "limit": 2, "nextOffset": None, "license": {}},
        }
        result = collect_pages(lambda offset: pages[offset])
        self.assertEqual([x["id"] for x in result.items], ["show-1", "show-2", "show-3"])
        self.assertEqual(result.duplicates_removed, 1)

    def test_rejects_non_monotonic_pagination(self):
        page = {"items": [item("a")], "count": 1, "offset": 0, "limit": 1, "nextOffset": 0, "license": {}}
        with self.assertRaises(CollectionError):
            collect_pages(lambda _offset: page)

    def test_validation_rejects_duplicate_ids_and_bad_dates(self):
        dataset = {"metadata": {"paginationComplete": True}, "showtimes": [item("a"), item("a")]}
        with self.assertRaises(CollectionError):
            validate_dataset(dataset)
        dataset["showtimes"] = [item("a", "not-a-date")]
        with self.assertRaises(CollectionError):
            validate_dataset(dataset)

    def test_validation_allows_unknown_time_after_known_time_on_same_night(self):
        known = item("known", "2026-10-09T01:30:00.000Z")
        unknown = item("unknown", "2026-10-08T23:00:00.000Z", known=False)
        validate_dataset({"metadata": {"paginationComplete": True}, "showtimes": [known, unknown]})

    def test_validation_rejects_out_of_order_known_showtimes(self):
        late = item("late", "2026-10-08T03:00:00.000Z")
        early = item("early", "2026-10-08T01:00:00.000Z")
        with self.assertRaises(CollectionError):
            validate_dataset({"metadata": {"paginationComplete": True}, "showtimes": [late, early]})

    def test_atomic_write_preserves_previous_dataset_on_validation_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.json"
            original = {"metadata": {"paginationComplete": True}, "showtimes": [item("old")]}
            path.write_text(json.dumps(original), encoding="utf-8")
            bad = {"metadata": {"paginationComplete": False}, "showtimes": []}
            with self.assertRaises(CollectionError):
                write_dataset_atomically(bad, path, Path(tmp) / "events.csv", Path(tmp) / "metadata.json")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

    def test_atomic_write_rolls_back_every_file_if_replace_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            json_path, csv_path, meta_path = root / "events.json", root / "events.csv", root / "metadata.json"
            json_path.write_text('{"old": true}', encoding="utf-8")
            csv_path.write_text('old csv', encoding="utf-8")
            meta_path.write_text('{"old": true}', encoding="utf-8")
            dataset = {"metadata": {"paginationComplete": True}, "showtimes": [item("new")]}
            import scripts.collect as collector
            real_replace = collector.os.replace
            calls = 0
            def fail_second(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated replace failure")
                return real_replace(source, destination)
            with patch.object(collector.os, "replace", side_effect=fail_second):
                with self.assertRaises(OSError):
                    write_dataset_atomically(dataset, json_path, csv_path, meta_path)
            self.assertEqual(json_path.read_text(encoding="utf-8"), '{"old": true}')
            self.assertEqual(csv_path.read_text(encoding="utf-8"), 'old csv')
            self.assertEqual(meta_path.read_text(encoding="utf-8"), '{"old": true}')

    def test_fixed_est_calendar_date_does_not_follow_daylight_saving(self):
        instant = datetime(2026, 7, 1, 4, 30, tzinfo=timezone.utc)
        self.assertEqual(fixed_est_date(instant).isoformat(), "2026-06-30")

    def test_suspicious_major_drop_is_rejected(self):
        old = {"metadata": {"paginationComplete": True}, "showtimes": [item(str(i)) for i in range(100)]}
        new = {"metadata": {"paginationComplete": True}, "showtimes": [item(str(i)) for i in range(39)]}
        with self.assertRaises(CollectionError):
            validate_dataset(new, previous=old)


if __name__ == "__main__":
    unittest.main()
