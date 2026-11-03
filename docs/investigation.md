# Public source investigation — 3 November 2026

## Evidence and retrieval

The supplied public app URL loaded an HTML shell requiring JavaScript. Its HTML referenced:

- `/static/bundle.app.55c4fcb21440510b3098.js`
- `/static/worker.49fdb05d4b732468025a.js`

Reading the public worker revealed a GET helper used with `/database_data`, `database_id`, `bucket_ids`, and `api_version: "2"`. The normal sync-down call also supplies a version; a public diagnostic call explicitly requests the complete database with `bucket_ids: {}`. No endpoints were guessed, and no write, login, access-control or private-database operations were attempted.

A fresh, unauthenticated Playwright Chromium context confirmed this actual request:

```text
GET https://www.obvibase.com/database_data?version=0&database_id=INEwAfkgyn76&bucket_ids=%7B%7D&api_version=2&zx=u9wr96k69qzp
HTTP 200
```

`zx` is the frontend's random cache-busting parameter. Direct requests without it also returned the full JSON successfully. The finished Python generator independently fetched the same snapshot over HTTPS without cookies or authentication. The shell initially failed DNS inside the network sandbox; the authorized network-enabled run succeeded. This was an environment restriction, not a source access restriction.

## Completeness and schema

The response was an object with `data.version`, `data.bucket_ids`, `data.deleted_buckets`, and `data.buckets`. Version observed: **283591**. All **51 bucket keys** in the manifest were present, with no deleted buckets. Bucket `1` holds the schema; the remaining 50 buckets hold **1,971 records**, keyed by paths such as `1/hggaqf0lulcu`.

There is no row-pagination cursor in this protocol. An empty bucket cache and version zero retrieve the whole database in one response; buckets are synchronization partitions rather than pages to request individually. The generator verifies exact manifest coverage, so it cannot silently publish only the first partition or an incremental response. A future protocol change causes validation failure instead of invented pagination requests.

The public database has one table, `1`. Its populated fields are:

| Column | Label | Source type | Calendar use |
| --- | --- | --- | --- |
| 1 | Day | string | Original description |
| 2 | Date | date (`YYYYMMDD`) | DTSTART date |
| 3 | Event | string | SUMMARY and conservative time parsing |
| 4 | Location | string | LOCATION and description |
| 5 | Area | string | Description |
| 6 | Event Type | string | Description |
| 7 | Contact | string | Description |
| 8 | Telephone | string, sometimes email | Description |
| 9 | Website/Map | string | Link preserved in description |
| 10 | Other Info | string | Original description and embedded links |
| id | Display record number | string | Not the stable UID source |
| a | Date added | date string | Not an event date or modification timestamp |

Schema columns `16` (Website) and `29` (Venue) are also recognized but were unpopulated. Numerous additional unnamed columns were unpopulated. There are no dedicated start/end time fields. Other Info can contain several performances or doors/arrival details; it is preserved verbatim instead of turning arbitrary numbers into event times.

The browser displayed a “today onwards” date filter. It is a client query on the downloaded data; the feed intentionally includes every dated, named public record, including historical entries.

## Results

- 1,963 generated events: 1,629 timed, 1 explicitly all-day, 333 date-only with an explanatory timing note.
- Eight incomplete records skipped; their paths are listed in `public/report.json`.
- No repeated record paths in the retrieved snapshot.
- Event dates span 2025-01-01 to 2027-02-01.
- 23 automated tests passed; an independent `icalendar` parse validated all 1,963 VEVENTs.
- Repeat generation with preserved revision state is byte-for-byte stable.

Representative extracted records (times shown in Europe/London):

| Event | Date | Time | Venue |
| --- | --- | --- | --- |
| Full English | 3 Nov 2026 | 19:30–22:00 | Coachmakers Arms, Hanley |
| The Knutsford Session | 3 Nov 2026 | 20:30; end unspecified | The Lord Eldon, Knutsford |
| Hartington FC | 5 Nov 2026 | Date-only: source says `7.30-10pm`, start AM/PM unspecified | Devonshire Arms, Hartington |
| Middlewich Live! All Day Free Festival | 30 Aug 2025 | Explicitly all-day | Middlewich Town Centre |

## Repeat the browser inspection

`tools/inspect_frontend.py` logs only the relevant GET requests, statuses, and the first visible body text from a fresh context. It does not log cookies or account data. Install Playwright separately if needed:

```sh
.venv/bin/pip install playwright
.venv/bin/python tools/inspect_frontend.py
```

The script uses `/usr/bin/chromium-browser`, which was available in this workspace. On another machine, install Chromium with `python -m playwright install chromium` and remove the `executable_path` argument to use Playwright's managed browser. The browser is not part of scheduled generation.

## Limits

This is an observed internal public API, not a documented service-level guarantee. Textual dates/times depend on the database editors; the feed does not correct or supplement them from third-party websites. Ambiguous shorthand is intentionally left date-only. Separate performances described within a single record are not split into invented event identities. Cancellations retain their source identity and date, with an explicit cancellation status where the title states one.

GitHub Pages hosting and an actual iPhone/iPad subscription could not be tested because this workspace supplies no configured GitHub repository/remote or Apple device. The project includes the deployable workflow and subscription instructions; it does not claim a live subscription URL exists.
