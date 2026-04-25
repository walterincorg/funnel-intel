from types import SimpleNamespace

import pytest

from backend.worker import recorder


class _RecordingTable:
    def __init__(self, select_data=None):
        self.select_data = select_data or []
        self.upsert_payload = None
        self.upsert_on_conflict = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def upsert(self, payload, on_conflict=None):
        self.upsert_payload = payload
        self.upsert_on_conflict = on_conflict
        return self

    def execute(self):
        if self.upsert_payload is not None:
            return SimpleNamespace(data=[self.upsert_payload])
        return SimpleNamespace(data=self.select_data)


class _FakeDb:
    def __init__(self, recording_table):
        self.recording_table = recording_table

    def table(self, name):
        assert name == "funnel_recordings"
        return self.recording_table


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], False),
        ([{"competitor_id": "comp-1", "is_stale": False}], True),
        ([{"competitor_id": "comp-1", "is_stale": True}], False),
    ],
)
def test_has_recording_only_counts_non_stale_rows(monkeypatch, rows, expected):
    table = _RecordingTable(select_data=rows)
    monkeypatch.setattr(recorder, "get_db", lambda: _FakeDb(table))

    assert recorder.has_recording("comp-1") is expected


def test_save_recording_replaces_stale_row_with_fresh_recording(monkeypatch):
    table = _RecordingTable()
    monkeypatch.setattr(recorder, "get_db", lambda: _FakeDb(table))
    monkeypatch.setattr(
        recorder,
        "steps_to_action_log",
        lambda steps: [{"type": "click", "selector": "#next"}],
    )

    result = recorder.save_recording("comp-1", [{}, {}, {}])

    assert result["competitor_id"] == "comp-1"
    assert table.upsert_on_conflict == "competitor_id"
    assert table.upsert_payload["is_stale"] is False
    assert table.upsert_payload["patch_count"] == 0
    assert table.upsert_payload["action_log"] == [{"type": "click", "selector": "#next"}]
