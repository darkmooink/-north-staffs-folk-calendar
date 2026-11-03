"""Independent parser validation of the complete generated feed."""
import json
from pathlib import Path
import sys
from icalendar import Calendar

folder = Path(sys.argv[1] if len(sys.argv) > 1 else 'public')
raw = (folder / 'calendar.ics').read_bytes()
cal = Calendar.from_ical(raw)
events = cal.walk('VEVENT')
report = json.loads((folder / 'report.json').read_text())
assert len(events) == report['events'] > 0
assert len({str(e['UID']) for e in events}) == len(events)
assert all(len(line) <= 75 for line in raw.split(b'\r\n'))
assert b'\n' not in raw.replace(b'\r\n', b'')
for event in events:
    assert not event.errors, event.errors
    assert all(k in event for k in ['UID', 'DTSTAMP', 'DTSTART', 'SUMMARY', 'URL'])
    if 'DTEND' in event:
        assert event.decoded('DTEND') > event.decoded('DTSTART')
print(f'Validated {len(events)} VEVENTs using icalendar')
