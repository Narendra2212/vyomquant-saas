"""Re-recording one baseline entry must not destroy the authorisation for the others.

THE DEFECT
----------
``capture_baseline.write_baseline`` writes a fresh capture over the baseline file. A capture
never contains a ``_baseline_note_*`` member -- a note is a human statement about why a
recorded contract was *allowed* to change -- so the write deleted every note the file
carried.

Nothing caught it. ``test_baseline_unchanged``'s differ deliberately skips notes on the
baseline side, precisely so that a note is not reported as a key the re-run removed. So the
documented way to re-record one entry silently erased the provenance of every entry
re-recorded before it, and the guard could not see it.

Found by re-recording ``surface_backtester`` for one added ``api_calls`` entry: that single
write dropped four notes -- two top-level (the plan-ladder re-record and RL-02) and two
nested ``_baseline_note_handlers`` on ``GET /api/strategies`` and
``GET /api/strategies/{id}``.

THE LINE THIS MUST NOT CROSS
----------------------------
Carrying members forward is only safe for notes. If it carried a *contract* key forward it
would become a way to hide a regression: a removed key would reappear in the file and the
differ would compare the re-run against a baseline that no longer describes anything real.
``test_a_removed_contract_key_is_not_resurrected`` is the test that matters here.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from tests.regression import capture_baseline
from tests.regression.capture_baseline import carry_forward_notes, write_baseline


# ---------------------------------------------------------------------------
# Fixtures: a baseline shaped like the real one that exposed this
# ---------------------------------------------------------------------------

def _previous() -> Dict[str, Any]:
    """Two top-level notes and one nested on an identity-bearing record."""
    return {
        "_baseline_note_plan_ladder": "AUTHORISED: the quota dependency.",
        "_baseline_note_rl02": "AUTHORISED: the restored rate limit.",
        "api_call_count": 2,
        "api_calls": [
            {
                "method": "GET",
                "path": "/api/strategies",
                "path_template": "/api/strategies",
                "via": "api.strategies.list",
                "resolved": True,
                "_baseline_note_handlers": "AUTHORISED (API-01): shadowed duplicate removed.",
            },
            {
                "method": "GET",
                "path": "/api/strategies/{id}",
                "path_template": "/api/strategies/{id}",
                "via": "api.strategies.getById",
                "resolved": True,
            },
        ],
    }


def _recaptured_with_an_inserted_entry() -> Dict[str, Any]:
    """What the capture answers after the surface starts calling one more endpoint.

    The new entry sorts FIRST, so every pre-existing record shifts position. This is the
    case that makes position useless as a way to replace a nested note.
    """
    return {
        "api_call_count": 3,
        "api_calls": [
            {
                "method": "DELETE",
                "path": "/api/backtests/{expr}",
                "path_template": "/api/backtests/{expr}",
                "via": "api.strategies.deleteBacktest",
                "resolved": True,
            },
            {
                "method": "GET",
                "path": "/api/strategies",
                "path_template": "/api/strategies",
                "via": "api.strategies.list",
                "resolved": True,
            },
            {
                "method": "GET",
                "path": "/api/strategies/{id}",
                "path_template": "/api/strategies/{id}",
                "via": "api.strategies.getById",
                "resolved": True,
            },
        ],
    }


# ---------------------------------------------------------------------------
# The carry-forward itself
# ---------------------------------------------------------------------------

class TestNotesAreCarriedForward:

    def test_top_level_notes_survive(self) -> None:
        merged = carry_forward_notes(_previous(), _recaptured_with_an_inserted_entry())

        assert merged["_baseline_note_plan_ladder"] == "AUTHORISED: the quota dependency."
        assert merged["_baseline_note_rl02"] == "AUTHORISED: the restored rate limit."

    def test_a_nested_note_follows_its_record_and_not_its_position(self) -> None:
        """The record it belongs to moved from index 0 to index 1."""
        merged = carry_forward_notes(_previous(), _recaptured_with_an_inserted_entry())

        annotated = [e for e in merged["api_calls"] if "_baseline_note_handlers" in e]
        assert len(annotated) == 1, "exactly one record carried a note"
        # Matched by identity, so it is on GET /api/strategies -- NOT on the DELETE that
        # now occupies the index the annotated record used to sit at.
        assert annotated[0]["via"] == "api.strategies.list"
        assert annotated[0]["method"] == "GET"

    def test_the_capture_s_own_values_win_over_the_old_file(self) -> None:
        """A note is restored; a recorded contract value is never overwritten."""
        merged = carry_forward_notes(_previous(), _recaptured_with_an_inserted_entry())

        # The whole point of the re-record was that this changed.
        assert merged["api_call_count"] == 3

    def test_a_removed_contract_key_is_not_resurrected(self) -> None:
        """The line this must not cross -- see the module docstring.

        If anything other than a note were carried forward, this mechanism would become a
        way to paper over a regression.
        """
        previous = {
            "_baseline_note_x": "authorised",
            "a_real_key": "value the re-run no longer produces",
            "nested": {"_baseline_note_y": "authorised", "another_real_key": 1},
        }
        current: Dict[str, Any] = {"nested": {}}

        merged = carry_forward_notes(previous, current)

        assert merged["_baseline_note_x"] == "authorised"
        assert merged["nested"]["_baseline_note_y"] == "authorised"
        assert "a_real_key" not in merged
        assert "another_real_key" not in merged["nested"]

    def test_an_array_with_no_identity_is_left_alone(self) -> None:
        """``raises``/``returns`` are positional and carry no identity.

        Guessing a match on such an array could attach an authorisation to the wrong
        record, which is worse than losing it -- so it is not attempted.
        """
        previous = {"returns": [{"_baseline_note_z": "authorised", "shape": "a"}]}
        current = {"returns": [{"shape": "b"}]}

        merged = carry_forward_notes(previous, current)

        assert "_baseline_note_z" not in merged["returns"][0]


# ---------------------------------------------------------------------------
# write_baseline, which is where the defect actually lived
# ---------------------------------------------------------------------------

class TestWriteBaselinePreservesNotes:

    @pytest.fixture()
    def baseline_dir(self, tmp_path, monkeypatch):
        """A throwaway baseline directory.

        The real one must not be written to: ``test_baseline_unchanged`` asserts that every
        file in it has a registered capture, so a stray file there fails an unrelated guard.
        """
        monkeypatch.setattr(capture_baseline, "BASELINE_DIR", tmp_path)
        return tmp_path

    def test_a_rerecord_keeps_every_note_the_file_had(self, baseline_dir) -> None:
        path = baseline_dir / "surface_example.json"
        path.write_text(json.dumps(_previous(), indent=2, sort_keys=True), encoding="utf-8")

        write_baseline("surface_example", _recaptured_with_an_inserted_entry())

        written = json.loads(path.read_text(encoding="utf-8"))
        assert "_baseline_note_plan_ladder" in written
        assert "_baseline_note_rl02" in written
        assert written["api_call_count"] == 3
        annotated = [e for e in written["api_calls"] if "_baseline_note_handlers" in e]
        assert [e["via"] for e in annotated] == ["api.strategies.list"]

    def test_the_first_write_of_a_new_baseline_needs_no_previous_file(self, baseline_dir) -> None:
        write_baseline("surface_brand_new", {"api_call_count": 1})

        written = json.loads((baseline_dir / "surface_brand_new.json").read_text(encoding="utf-8"))
        assert written == {"api_call_count": 1}

    def test_a_note_whose_record_is_gone_is_reported_rather_than_dropped_quietly(
        self, baseline_dir, capsys
    ) -> None:
        """Sometimes the entry really should go. That is a decision, so it gets said out loud."""
        path = baseline_dir / "surface_example.json"
        path.write_text(json.dumps(_previous(), indent=2, sort_keys=True), encoding="utf-8")

        # The annotated endpoint is no longer called by this surface at all.
        shrunk = {
            "api_call_count": 1,
            "api_calls": [
                {
                    "method": "GET",
                    "path": "/api/strategies/{id}",
                    "path_template": "/api/strategies/{id}",
                    "via": "api.strategies.getById",
                    "resolved": True,
                }
            ],
        }
        write_baseline("surface_example", shrunk)

        reported = capsys.readouterr().out
        assert "_baseline_note_handlers" in reported
        assert "could not be carried forward" in reported
        # The top-level notes were still placed, so the warning is specific rather than
        # a blanket failure.
        written = json.loads(path.read_text(encoding="utf-8"))
        assert "_baseline_note_plan_ladder" in written

    def test_an_unreadable_existing_baseline_does_not_stop_the_write(
        self, baseline_dir, capsys
    ) -> None:
        path = baseline_dir / "surface_example.json"
        path.write_text("{ this is not json", encoding="utf-8")

        write_baseline("surface_example", {"api_call_count": 1})

        assert "unreadable" in capsys.readouterr().out
        assert json.loads(path.read_text(encoding="utf-8")) == {"api_call_count": 1}
