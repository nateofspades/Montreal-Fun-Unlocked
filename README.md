# Montreal Fun Unlocked

A searchable, mobile-friendly static guide to upcoming Montréal events. The site collects every available showtime in an explicit rolling 60-day window from the paginated Montréal Has Things API, then publishes validated JSON, CSV, and a GitHub Pages interface.

Live site: https://nateofspades.github.io/montreal-fun-unlocked/

## Data and attribution

Has Things exposes no-key, CORS-enabled city feeds and a paginated `/api/events` endpoint. Its documentation says the downloadable feeds are limited to 1,000 listings, while the API supports `limit` (maximum 500), `offset`, and `nextOffset`; this collector follows `nextOffset` until it is absent rather than trusting `total` as an exact overall count.[1]

The generated database in `docs/data/` is published under the Open Database Licence 1.0. Required credit: **Data: Has Things (hasthings.com)**. Source and ticket links, named sources, tags, status, age restriction, venue, confidence, and other supplied metadata are retained. Has Things excludes bulk descriptions and images, so this project does not invent or republish them.[1]

- JSON: `docs/data/events.json`
- CSV: `docs/data/events.csv`
- Collection metadata: `docs/data/metadata.json`
- Data licence: `LICENSE-DATA.md`
- Website and collector code: MIT (`LICENSE`)

## Local setup

Requires Python 3.12+ and Node.js (only to run the dependency-free browser-logic tests). There are no package installs or paid services.

    git clone https://github.com/nateofspades/montreal-fun-unlocked.git
    cd montreal-fun-unlocked
    python3 -m unittest discover -s tests -p 'test_*.py' -v
    node tests/test_app.js

Run a collection:

    python3 scripts/collect.py --output-dir docs/data --cache-dir .cache/hasthings
    python3 scripts/validate.py docs/data
    python3 -m http.server 8000 --directory docs

Then open `http://localhost:8000`.

## Collection behavior and request safeguards

`scripts/collect.py`:

- Sends explicit UTC `from` and `to` instants spanning exactly 60 days. The API otherwise defaults to tonight.[1]
- Requests up to 500 rows per page and follows every `nextOffset` sequentially.
- Waits at least two seconds between uncached requests.
- Identifies the project with a descriptive User-Agent.
- Reuses an on-disk response for the documented five-minute API cache lifetime. Has Things says polling inside that window returns the same bytes.[1]
- Retries only temporary network errors, HTTP 5xx, and HTTP 429, with bounded increasing delays. `Retry-After` is honored.
- Stops on persistent access errors; it does not bypass blocks.
- Deduplicates only by stable showtime `id`, preserving separate performances.
- Preserves source text exactly in JSON and neutralizes spreadsheet-formula prefixes in CSV cells.
- Writes through temporary files only after full pagination and validation.
- Rejects empty results, duplicate IDs, malformed timestamps, invalid local-night ordering, incomplete pagination, mismatched JSON/CSV, and a drop below 60% of a previous dataset of at least 100 showtimes.
- Preserves the last successful files when collection or validation fails.

Has Things currently documents edge caching of five minutes for API answers, no general rate limit beyond cache behavior, and a 60-request-per-minute unauthenticated bulk limit; abusive clients can be blocked. This project deliberately runs far below that pace.[1]

## Website behavior

The static site works at the repository Pages subpath and uses relative asset/data URLs. It:

- Displays dates and times with `America/Toronto`.
- Shows `Time not listed` whenever `startTimeKnown` is false, rather than displaying the source's noon placeholder.
- Searches titles, venues, and categories.
- Filters by category, venue, Today, Tomorrow, This Weekend, or custom dates.
- Sorts chronologically.
- Hides past, cancelled, and postponed listings by default; sold-out listings remain visible and labelled.
- Loads 48 cards at a time until every matching showtime is accessible.
- Shows the matching count and genuine last successful collection timestamp.
- Links to the supplied original listing and ticket URL when present.

## Daily automation and contribution attribution

`.github/workflows/refresh-and-deploy.yml` runs at:

    0 10 * * *

GitHub cron is UTC, so this is **5:00 a.m. fixed EST (UTC−5) year-round**. It runs at **6:00 a.m. Montréal local time while daylight saving time is in effect**; it intentionally does not move with DST. Scheduled GitHub Actions jobs can be delayed during periods of high load, especially near the start of an hour.[2]

The workflow can also be started manually. Concurrency permits only one collection/deployment run at a time. A successful collection makes exactly one data-refresh commit to `main`, including a changed `lastSuccessfulCollection` timestamp even if event rows are unchanged. The commit identity is:

    nateofspades <33226599+nateofspades@users.noreply.github.com>

That exact GitHub-provided noreply address was verified from commits linked to the account. A same-fixed-EST-day rerun detects the existing successful dataset and does not recollect or create another refresh commit. The manual `force_refresh` option exists only to correct or replace published results.

The same workflow validates, uploads, and deploys the Pages artifact directly with GitHub's supported Pages actions and required permissions; it does not depend on a token-authored push triggering a second workflow.[3] Select `deploy_only` to retry a failed deployment using existing validated data without contacting Has Things.

## Branch policy

Routine validated data refreshes commit directly to `main`. After initial setup, changes to collector code, UI, filters, sources, tests, or automation must be made on a separate branch, previewed/tested, and merged into `main`. Never force-push or rewrite `main`.

## Recovery

Every successful dataset remains recoverable in Git.

1. Find the last working refresh commit: `git log -- docs/data`.
2. Create a recovery branch: `git switch -c recovery/restore-events`.
3. Restore data from the known-good commit: `git restore --source <good-sha> docs/data`.
4. Run `python3 scripts/validate.py docs/data` and the full test suite.
5. Commit, push, and merge the recovery branch without rewriting history.
6. Run the workflow manually with `deploy_only: true` to republish without recollection.

A failed refresh leaves `main` and the live website untouched. A failed deployment can be retried with `deploy_only`.

## Limitations

- Event accuracy depends on the source and its upstream venues/ticket sellers; confirm details at the original link.
- Unknown fields remain blank or are labelled unknown; no missing details are inferred.
- The site intentionally carries no bulk event descriptions, posters, or images.
- GitHub may delay or, under sufficiently high load, drop scheduled jobs.[2]

## Sources

[1] [Feeds & API — Montréal Has Things](https://montreal.hasthings.com/feeds)
[2] [Events that trigger workflows - schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
[3] [Using custom workflows with GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
