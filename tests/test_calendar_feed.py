import copy
from datetime import date, datetime, time, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from icalendar import Calendar
import calendar_feed as f

NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
LATER = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


def snapshot(rows=None):
    columns = {k: {'name': v, 'type': 'd' if k == '2' else 's'} for k, v in f.FIELDS.items()}
    return {'data': {'version': 100, 'bucket_ids': {'1': 'schema', '2': 'records'},
                     'deleted_buckets': [], 'buckets': {
                         '1': {'tables': {'1': {'columns': columns}}},
                         '2': rows if rows is not None else {'1/abc': {
                             '2': '20260701', '3': 'Music 7.30pm-10pm',
                             '4': 'Hall, London', '10': 'Notes; back\\slash\nand newline'}}}}}


class DatesAndTimes(unittest.TestCase):
    def test_dates(self):
        for value in ['20260203', '2026-02-03', '03/02/2026']:
            self.assertEqual(f.parse_date(value), date(2026, 2, 3))
        self.assertEqual(f.parse_date('20240229'), date(2024, 2, 29))
        for value in ['20260229', '03/04/26', '', '2026-13-01', None]:
            with self.assertRaises(f.FeedError):
                f.parse_date(value)

    def test_explicit_clocks(self):
        for value, expected in [('7.30pm', time(19, 30)), ('12am', time(0)),
                                ('12pm', time(12)), ('12 noon', time(12)),
                                ('00:15', time(0, 15)), ('19:30', time(19, 30))]:
            self.assertEqual(f.parse_clock(value), expected)
        for value in ['7.30', '7', '25:00', '13pm', '7.70pm', '8.30ish']:
            self.assertIsNone(f.parse_clock(value))

    def test_ranges_and_arrival(self):
        self.assertEqual(f.parse_times('Music 7.30pm-10pm')[:2], (time(19, 30), time(22)))
        self.assertEqual(f.parse_times('Music 7.15 for 8pm')[:2], (time(20), None))
        self.assertEqual(f.parse_times('Music 7pm for 7.30')[:2], (None, None))
        self.assertEqual(f.parse_times('Music 7.30-10pm')[:2], (None, None))
        self.assertEqual(f.parse_times('Music 3pm til 6pm')[:2], (time(15), time(18)))
        self.assertEqual(f.parse_times('Doors 7pm')[:2], (None, None))
        self.assertEqual(f.parse_times('Music 11pm-1am')[:2], (time(23), None))
        self.assertEqual(f.parse_times('Music 2pm and 8pm')[:2], (None, None))

    def test_dst_boundaries(self):
        cases = [('20260328', '20260328T193000Z'), ('20260329', '20260329T183000Z'),
                 ('20261024', '20261024T183000Z'), ('20261025', '20261025T193000Z')]
        for day, expected in cases:
            self.assertEqual(f.local_to_utc(f.parse_date(day), time(19, 30)), expected)
        for day in ['20260329', '20261025']:
            with self.assertRaisesRegex(f.FeedError, 'Ambiguous or nonexistent'):
                f.local_to_utc(f.parse_date(day), time(1, 30))

    def test_all_day_and_missing_time(self):
        for title, kind in [('All Day Festival', 'all-day'), ('Music', 'date-only'),
                            ('Music 7.30', 'date-only')]:
            event = f.make_event('1/a', {'2': '20260329', '3': title})
            self.assertEqual(event['kind'], kind)
            self.assertEqual(event['start'], '20260329')
            self.assertNotIn('end', event)

    def test_no_invented_duration(self):
        event = f.make_event('1/a', {'2': '20260329', '3': 'Music 8pm'})
        self.assertNotIn('end', event)


class SnapshotTests(unittest.TestCase):
    def test_complete_manifest(self):
        s = snapshot()
        s['data']['bucket_ids']['3'] = 'missing'
        with self.assertRaisesRegex(f.FeedError, 'Incomplete'):
            f.extract(s)

    def test_identical_duplicate_path(self):
        s = snapshot()
        s['data']['bucket_ids']['3'] = 'duplicate'
        s['data']['buckets']['3'] = copy.deepcopy(s['data']['buckets']['2'])
        events, report = f.extract(s)
        self.assertEqual(len(events), 1)
        self.assertEqual(report['duplicate_paths'], 1)
        s['data']['buckets']['3']['1/abc']['3'] = 'Different'
        with self.assertRaisesRegex(f.FeedError, 'Conflicting duplicate'):
            f.extract(s)

    def test_distinct_records_with_same_content_keep_identity(self):
        s = snapshot()
        s['data']['buckets']['2']['1/def'] = copy.deepcopy(s['data']['buckets']['2']['1/abc'])
        events, _ = f.extract(s)
        self.assertEqual(len({e['uid'] for e in events}), 2)

    def test_missing_date_or_name_skipped(self):
        events, report = f.extract(snapshot({'1/a': {'3': 'Music'}, '1/b': {'2': '20260101'}}))
        self.assertEqual(events, [])
        self.assertEqual(len(report['skipped']), 2)

    def test_schema_change_and_bad_dates_fail(self):
        s = snapshot()
        s['data']['buckets']['1']['tables']['1']['columns']['2']['name'] = 'Unexpected'
        with self.assertRaises(f.FeedError):
            f.extract(s)
        with self.assertRaises(f.FeedError):
            f.extract(snapshot({'1/a': {'2': '20260230', '3': 'Music'}}))

    def test_rich_text(self):
        self.assertEqual(f.text_value([['Hello '], '1/abc']),
                         'Hello https://www.obvibase.com/app/r/INEwAfkgyn76/1/abc')
        with self.assertRaises(f.FeedError):
            f.text_value({'new': 'format'})


class ICSTests(unittest.TestCase):
    def test_independent_parser_and_utf8_folding(self):
        s = snapshot()
        s['data']['buckets']['2']['1/abc']['3'] = 'é🎻' * 80 + ' 8pm'
        events, _ = f.extract(s)
        raw, _ = f.render(events, now=NOW)
        self.assertTrue(all(len(line) <= 75 for line in raw.split(b'\r\n')))
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))
        cal = Calendar.from_ical(raw)
        event = cal.walk('VEVENT')[0]
        self.assertEqual(str(event['SUMMARY']), events[0]['summary'])
        self.assertEqual(str(event['DESCRIPTION']), events[0]['description'])
        self.assertEqual(str(event['LOCATION']), 'Hall, London')
        self.assertEqual(event.decoded('DTSTART'), datetime(2026, 7, 1, 19, tzinfo=timezone.utc))
        self.assertNotIn('DTEND', event)
        self.assertEqual(str(event['URL']), events[0]['url'])

    def test_date_property_and_cancellation(self):
        events, _ = f.extract(snapshot({'1/a': {'2': '20260101', '3': 'All Day Festival CANCELLED!'}}))
        raw, _ = f.render(events, now=NOW)
        event = Calendar.from_ical(raw).walk('VEVENT')[0]
        self.assertEqual(event.decoded('DTSTART'), date(2026, 1, 1))
        self.assertEqual(str(event['STATUS']), 'CANCELLED')
        self.assertNotIn('DTEND', event)

    def test_determinism_change_and_deletion(self):
        events, _ = f.extract(snapshot())
        raw, state = f.render(events, now=NOW)
        again, _ = f.render(list(reversed(events)), state, now=LATER)
        self.assertEqual(raw, again)
        uid = events[0]['uid']
        events[0]['summary'] = 'Changed'
        changed, revised = f.render(events, state, now=LATER)
        self.assertNotEqual(raw, changed)
        self.assertEqual(revised[uid]['sequence'], 1)
        self.assertEqual(str(Calendar.from_ical(changed).walk('VEVENT')[0]['UID']), uid)
        empty, _ = f.render([], revised, now=LATER)
        self.assertEqual(Calendar.from_ical(empty).walk('VEVENT'), [])

    def test_duplicate_uid_rejected(self):
        events, _ = f.extract(snapshot())
        with self.assertRaises(f.FeedError):
            f.render(events + events, now=NOW)


class PreservationTests(unittest.TestCase):
    def test_failure_preserves_all_files(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            f.build(snapshot(), output, now=NOW)
            previous = f.load_previous(output)
            before = {p.name: p.read_bytes() for p in output.iterdir()}
            for s in [snapshot({}), snapshot({'1/a': {'2': 'bad', '3': 'Music'}})]:
                with self.assertRaises(f.FeedError):
                    f.build(s, output, previous, now=LATER)
                self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})

    def test_large_drop_guard_and_reviewed_override(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            s = snapshot({f'1/r{i}': {'2': '20260101', '3': 'Music 8pm'} for i in range(10)})
            f.build(s, output, now=NOW)
            previous = f.load_previous(output)
            with self.assertRaisesRegex(f.FeedError, '20%'):
                f.build(snapshot(), output, previous, now=LATER)
            f.build(snapshot(), output, previous, allow_large_drop=True, now=LATER)
            self.assertEqual(len(Calendar.from_ical((output / 'calendar.ics').read_bytes()).walk('VEVENT')), 1)

    def test_normal_deletion_and_stale_version(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            s = snapshot({f'1/r{i}': {'2': '20260101', '3': 'Music 8pm'} for i in range(10)})
            f.build(s, output, now=NOW)
            previous = f.load_previous(output)
            del s['data']['buckets']['2']['1/r0']
            f.build(s, output, previous, now=LATER)
            self.assertEqual(len(f.load_previous(output)['events']), 9)
            s['data']['version'] = 99
            with self.assertRaisesRegex(f.FeedError, 'backwards'):
                f.build(s, output, previous, now=LATER)

    def test_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            f.build(snapshot(), output, now=NOW)
            (output / 'calendar.ics').write_text('corrupt')
            with self.assertRaisesRegex(f.FeedError, 'checksum'):
                f.load_previous(output)

    def test_get_retries_and_no_auth(self):
        with patch.object(f, 'urlopen', side_effect=URLError('offline')) as opener, patch.object(f.clock, 'sleep'):
            with self.assertRaises(URLError):
                f.read_url(f.ENDPOINT)
            self.assertEqual(opener.call_count, 3)
            request = opener.call_args[0][0]
            self.assertEqual(request.get_method(), 'GET')
            self.assertFalse(request.has_header('Authorization'))
            self.assertFalse(request.has_header('Cookie'))

    def test_previous_network_error_never_falls_back(self):
        with patch.object(f, 'read_url', side_effect=URLError('offline')):
            with self.assertRaises(URLError):
                f.load_previous(Path('/nonexistent'), 'https://example.com')

    def test_missing_remote_state_with_live_feed_fails(self):
        error = HTTPError('https://example.com/state.json', 404, 'missing', {}, None)
        with patch.object(f, 'read_url', side_effect=[error, b'existing calendar']):
            with self.assertRaisesRegex(f.FeedError, 'state is missing'):
                f.load_previous(Path('/nonexistent'), 'https://example.com')


if __name__ == '__main__':
    unittest.main()
