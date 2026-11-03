#!/usr/bin/env python3
"""Read-only Obvibase snapshot -> iCalendar. Runtime: Python 3.11+ stdlib."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, time, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time as clock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

DATABASE = 'INEwAfkgyn76'
ENDPOINT = 'https://www.obvibase.com/database_data'
SOURCE = 'https://www.obvibase.com/app/?' + urlencode({'location': json.dumps({
    'type': 'table', 'databaseId': DATABASE,
    'queryPath': {'recordPath': [], 'columnPath': ['1']}}, separators=(',', ':'))})
LONDON = ZoneInfo('Europe/London')
UTC = timezone.utc
MAX_BYTES = 32 * 1024 * 1024
FIELDS = {'1': 'Day', '2': 'Date', '3': 'Event', '4': 'Location', '5': 'Area',
          '6': 'Event Type', '7': 'Contact', '8': 'Telephone', '9': 'Website/Map',
          '10': 'Other Info', '16': 'Website', '29': 'Venue'}


class FeedError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_url(url, attempts=3):
    """GET only, bounded response, timeouts and retries. No login or cookies."""
    for attempt in range(attempts):
        try:
            req = Request(url, headers={'User-Agent': 'ObvibaseCalendar/1.0',
                                        'Accept': 'application/json, text/calendar;q=0.9'})
            with urlopen(req, timeout=30) as response:
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise FeedError('Response exceeds 32 MiB; refusing partial data')
                return body
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
        except (URLError, TimeoutError, OSError):
            if attempt == attempts - 1:
                raise
        clock.sleep(2 ** attempt)
    raise FeedError('Retrieval failed')


def fetch_snapshot():
    # Observed frontend GET; empty bucket_ids requests ALL buckets, not a delta.
    return json.loads(read_url(ENDPOINT + '?' + urlencode({
        'version': '0', 'database_id': DATABASE, 'bucket_ids': '{}', 'api_version': '2'})))


def unpack(snapshot):
    try:
        data = snapshot['data']
        buckets, ids = data['buckets'], data['bucket_ids']
        if not isinstance(data['version'], int) or data['version'] < 0:
            raise FeedError('Invalid database version')
        if not ids or set(ids) != set(buckets) or '1' not in buckets:
            raise FeedError('Incomplete snapshot: bucket manifest does not match response')
        if data.get('deleted_buckets'):
            raise FeedError('Unexpected delta response on full snapshot request')
        schema = buckets['1']['tables']['1']
        for key, name in list(FIELDS.items())[:10]:
            col = schema['columns'][key]
            if col.get('name') != name or col['type'] != ('d' if key == '2' else 's'):
                raise FeedError(f'Schema changed for column {key}: expected {name}')
        rows = {}
        repeated = 0
        for bucket, values in buckets.items():
            if bucket == '1':
                continue
            if not isinstance(values, dict):
                raise FeedError(f'Invalid bucket {bucket}')
            for path, record in values.items():
                if not re.fullmatch(r'1/[A-Za-z0-9]+', path):
                    raise FeedError(f'Unexpected table/record path: {path}')
                if not isinstance(record, dict):
                    raise FeedError(f'Invalid record {path}')
                if path in rows:
                    if rows[path] != record:
                        raise FeedError(f'Conflicting duplicate record {path}')
                    repeated += 1
                rows[path] = record
        return data['version'], rows, repeated, len(buckets)
    except (KeyError, TypeError, AttributeError) as exc:
        raise FeedError(f'Unexpected Obvibase schema: {exc}') from exc


def text_value(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    # Obvibase rich strings: literal segments [text], record references "1/id".
    if isinstance(value, list):
        return ''.join(segment[0] if isinstance(segment, list) and len(segment) == 1
                       and isinstance(segment[0], str) else
                       f'https://www.obvibase.com/app/r/{DATABASE}/{segment}'
                       if isinstance(segment, str) and re.fullmatch(r'(?:[A-Za-z0-9]+/)+[A-Za-z0-9]+', segment)
                       else _bad_text() for segment in value).strip()
    raise FeedError('Unexpected text field type')


def _bad_text():
    raise FeedError('Unexpected rich text segment')


def parse_date(value):
    if not isinstance(value, str):
        raise FeedError('Date must be a string')
    for pattern, fmt in [(r'\d{8}', '%Y%m%d'), (r'\d{4}-\d{2}-\d{2}', '%Y-%m-%d'),
                         (r'\d{2}/\d{2}/\d{4}', '%d/%m/%Y')]:
        if re.fullmatch(pattern, value):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError as exc:
                raise FeedError(f'Invalid date: {value}') from exc
    raise FeedError(f'Unsupported date: {value}')


# A broad token recognises incomplete times too, preventing the end of a range
# from being mistaken for a start. Bare numbers are only allowed in pairs.
TOKEN = r'(?:\d{1,2}(?:[.:]\d{2})?\s*(?:[ap]m|noon|midnight)?|noon|midnight)'
PAIR = re.compile(rf'(?<![\d.])(?P<a>{TOKEN})\s*(?P<sep>[-–—]|to|til|until|for)\s*(?P<b>{TOKEN})(?![\w.])', re.I)
SINGLE = re.compile(r'(?<![\d.])(?:\d{1,2}(?:[.:]\d{2})?\s*(?:[ap]m|noon|midnight)|\d{1,2}[.:]\d{2}|noon|midnight)(?!\w)', re.I)


def parse_clock(value):
    value = value.lower().strip()
    if value in ('noon', '12 noon', '12noon'):
        return time(12)
    if value in ('midnight', '12 midnight', '12midnight'):
        return time(0)
    m = re.fullmatch(r'(\d{1,2})(?:([.:])(\d{2}))?\s*([ap]m)?', value)
    if not m:
        return None
    hour, separator, minute, meridiem = m.groups()
    hour, minute = int(hour), int(minute or 0)
    if minute > 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        return time(hour % 12 + (12 if meridiem == 'pm' else 0), minute)
    # Accept explicit 24-hour HH:MM only. '7.30' has no reliable AM/PM.
    if separator == ':' and hour <= 23:
        return time(hour, minute)
    return None


def parse_times(title):
    pairs = list(PAIR.finditer(title))
    if pairs:
        if len(pairs) != 1:
            return None, None, 'Multiple time ranges; check source'
        m = pairs[0]
        if SINGLE.search(title[:m.start()] + title[m.end():]):
            return None, None, 'Multiple times; check source'
        start, end = parse_clock(m['a']), parse_clock(m['b'])
        if m['sep'].lower() == 'for':
            # "doors 7pm for 7.30pm" means performance starts at 7.30pm.
            return (end, None, 'Arrival time retained in title') if end else (
                None, None, 'Performance time lacks explicit AM/PM; check source')
        if start is None:
            return None, None, 'Start time lacks explicit AM/PM; check source'
        if end is None:
            return start, None, 'End time unclear; no duration assigned'
        if end <= start:
            return start, None, 'End date unclear; no overnight duration inferred'
        return start, end, ''
    matches = list(SINGLE.finditer(title))
    if len(matches) != 1:
        return None, None, 'Time missing or ambiguous; date-only listing, check source'
    if re.search(r'\bdoors?\s*$', title[:matches[0].start()], re.I):
        return None, None, 'Only door-opening time supplied; performance time unknown'
    parsed = parse_clock(matches[0].group())
    return parsed, None, '' if parsed else 'Time lacks explicit AM/PM; check source'


def local_to_utc(day, local_time):
    naive = datetime.combine(day, local_time)
    candidates = set()
    for fold in (0, 1):
        instant = naive.replace(tzinfo=LONDON, fold=fold).astimezone(UTC)
        if instant.astimezone(LONDON).replace(tzinfo=None) == naive:
            candidates.add(instant)
    if len(candidates) != 1:
        raise FeedError(f'Ambiguous or nonexistent London time: {naive.isoformat()}')
    return candidates.pop().strftime('%Y%m%dT%H%M%SZ')


def make_event(path, row):
    day = parse_date(row['2'])
    title = text_value(row.get('3'))
    start, end, note = parse_times(title)
    all_day = bool(re.search(r'\ball[ -]day\b', title, re.I)) and start is None
    kind = 'timed' if start else 'all-day' if all_day else 'date-only'
    source = f'https://www.obvibase.com/app/r/{DATABASE}/{path}'
    description = []
    for key, label in FIELDS.items():
        val = text_value(row.get(key))
        if val:
            description.append(f'{label}: {val}')
    if note and not all_day:
        description.append('Timing note: ' + note)
    if kind == 'date-only':
        description.append('Shown in the all-day area because the event time is unknown; this does not mean it lasts all day.')
    description.append('Source: ' + source)
    event = {'uid': f'{DATABASE}-{path.replace("/", "-")}@obvibase-calendar',
             'summary': title, 'description': '\n'.join(description),
             'location': text_value(row.get('4')) or text_value(row.get('29')),
             'url': source, 'kind': kind,
             'start': local_to_utc(day, start) if start else day.strftime('%Y%m%d')}
    if end:
        event['end'] = local_to_utc(day, end)
    if re.search(r'\bcancelled\b|\bclub closed\b', title, re.I):
        event['status'] = 'CANCELLED'
    return event


def extract(snapshot):
    version, rows, duplicates, bucket_count = unpack(snapshot)
    events, skipped = [], []
    for path, row in sorted(rows.items()):
        if not row.get('2') or not text_value(row.get('3')):
            skipped.append({'record': path, 'reason': 'Missing date or event name'})
            continue
        # Malformed dates/DST ambiguity fail the build, rather than delete an event.
        events.append(make_event(path, row))
    report = {'source_version': version, 'buckets': bucket_count, 'records': len(rows),
              'events': len(events), 'kinds': dict(Counter(e['kind'] for e in events)),
              'duplicate_paths': duplicates, 'skipped': skipped,
              'date_range': [min((e['start'][:8] for e in events), default=''),
                             max((e['start'][:8] for e in events), default='')]}
    return events, report


def escape(value):
    value = value.replace('\r\n', '\n').replace('\r', '\n')
    if any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise FeedError('Control character in source text')
    return value.replace('\\', '\\\\').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def fold_line(line):
    lines, current = [], ''
    for char in line:
        if len((current + char).encode('utf-8')) > 75:
            lines.append(current)
            current = ' '
        current += char
    return '\r\n'.join(lines + [current])


def render(events, previous=None, now=None):
    previous = previous or {}
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Obvibase Calendar//EN',
             'CALSCALE:GREGORIAN', "X-WR-CALNAME:North Staffs Folk Diary",
             'X-WR-TIMEZONE:Europe/London', 'REFRESH-INTERVAL;VALUE=DURATION:PT6H',
             'X-PUBLISHED-TTL:PT6H']
    state = {}
    for event in sorted(events, key=lambda e: e['uid']):
        uid = event['uid']
        if uid in state:
            raise FeedError('Duplicate event UID')
        fingerprint = digest(canonical(event).encode())
        old = previous.get(uid)
        if old and old['hash'] == fingerprint:
            revision = old
        else:
            revision = {'hash': fingerprint, 'stamp': timestamp,
                        'sequence': old['sequence'] + 1 if old else 0}
        state[uid] = revision
        lines += ['BEGIN:VEVENT', 'UID:' + uid, 'DTSTAMP:' + revision['stamp'],
                  'LAST-MODIFIED:' + revision['stamp'], 'SEQUENCE:' + str(revision['sequence']),
                  ('DTSTART:' if event['kind'] == 'timed' else 'DTSTART;VALUE=DATE:') + event['start']]
        if 'end' in event:
            lines.append('DTEND:' + event['end'])
        for key in ('summary', 'location', 'description'):
            if event.get(key):
                lines.append(key.upper() + ':' + escape(event[key]))
        lines += ['URL:' + event['url'], 'TRANSP:TRANSPARENT']
        if 'status' in event:
            lines.append('STATUS:' + event['status'])
        lines.append('END:VEVENT')
    lines.append('END:VCALENDAR')
    return ('\r\n'.join(fold_line(line) for line in lines) + '\r\n').encode(), state


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        name = tmp.name
        tmp.write(data)
    try:
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_previous(output, base_url=None):
    if base_url:
        if urlsplit(base_url).scheme != 'https':
            raise FeedError('Previous feed must use HTTPS')
        try:
            state_bytes = read_url(base_url.rstrip('/') + '/state.json')
        except HTTPError as exc:
            if exc.code != 404:
                raise
            # A missing state is only a first deployment if the calendar is absent too.
            try:
                read_url(base_url.rstrip('/') + '/calendar.ics')
            except HTTPError as feed_exc:
                if feed_exc.code != 404:
                    raise
            else:
                raise FeedError('Published calendar exists but its state is missing')
        else:
            state = json.loads(state_bytes)
            feed = read_url(base_url.rstrip('/') + '/calendar.ics')
            return validate_previous(state, feed)
    state_path = output / 'state.json'
    if state_path.exists():
        return validate_previous(json.loads(state_path.read_text()), (output / 'calendar.ics').read_bytes())
    if (output / 'calendar.ics').exists():
        raise FeedError('Local calendar exists but its state is missing')
    return None


def validate_previous(state, feed):
    if state.get('format') != 1 or state.get('database') != DATABASE:
        raise FeedError('Previous state format/database mismatch')
    if state['feed_sha256'] != digest(feed):
        raise FeedError('Previous feed/state checksum mismatch')
    if state['report']['events'] != len(state['events']):
        raise FeedError('Previous state count mismatch')
    return state


def build(snapshot, output, previous=None, allow_large_drop=False, now=None):
    events, report = extract(snapshot)
    if not events:
        raise FeedError('Empty result: preserving last good feed')
    if previous:
        old_report = previous['report']
        if report['source_version'] < old_report['source_version']:
            raise FeedError('Source version went backwards; refusing stale snapshot')
        if not allow_large_drop:
            for key in ('records', 'events'):
                if report[key] < old_report[key] * 0.8:
                    raise FeedError(f'{key} fell by more than 20%; inspect before allowing large drop')
            old_timed = old_report['kinds'].get('timed', 0)
            if report['kinds'].get('timed', 0) < old_timed * 0.8:
                raise FeedError('Timed events fell by more than 20%; inspect source/parser changes')
    body, revisions = render(events, previous['events'] if previous else None, now)
    state = {'format': 1, 'database': DATABASE, 'feed_sha256': digest(body),
             'report': report, 'events': revisions}
    # All fetching/parsing/validation completes before any output is replaced.
    atomic_write(output / 'calendar.ics', body)
    atomic_write(output / 'state.json', (canonical(state) + '\n').encode())
    atomic_write(output / 'report.json', (json.dumps(report, indent=2) + '\n').encode())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, help='Offline full Obvibase JSON response')
    parser.add_argument('--output', type=Path, default=Path('public'))
    parser.add_argument('--previous-base-url', help='Restore revisions and guard counts from published Pages feed')
    parser.add_argument('--allow-large-drop', action='store_true', help='Accept reviewed deletion >20%%; never permits empty output')
    args = parser.parse_args()
    try:
        previous = load_previous(args.output, args.previous_base_url)
        snapshot = json.loads(args.snapshot.read_text()) if args.snapshot else fetch_snapshot()
        report = build(snapshot, args.output, previous, args.allow_large_drop)
        print(json.dumps(report, indent=2))
    except (FeedError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Build failed; no deployment should run: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
