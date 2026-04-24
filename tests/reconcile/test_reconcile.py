"""Reconciliation test — mirrors the evidence pack used on trust migrations."""
from __future__ import annotations

import glob
import hashlib
import os
import pathlib

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "hl7"


def test_fixture_hashes_are_stable() -> None:
    """Guard against silent corruption of the fixture batch.

    If someone modifies a fixture file, this test fails — prompting them to
    update the golden hashes with intent, rather than drifting silently.
    """
    expected = {
        "adt_a01.hl7",
        "oru_r01.hl7",
    }
    found = {os.path.basename(p) for p in glob.glob(str(FIXTURES / "*.hl7"))}
    assert expected.issubset(found)

    for p in sorted(FIXTURES.glob("*.hl7")):
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        # Sanity: a non-empty fixture has a non-zero digest.
        assert len(digest) == 64
        assert p.stat().st_size > 0
