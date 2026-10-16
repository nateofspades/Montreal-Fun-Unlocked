from contextlib import redirect_stderr
from functools import partial
import hashlib
from http.client import IncompleteRead
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from scripts.verify_deployment import verify_deployment


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        self.files = {
            "events.json": b'{"showtimes":[]}\n',
            "events.csv": b"id,title\r\n",
            "metadata.json": b'{"lastSuccessfulCollection":"2026-10-16"}\n',
        }
        for name, content in self.files.items():
            (self.data_dir / name).write_bytes(content)
        self.requests = []
        self.sleeps = []

    def opener(self, request, *, timeout):
        self.requests.append((request, timeout))
        return io.BytesIO(self.files[Path(urlsplit(request.full_url).path).name])

    def test_verifies_all_exact_files_with_cache_busting_and_timeout(self):
        hashes = verify_deployment(
            "https://example.test/project/", self.data_dir,
            opener=self.opener, sleep=self.sleeps.append, timeout=3,
        )
        self.assertEqual(hashes, {
            name: hashlib.sha256(content).hexdigest()
            for name, content in self.files.items()
        })
        self.assertEqual(len(self.requests), 3)
        for request, timeout in self.requests:
            url = urlsplit(request.full_url)
            self.assertTrue(url.path.startswith("/project/data/"))
            self.assertIn("verify", parse_qs(url.query))
            self.assertEqual(request.get_header("Cache-control"), "no-cache")
            self.assertEqual(timeout, 3)
        self.assertEqual(self.sleeps, [])


    def test_retries_http_network_timeout_and_stale_files(self):
        for problem in (HTTPError("https://example.test", 503, "unavailable", {}, None),
                        URLError("offline"), TimeoutError("timed out"),
                        IncompleteRead(b"truncated"), b"stale"):
            with self.subTest(problem=problem):
                calls = []
                sleeps = []

                def flaky(request, *, timeout):
                    calls.append(request.full_url)
                    if len(calls) == 1:
                        if isinstance(problem, Exception):
                            raise problem
                        return io.BytesIO(problem)
                    return self.opener(request, timeout=timeout)

                hashes = verify_deployment(
                    "https://example.test/project", self.data_dir,
                    opener=flaky, sleep=sleeps.append, attempts=2, delay=4,
                )
                self.assertEqual(set(hashes), set(self.files))
                self.assertEqual(sleeps, [4])
                self.assertEqual(len(calls), 4)
                self.assertNotEqual(calls[0], calls[1])


    def test_exhausted_network_failures_are_bounded(self):
        for problem in (HTTPError("https://example.test", 404, "not ready", {}, None),
                        URLError("offline"), TimeoutError("timed out")):
            with self.subTest(problem=problem):
                calls = []
                sleeps = []

                def unavailable(request, *, timeout):
                    calls.append(request.full_url)
                    raise problem

                with redirect_stderr(io.StringIO()), self.assertRaisesRegex(
                        RuntimeError, "failed after 3 attempts"):
                    verify_deployment(
                        "https://example.test/project/", self.data_dir,
                        opener=unavailable, sleep=sleeps.append, attempts=3, delay=2,
                    )
                self.assertEqual(len(calls), 3)
                self.assertEqual(sleeps, [2, 2])

    def test_matching_metadata_does_not_hide_stale_event_files(self):
        for stale_name in ("events.json", "events.csv", "metadata.json"):
            with self.subTest(stale_name=stale_name):
                sleeps = []

                def stale(request, *, timeout):
                    name = Path(urlsplit(request.full_url).path).name
                    content = self.files[name]
                    if name == stale_name:
                        content += b" "  # Even semantically equivalent bytes must fail.
                    return io.BytesIO(content)

                with redirect_stderr(io.StringIO()), self.assertRaisesRegex(
                        RuntimeError, f"failed after 2 attempts: {stale_name}"):
                    verify_deployment(
                        "https://example.test/project/", self.data_dir,
                        opener=stale, sleep=sleeps.append, attempts=2,
                    )
                self.assertEqual(sleeps, [10])

    def test_invalid_retry_settings_are_rejected_before_fetch(self):
        for settings in ({"attempts": 0}, {"delay": -1}, {"timeout": 0}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                verify_deployment("https://example.test/", self.data_dir,
                                  opener=self.opener, **settings)
        self.assertEqual(self.requests, [])

    def test_cli_verifies_real_http_files_at_repository_subpath(self):
        served = self.data_dir / "project" / "data"
        served.mkdir(parents=True)
        for name, content in self.files.items():
            (served / name).write_bytes(content)

        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(QuietHandler, directory=str(self.data_dir)),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run(
                [sys.executable, "scripts/verify_deployment.py", "--url",
                 f"http://127.0.0.1:{server.server_port}/project/",
                 "--data-dir", str(self.data_dir), "--attempts", "1"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"verified": true', result.stdout)
            for content in self.files.values():
                self.assertIn(hashlib.sha256(content).hexdigest(), result.stdout)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_cli_reports_failure_with_nonzero_exit(self):
        result = subprocess.run(
            [sys.executable, "scripts/verify_deployment.py", "--url",
             "https://example.test/project/", "--data-dir", str(self.data_dir / "missing")],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("No such file", result.stderr)


if __name__ == "__main__":
    unittest.main()
