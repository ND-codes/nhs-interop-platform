"""The dead-letter path holds complete raw HL7 messages, which is every PID
field including the ones the mapper drops. It had no expiry, while
docs/security.md claimed a storage-limitation control. These tests pin the
retention window and the gauge that makes it auditable.
"""
from __future__ import annotations

import os
import time

import pytest
from prometheus_client import REGISTRY

import main

RAW = (
    "MSH|^~\\&|EPIC|RGT|INTEROP|RGT|20261109120000||ADT^A01|MSG00001|P|2.5.1\r"
    "PID|1||9000000009^^^NHS^NH||DOE^JOHN^A||19800101|M\r"
)


@pytest.fixture
def transform(tmp_path, monkeypatch):
    """Point the DLQ at a temporary directory for the duration of one test.

    The module attributes are patched rather than the environment, because
    re-importing main would register every metric in the default collector
    registry a second time and raise Duplicated timeseries.
    """
    monkeypatch.setattr(main, "DLQ_PATH", tmp_path)
    monkeypatch.setattr(main, "DLQ_RETENTION_HOURS", 72)
    return main


def write_aged(main, name: str, hours_old: float) -> None:
    path = main.DLQ_PATH / name
    path.write_text(RAW, encoding="utf-8")
    when = time.time() - (hours_old * 3600)
    os.utime(path, (when, when))


def test_a_message_inside_the_window_survives(transform) -> None:
    write_aged(transform, "recent.hl7", 1)
    transform._prune_dlq()
    assert (transform.DLQ_PATH / "recent.hl7").exists()


def test_a_message_past_the_window_is_deleted(transform) -> None:
    write_aged(transform, "stale.hl7", 73)
    pruned = transform._prune_dlq()
    assert pruned == 1
    assert not (transform.DLQ_PATH / "stale.hl7").exists()


def test_the_boundary_is_not_off_by_one(transform) -> None:
    """71 hours stays, 73 goes. The hour either side of the cutoff is where a
    retention rule is most likely to be wrong and least likely to be noticed.
    """
    write_aged(transform, "keep.hl7", 71)
    write_aged(transform, "drop.hl7", 73)
    transform._prune_dlq()
    assert (transform.DLQ_PATH / "keep.hl7").exists()
    assert not (transform.DLQ_PATH / "drop.hl7").exists()


def test_writing_a_new_message_prunes_the_old_ones(transform) -> None:
    write_aged(transform, "stale.hl7", 100)
    transform._dlq(RAW, "new-trace", reason="fhir_write_error")
    assert (transform.DLQ_PATH / "new-trace.hl7").exists()
    assert not (transform.DLQ_PATH / "stale.hl7").exists()


def test_the_oldest_age_gauge_reports_the_survivor(transform) -> None:
    """A retention rule nothing measures is not a control. This is the number
    an auditor can be shown and an alert can watch."""
    write_aged(transform, "old-but-valid.hl7", 48)
    write_aged(transform, "newer.hl7", 2)
    transform._prune_dlq()
    age = REGISTRY.get_sample_value("hl7_dlq_oldest_seconds")
    assert age is not None
    assert 47 * 3600 < age < 49 * 3600


def test_the_gauge_keeps_rising_when_nothing_prunes(transform) -> None:
    """The case the alert exists for: a message is dead-lettered, then nothing
    else is written and the service never restarts, so pruning never runs
    again. The first version set the gauge only when pruning ran, so it froze
    at the value from that moment and DLQMessagesPastRetention (80 hours)
    could never fire. Ageing the file on disk stands in for the clock moving
    on, with no prune in between."""
    write_aged(transform, "stuck.hl7", 1)
    transform._prune_dlq()
    assert REGISTRY.get_sample_value("hl7_dlq_oldest_seconds") < 2 * 3600

    write_aged(transform, "stuck.hl7", 81)
    age = REGISTRY.get_sample_value("hl7_dlq_oldest_seconds")
    assert age > 288000, "the alert threshold in alerts.yml must be reachable"
    assert (transform.DLQ_PATH / "stuck.hl7").exists()


def test_the_gauge_is_zero_on_an_empty_queue(transform) -> None:
    transform._prune_dlq()
    assert REGISTRY.get_sample_value("hl7_dlq_oldest_seconds") == 0.0


def test_only_hl7_files_are_pruned(transform) -> None:
    """Pruning globs *.hl7. Anything else in that directory is not ours to
    delete, and a prune that removes unexpected files is a worse bug than one
    that leaves them."""
    write_aged(transform, "stale.hl7", 100)
    other = transform.DLQ_PATH / "README.txt"
    other.write_text("not a message", encoding="utf-8")
    os.utime(other, (time.time() - 1000 * 3600,) * 2)
    transform._prune_dlq()
    assert other.exists()
    assert not (transform.DLQ_PATH / "stale.hl7").exists()
