"""GA-03: Copilot monthly premium usage counts each event ID/model once."""

import json
from datetime import datetime, timezone

import pytest

from desktop import quota

NOW = datetime(2026, 10, 14, 1, tzinfo=timezone.utc)


def shutdown(event_id="synthetic-event", models=None, timestamp="2026-10-10T01:00:00Z"):
    return {"type": "session.shutdown", "id": event_id, "timestamp": timestamp,
            "data": {"modelMetrics": {
                name: {"requests": {"cost": cost}}
                for name, cost in (models or {"synthetic-model": 7}).items()
            }}}


def write_events(root, name, events):
    path = root / name / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return path


@pytest.mark.parametrize("same_file", [True, False])
def test_ga03_copied_shutdown_counts_once_in_month_and_remaining_quota(tmp_path, same_file):
    event = shutdown()
    write_events(tmp_path, "session-a", [event, event] if same_file else [event])
    if not same_file:
        write_events(tmp_path, "session-copy", [event])
    usage = quota.copilot_premium_requests_this_month(tmp_path, NOW)
    assert usage["status"] == "ok"
    assert usage["used"] == usage["known_used"] == 7
    assert quota.copilot_windows(100, usage, NOW)[0]["remaining_pct"] == 93


def test_ga03_shared_event_id_keeps_distinct_models_once_each(tmp_path):
    write_events(tmp_path, "session-a", [shutdown(models={"model-a": 2, "model-b": 5})])
    write_events(tmp_path, "session-copy", [shutdown(models={"model-a": 2})])
    usage = quota.copilot_premium_requests_this_month(tmp_path, NOW)
    assert usage["used"] == 7


def test_ga03_distinct_event_ids_for_the_same_model_both_count(tmp_path):
    write_events(tmp_path, "session-a", [shutdown("event-a"), shutdown("event-b")])
    assert quota.copilot_premium_requests_this_month(tmp_path, NOW)["used"] == 14


@pytest.mark.parametrize("event_id", [None, "", 123, "x" * 513])
def test_ga03_without_a_valid_event_id_distinct_events_are_not_collapsed(tmp_path, event_id):
    write_events(tmp_path, "session-a", [shutdown(event_id), shutdown(event_id, models={"synthetic-model": 3})])
    assert quota.copilot_premium_requests_this_month(tmp_path, NOW)["used"] == 10


def test_ga03_missing_event_id_does_not_form_a_shared_empty_key(tmp_path):
    event = shutdown()
    event.pop("id")
    write_events(tmp_path, "session-a", [event])
    write_events(tmp_path, "session-b", [event])
    assert quota.copilot_premium_requests_this_month(tmp_path, NOW)["used"] == 14


def test_ga03_valid_event_id_at_collector_length_boundary_deduplicates(tmp_path):
    event = shutdown("x" * 512)
    write_events(tmp_path, "session-a", [event, event])
    assert quota.copilot_premium_requests_this_month(tmp_path, NOW)["used"] == 7


def test_ga03_copied_event_after_file_reappears_and_repeat_scan_counts_once(tmp_path):
    event = shutdown()
    original = write_events(tmp_path, "session-a", [event])
    write_events(tmp_path, "session-copy", [event])
    assert quota.copilot_premium_requests_this_month(tmp_path, NOW)["used"] == 7
    original.unlink()
    assert quota.copilot_premium_requests_this_month(tmp_path, NOW)["used"] == 7
    write_events(tmp_path, "session-a", [event])
    assert quota.copilot_premium_requests_this_month(tmp_path, NOW)["used"] == 7


def test_ga03_out_of_month_copy_cannot_hide_the_current_month_event(tmp_path):
    write_events(tmp_path, "session-a", [shutdown(timestamp="2026-09-30T23:59:59Z"), shutdown(), shutdown()])
    assert quota.copilot_premium_requests_this_month(tmp_path, NOW)["used"] == 7


def test_ga03_partial_usage_preserves_unique_known_sum_and_unavailable_remaining(tmp_path):
    write_events(tmp_path, "session-a", [shutdown(), shutdown(), shutdown("bad-cost", {"model-a": None})])
    usage = quota.copilot_premium_requests_this_month(tmp_path, NOW)
    assert usage["status"] == "partial"
    assert usage["known_used"] == 7
    assert usage["used"] is None
    assert quota.copilot_windows(100, usage, NOW)[0]["remaining_pct"] is None
