from datetime import datetime, timedelta, timezone

from brunata_client.ledger import (
    decode_line,
    encode_line,
    find_gaps,
    parse_lines,
    trim_to_max_age,
)

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


# ---------------------------------------------------------------------------
# find_gaps
#
# Confirmed real case (2026-08-12/13): the per-poll trailing-edge check
# could only ever catch a gap while it was still the newest thing in the
# ledger — once a later poll succeeded, the gap became invisible to it
# forever, buried mid-history. find_gaps has no such blind spot.
# ---------------------------------------------------------------------------

def test_find_gaps_no_gaps_below_threshold():
    base = datetime(2026, 8, 1, tzinfo=_TZ)
    entries = [(base + timedelta(hours=h), 100 + h) for h in range(0, 24, 4)]
    assert find_gaps(entries, threshold=timedelta(hours=6)) == []


def test_find_gaps_detects_a_gap_at_the_end():
    base = datetime(2026, 8, 1, tzinfo=_TZ)
    entries = [
        (base, 100),
        (base + timedelta(hours=4), 110),
        (base + timedelta(hours=30), 200),  # 26h gap since the previous entry
    ]
    gaps = find_gaps(entries, threshold=timedelta(hours=6))
    assert gaps == [(base + timedelta(hours=4), base + timedelta(hours=30))]


def test_find_gaps_detects_a_gap_buried_in_the_middle():
    """The exact 2026-08-12/13 scenario: a gap that's no longer at the tail
    because normal polling resumed afterward and kept going — must still be
    found, not just gaps at the very end of the ledger."""
    base = datetime(2026, 8, 10, tzinfo=_TZ)
    entries = [
        (base, 100),
        (base + timedelta(hours=4), 110),
        # gap here: 2026-08-10 04:00 -> 2026-08-13 09:00, ~29 hours
        (base + timedelta(days=3, hours=5), 200),
        (base + timedelta(days=3, hours=9), 210),  # normal polling resumed
        (base + timedelta(days=3, hours=13), 220),
    ]
    gaps = find_gaps(entries, threshold=timedelta(hours=6))
    assert gaps == [(base + timedelta(hours=4), base + timedelta(days=3, hours=5))]


def test_find_gaps_multiple_separate_gaps():
    base = datetime(2026, 8, 1, tzinfo=_TZ)
    entries = [
        (base, 100),
        (base + timedelta(hours=20), 110),  # gap 1
        (base + timedelta(hours=24), 120),
        (base + timedelta(hours=48), 130),  # gap 2
        (base + timedelta(hours=50), 140),
    ]
    gaps = find_gaps(entries, threshold=timedelta(hours=6))
    assert gaps == [
        (base, base + timedelta(hours=20)),
        (base + timedelta(hours=24), base + timedelta(hours=48)),
    ]


def test_find_gaps_empty_and_single_entry_have_no_gaps():
    assert find_gaps([], threshold=timedelta(hours=6)) == []
    ts = datetime(2026, 8, 1, tzinfo=_TZ)
    assert find_gaps([(ts, 100)], threshold=timedelta(hours=6)) == []


def test_find_gaps_boundary_exactly_at_threshold_is_not_a_gap():
    base = datetime(2026, 8, 1, tzinfo=_TZ)
    entries = [(base, 100), (base + timedelta(hours=6), 110)]
    assert find_gaps(entries, threshold=timedelta(hours=6)) == []
