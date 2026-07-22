from datetime import datetime, timedelta, timezone

from brunata_client.ledger import decode_line, encode_line, parse_lines, trim_to_max_age

_TZ = timezone(timedelta(hours=2))


def test_encode_decode_roundtrip():
    ts = datetime(2026, 7, 22, 20, 44, 0, tzinfo=_TZ)
    line = encode_line(ts, 154212)
    assert decode_line(line) == (ts, 154212)


def test_encode_uses_iso8601_not_danish_format():
    # Explicit requirement: no dd-mm-yyyy ambiguity in the stored format.
    ts = datetime(2026, 3, 4, 8, 15, 0, tzinfo=_TZ)
    line = encode_line(ts, 1000)
    assert "2026-03-04" in line
    assert "04-03-2026" not in line
    assert "03-04-2026" not in line


def test_value_is_always_an_integer():
    ts = datetime(2026, 7, 22, tzinfo=_TZ)
    line = encode_line(ts, 154212)
    assert '"value": 154212' in line
    assert "154212.0" not in line


def test_decode_line_rejects_empty_and_blank_lines():
    assert decode_line("") is None
    assert decode_line("   \n") is None


def test_decode_line_rejects_corrupt_json():
    assert decode_line("{not valid json") is None
    assert decode_line('{"ts": "2026-07-22T20:44:00+02:00"}') is None  # missing "value"
    assert decode_line('{"value": 100}') is None  # missing "ts"


def test_parse_lines_skips_corrupt_lines_without_failing():
    ts = datetime(2026, 7, 22, 20, 44, 0, tzinfo=_TZ)
    good_line = encode_line(ts, 154212)
    lines = [good_line, "", "{garbage", good_line]
    parsed = parse_lines(lines)
    assert parsed == [(ts, 154212), (ts, 154212)]


def test_trim_to_max_age_keeps_only_recent_entries():
    as_of = datetime(2026, 7, 22, tzinfo=_TZ)
    entries = [
        (as_of - timedelta(days=400), 100),  # older than 1 year cap
        (as_of - timedelta(days=200), 200),
        (as_of - timedelta(days=1), 300),
    ]
    trimmed = trim_to_max_age(entries, as_of, max_age_days=365)
    assert trimmed == [(as_of - timedelta(days=200), 200), (as_of - timedelta(days=1), 300)]


def test_trim_to_max_age_boundary_is_inclusive():
    as_of = datetime(2026, 7, 22, tzinfo=_TZ)
    cutoff_entry = (as_of - timedelta(days=365), 100)
    trimmed = trim_to_max_age([cutoff_entry], as_of, max_age_days=365)
    assert trimmed == [cutoff_entry]


def test_trim_to_max_age_empty_input():
    as_of = datetime(2026, 7, 22, tzinfo=_TZ)
    assert trim_to_max_age([], as_of, max_age_days=365) == []
