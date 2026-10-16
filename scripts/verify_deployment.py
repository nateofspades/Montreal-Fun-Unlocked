"""Verify that the public Pages data exactly matches the validated local files."""

import argparse
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
import uuid


FILES = ("events.json", "events.csv", "metadata.json")


def verify_deployment(page_url, data_dir, *, attempts=6, delay=10, timeout=15,
                      opener=urlopen, sleep=time.sleep):
    if attempts < 1 or delay < 0 or timeout <= 0:
        raise ValueError("attempts and timeout must be positive; delay must be nonnegative")
    expected = {name: (Path(data_dir) / name).read_bytes() for name in FILES}
    for attempt in range(1, attempts + 1):
        try:
            for name, content in expected.items():
                url = f"{page_url.rstrip('/')}/data/{name}?verify={uuid.uuid4().hex}"
                request = Request(url, headers={"Cache-Control": "no-cache"})
                with opener(request, timeout=timeout) as response:
                    actual = response.read()
                if actual != content:
                    raise RuntimeError(f"{name}: deployed bytes do not match local data")
            return {name: hashlib.sha256(content).hexdigest()
                    for name, content in expected.items()}
        except (URLError, OSError, HTTPException, RuntimeError) as error:
            print(f"Verification attempt {attempt}/{attempts}: {error}", file=sys.stderr)
            if attempt == attempts:
                raise RuntimeError(f"Deployment verification failed after {attempts} attempts: {error}") from error
            sleep(delay)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Deployed Pages site URL (including repository subpath)")
    parser.add_argument("--data-dir", type=Path, default=Path("docs/data"))
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--delay", type=float, default=10)
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args()
    try:
        hashes = verify_deployment(args.url, args.data_dir, attempts=args.attempts,
                                   delay=args.delay, timeout=args.timeout)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Deployment verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"verified": True, "sha256": hashes}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
