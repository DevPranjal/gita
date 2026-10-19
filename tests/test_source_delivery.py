from dataclasses import FrozenInstanceError

import pytest

from gita.source_delivery import (
    PreviouslyDeliveredRange,
    SourceDeliveryLedger,
    SourceKey,
    SourceLine,
)


KEY = SourceKey("commit", "src/file.py", "blob")
LINES = {1: "one\r\n", 2: "  two\n", 3: "\n", 4: "four", 5: ""}


def test_exact_overlap_and_receipt_boundaries():
    ledger = SourceDeliveryLedger()
    ledger.register_completed(KEY, {1: LINES[1], 2: LINES[2]}, receipt_id="call-1")
    ledger.register_completed(KEY, {2: LINES[2], 3: LINES[3]}, receipt_id="call-2")
    result = ledger.render(KEY, LINES, start=2, end=5)
    assert result.new_lines == (SourceLine(4, "four"), SourceLine(5, ""))
    assert result.previously_delivered_ranges == (
        PreviouslyDeliveredRange(2, 2, "call-1"),
        PreviouslyDeliveredRange(3, 3, "call-2"),
    )
    assert ledger.render(KEY, LINES, start=1, end=2).previously_delivered_ranges == (
        PreviouslyDeliveredRange(1, 2, "call-1"),
    )


@pytest.mark.parametrize(
    "other",
    [
        SourceKey("changed", KEY.path, KEY.blob),
        SourceKey(KEY.commit, "other.py", KEY.blob),
        SourceKey(KEY.commit, KEY.path, "changed"),
        SourceKey(KEY.commit, KEY.path.upper(), KEY.blob),
    ],
)
def test_identity_is_exact(other):
    ledger = SourceDeliveryLedger()
    ledger.register_completed(KEY, LINES, receipt_id="call-1")
    result = ledger.render(other, LINES, start=1, end=5)
    assert result.new_lines == tuple(SourceLine(n, text) for n, text in LINES.items())
    assert not result.previously_delivered_ranges


@pytest.mark.parametrize("flag", ["force_full", "replay"])
def test_full_replay_does_not_erase_or_register(flag):
    ledger = SourceDeliveryLedger()
    ledger.register_completed(KEY, {2: LINES[2]}, receipt_id="call-1")
    result = ledger.render(KEY, LINES, start=1, end=3, **{flag: True})
    assert result.new_lines == tuple(SourceLine(n, LINES[n]) for n in (1, 2, 3))
    assert not result.previously_delivered_ranges
    normal = ledger.render(KEY, LINES, start=1, end=3)
    assert normal.new_lines == (SourceLine(1, LINES[1]), SourceLine(3, LINES[3]))
    assert normal.previously_delivered_ranges == (PreviouslyDeliveredRange(2, 2, "call-1"),)


def test_registration_is_only_explicit_lines_and_render_has_no_side_effect():
    ledger = SourceDeliveryLedger()
    ledger.render(KEY, LINES, start=1, end=5)
    assert ledger.retained_lines == 0
    ledger.register_completed(KEY, {1: LINES[1], 3: LINES[3]}, receipt_id="call-1")
    result = ledger.render(KEY, LINES, start=1, end=5)
    assert tuple(line.number for line in result.new_lines) == (2, 4, 5)
    assert result.previously_delivered_ranges == (
        PreviouslyDeliveredRange(1, 1, "call-1"),
        PreviouslyDeliveredRange(3, 3, "call-1"),
    )


@pytest.mark.parametrize("replacement", ["one\n", "one\r\n ", "ONE\r\n"])
def test_conflict_guard_is_exact_and_atomic(replacement):
    ledger = SourceDeliveryLedger()
    ledger.register_completed(KEY, {1: LINES[1]}, receipt_id="call-1")
    with pytest.raises(ValueError, match="inconsistent"):
        ledger.register_completed(KEY, {2: LINES[2], 1: replacement}, receipt_id="call-2")
    assert ledger.retained_lines == 1
    for flags in ({}, {"force_full": True}, {"replay": True}):
        with pytest.raises(ValueError, match="inconsistent"):
            ledger.render(KEY, {1: replacement}, start=1, end=1, **flags)


def test_bounded_line_forgetting_never_claims_evicted_content():
    ledger = SourceDeliveryLedger(max_lines=2)
    ledger.register_completed(KEY, {1: LINES[1], 2: LINES[2]}, receipt_id="call-1")
    ledger.register_completed(KEY, {3: LINES[3]}, receipt_id="call-2")
    assert ledger.retained_lines == 2
    result = ledger.render(KEY, LINES, start=1, end=3)
    assert result.new_lines == (SourceLine(1, LINES[1]),)
    assert result.previously_delivered_ranges == (
        PreviouslyDeliveredRange(2, 2, "call-1"),
        PreviouslyDeliveredRange(3, 3, "call-2"),
    )
    ledger.register_completed(KEY, {1: LINES[1]}, receipt_id="call-3")
    assert ledger.render(KEY, LINES, start=1, end=1).previously_delivered_ranges == (
        PreviouslyDeliveredRange(1, 1, "call-3"),
    )


def test_character_bound_charges_identifiers_and_forgets():
    key = SourceKey("c", "p", "b")
    ledger = SourceDeliveryLedger(max_chars=10)
    ledger.register_completed(key, {1: "123", 2: "456"}, receipt_id="r")
    assert ledger.retained_chars == 7
    assert ledger.retained_lines == 1
    result = ledger.render(key, {1: "123", 2: "456"}, start=1, end=2)
    assert result.new_lines == (SourceLine(1, "123"),)
    assert result.previously_delivered_ranges == (PreviouslyDeliveredRange(2, 2, "r"),)
    ledger.register_completed(key, {3: "way too large"}, receipt_id="r")
    assert ledger.retained_lines == 1
    assert ledger.render(key, {3: "way too large"}, start=3, end=3).new_lines
    ledger.register_completed(key, {4: ""}, receipt_id="receipt-too-large")
    assert ledger.retained_chars <= 10
    assert ledger.render(key, {4: ""}, start=4, end=4).new_lines


def test_input_mapping_is_snapshotted_and_key_is_immutable():
    ledger = SourceDeliveryLedger()
    lines = {1: "exact"}
    ledger.register_completed(KEY, lines, receipt_id="call-1")
    lines[1] = "changed"
    assert not ledger.render(KEY, {1: "exact"}, start=1, end=1).new_lines
    with pytest.raises(FrozenInstanceError):
        KEY.blob = "changed"


@pytest.mark.parametrize("start,end", [(0, 1), (2, 1), (True, 1), (1, 1.5)])
def test_bad_ranges(start, end):
    with pytest.raises(ValueError):
        SourceDeliveryLedger().render(KEY, LINES, start=start, end=end)


@pytest.mark.parametrize("value", ["", " ", " padded", "bad\nid", "bad\x00id", None])
def test_bad_identifiers(value):
    with pytest.raises(ValueError):
        SourceKey(value, "path", "blob")
    with pytest.raises(ValueError):
        SourceKey("commit", value, "blob")
    with pytest.raises(ValueError):
        SourceKey("commit", "path", value)
    with pytest.raises(ValueError):
        SourceDeliveryLedger().register_completed(KEY, {1: "line"}, receipt_id=value)


@pytest.mark.parametrize("lines", [{0: "bad"}, {True: "bad"}, {1.5: "bad"}, {"1": "bad"}])
def test_bad_line_numbers(lines):
    with pytest.raises(ValueError):
        SourceDeliveryLedger().register_completed(KEY, lines, receipt_id="call")


def test_missing_requested_lines_cannot_be_assumed_known():
    ledger = SourceDeliveryLedger()
    ledger.register_completed(KEY, LINES, receipt_id="call-1")
    for lines in ({}, {1: LINES[1], 3: LINES[3]}, {3: LINES[3], 4: LINES[4]}):
        with pytest.raises(ValueError, match="provided"):
            ledger.render(KEY, lines, start=1, end=2)


@pytest.mark.parametrize("kwargs", [{"max_chars": 0}, {"max_lines": -1}, {"max_lines": True}])
def test_invalid_bounds(kwargs):
    with pytest.raises(ValueError):
        SourceDeliveryLedger(**kwargs)


def test_non_string_source_rejected():
    with pytest.raises(TypeError):
        SourceDeliveryLedger().register_completed(KEY, {1: None}, receipt_id="call")
