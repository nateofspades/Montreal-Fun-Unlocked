import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.collect import CSV_FIELDS, csv_row
from scripts.validate import validate_artifacts
from tests.test_collector import item


class ArtifactValidationTests(unittest.TestCase):
    def test_validates_json_csv_pagination_and_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            events = [item("a"), item("b", "2026-10-09T01:00:00.000Z")]
            metadata = {
                "paginationComplete": True, "uniqueShowtimeCount": 2,
                "pagesRetrieved": 2, "offsetsRetrieved": [0, 500],
                "windowFrom": "2026-10-08T00:00:00Z", "windowTo": "2026-12-07T00:00:00Z",
                "windowDays": 60, "timezone": "America/Toronto",
                "lastSuccessfulCollection": "2026-10-08T00:10:00Z",
            }
            (data_dir / "events.json").write_text(json.dumps({"metadata": metadata, "showtimes": events}), encoding="utf-8")
            (data_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            with (data_dir / "events.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
                writer.writeheader(); writer.writerows(csv_row(event) for event in events)
            report = validate_artifacts(data_dir)
            self.assertEqual(report["showtimeCount"], 2)
            self.assertEqual(report["pagesRetrieved"], 2)

    def test_rejects_csv_that_does_not_match_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            event = item("a")
            metadata = {"paginationComplete": True, "uniqueShowtimeCount": 1, "pagesRetrieved": 1,
                        "offsetsRetrieved": [0], "windowFrom": "2026-10-08T00:00:00Z",
                        "windowTo": "2026-12-07T00:00:00Z", "windowDays": 60,
                        "timezone": "America/Toronto", "lastSuccessfulCollection": "2026-10-08T00:10:00Z"}
            (data_dir / "events.json").write_text(json.dumps({"metadata": metadata, "showtimes": [event]}), encoding="utf-8")
            (data_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            with (data_dir / "events.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
                writer.writeheader()
                wrong = csv_row(event); wrong["title"] = "corrupted title"
                writer.writerow(wrong)
            with self.assertRaises(Exception):
                validate_artifacts(data_dir)


if __name__ == "__main__":
    unittest.main()
