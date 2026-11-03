# North Staffs Folk Diary → Apple Calendar

A read-only subscription generator for public Obvibase database `INEwAfkgyn76`. Python 3.11+ with **no runtime packages**; GitHub Actions rebuilds every six hours and publishes a complete `calendar.ics` to GitHub Pages. It never writes to Obvibase or requires an Obvibase account.

**Verified live on 3 November 2026:** 51/51 buckets, 1,971 records, **1,963 events** (1,629 timed, 1 explicitly all-day, 333 date-only with timing notes). Eight records lack a date or event name. Dates span 1 January 2025–1 February 2027. The included `public/calendar.ics` is a real generated feed. Repository: https://github.com/darkmooink/-north-staffs-folk-calendar. Once Pages is enabled and deployed, subscribe at https://darkmooink.github.io/-north-staffs-folk-calendar/calendar.ics.

## Run locally

On Linux/macOS with Python 3.11+ and system timezone data:

```sh
python3 calendar_feed.py
```

Outputs:

- `public/calendar.ics` — subscription feed.
- `public/state.json` — stable per-event revision timestamps, sequences, hashes and safety baseline. Keep this with the feed.
- `public/report.json` — counts, date range and skipped record IDs.

No credentials, cookies, API keys or environment variables are needed. Only GET requests are made. The generator uses 30-second request timeouts, up to three attempts, and a 32 MiB response limit. Only retryable HTTP/network failures are retried.

For tests and independent ICS validation:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/validate_feed.py public
```

The test-only dependency is `icalendar`; Playwright is only needed to repeat the investigation. Tests cover dates, explicit and ambiguous times, DST gaps/folds, duplicate IDs, Unicode byte folding, escaping, independent parsing, revisions, deletions, completeness checks, failed retrieval and preservation of the last good feed.

## Publish with GitHub Pages

1. Put this project in a GitHub repository with `main` as its default branch. Include the `public` directory as the initial validated baseline; `.venv` and downloaded raw snapshots are ignored.
2. In **Settings → Pages → Build and deployment**, choose **GitHub Actions** as the source.
3. In **Actions**, enable workflows if prompted and run **Rebuild and publish calendar** manually once. Check that the build and deployment succeed.
4. Use `https://OWNER.github.io/REPOSITORY/calendar.ics` as the subscription URL, or the deployed Pages URL plus `/calendar.ics` when using a custom domain. For an `OWNER.github.io` repository, omit `/REPOSITORY`.

The workflow runs at 00:17, 06:17, 12:17 and 18:17 UTC, on pushes to `main`, and on manual dispatch. Edit the branch filter if using another default branch. GitHub schedules can be delayed and are [disabled after 60 days without repository activity in public repositories](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows?tool=webui); monitor the Actions tab and re-enable if necessary. Apple controls its own refresh interval independently.

The build retrieves the **currently published** `state.json` and feed, checks their checksum, then uses those revisions and counts as its baseline. A first deployment falls back to the included local baseline only when both remote files return 404. Other failures stop the workflow. Generated updates are deployed as an artifact without committing back to Git. No PAT or user secret is required; the deployment job uses GitHub's short-lived token with Pages and OIDC permissions. Each job has a ten-minute timeout and concurrent deployments are serialized.

See [GitHub's custom Pages workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Subscribe on iPhone or iPad

1. Open **Calendar → Calendars → Add Calendar → Add Subscription Calendar**.
2. Paste the **HTTPS subscription URL** ending in `/calendar.ics`.
3. Tap **Find** or **Subscribe**, review the calendar name, then **Add** or **Done** (labels vary by OS version).
4. Choose iCloud if offered and you want the subscription on your other devices. No username/password is required.

Use a subscription, rather than importing the downloaded file, to receive subsequent changes and removals. The calendar is read-only. If the Calendar menu is unavailable on an older release, use **Settings → Calendar → Accounts → Add Account → Other → Add Subscribed Calendar**; newer Settings layouts put Calendar under **Apps**.

Apple describes subscriptions and cross-device behavior in [Use iCloud calendar subscriptions](https://support.apple.com/en-sg/102301). A successful Pages build does not force an immediate Apple refresh. End-to-end subscription on an actual Apple device has not been tested here.

## Dates, times and identity

- Dedicated date column `2` supplies the date; column `3` supplies the original event title, including any times. The source has no separate start/end columns. Location, area, event type, contact, telephone/email, website/map and other information are retained in the description. `LOCATION` uses Location, with Venue as a fallback. Each event's `URL` links to its specific source record.
- Explicit `7.30pm`, `8pm`, `19:30`, noon, and explicit ranges such as `7.30pm-10pm` are supported. `7.15 for 8pm` uses the explicit performance time, 8pm, and preserves the arrival information in the title. A door-opening time alone is not treated as the performance start.
- `7.30`, `7.30-10pm`, `7pm for 7.30`, multiple performances and unstructured prose do not justify guessing a start time. Such entries use `DTSTART;VALUE=DATE` and a description explaining that their time is unknown. They appear in the all-day area but are **not asserted to last all day**. The one explicit “All Day” festival is preserved as an all-day event.
- No event date, missing end time, overnight end date, recurrence rule or duration is invented. A timed event without an explicit usable end omits `DTEND`. A DATE event without `DTEND` represents that calendar date. The feed preserves historical events, despite the frontend's default “today onwards” filter.
- Timed events are interpreted in `Europe/London` using Python `zoneinfo`, then emitted as UTC (`Z`). This preserves GMT/BST correctly without embedding `VTIMEZONE`. A nonexistent or ambiguous DST wall time stops the build rather than silently choosing an offset. Clients display UTC instants in their selected local timezone.
- UIDs derive from database ID and immutable record path, never title, date, order or content. Identical repeated paths are deduplicated; conflicting repeated paths fail. Different source IDs with identical contents remain separate events because their identities may be intentional.
- Fields are escaped, UTF-8 lines folded at 75 octets, and line endings are CRLF. Events are ordered by UID. Revision state keeps `DTSTAMP`, `LAST-MODIFIED` and `SEQUENCE` unchanged when event contents are unchanged. Changed events retain the UID and receive a new timestamp and incremented sequence. Timestamps represent feed revisions, not claimed source modification times. Preserve `state.json`: a fresh build without any state starts a new revision history.
- Explicit “CANCELLED” or “CLUB CLOSED” titles receive `STATUS:CANCELLED`. Original wording remains available; postponed dates are not inferred. Events are transparent and contain no alarms or meeting invitations.

The serialization follows [RFC 5545](https://www.rfc-editor.org/info/rfc5545/); the complete output is checked with an independent parser before deployment.

## Failure behavior and recovery

Every refresh fetches the complete public snapshot. Manifest mismatch, schema drift, malformed dates, conflicting duplicate records, empty event output, a backwards source version, and drops greater than 20% in records, events or timed entries stop publication. Ordinary deletions disappear on the next successful full regeneration. Larger legitimate purges require review, then a manual workflow run with **allow_large_drop** enabled. That option never permits empty output; a genuinely empty calendar needs a deliberate policy/code change.

Fetching and validation finish before local files are replaced. Individual file replacements are atomic. An interrupted write between feed and state files is detected by the checksum on the next run. In Actions, any failed step prevents artifact deployment, so GitHub Pages continues serving its last successfully deployed feed even when Obvibase is unavailable. A failed deployment likewise does not advance the persisted published baseline. Keep an eye on failed workflow runs because an old feed can remain available while stale.

For diagnosis/offline recovery, save the **observed public GET** response:

```sh
curl --fail --silent --show-error --max-time 30 --get \
  'https://www.obvibase.com/database_data' \
  --data-urlencode 'version=0' \
  --data-urlencode 'database_id=INEwAfkgyn76' \
  --data-urlencode 'bucket_ids={}' \
  --data-urlencode 'api_version=2' \
  --output raw-snapshot.json
python3 calendar_feed.py --snapshot raw-snapshot.json
```

A previously saved public response can rebuild the feed offline, subject to version/completeness guards. This is not a live automatic fallback and should not be presented as fresh data. If Obvibase changes its internal API, retain the last good feed and re-run the inspection procedure in `docs/investigation.md`; adapt the adapter to the actual public response. Do not add guessed endpoints or authentication bypasses.
