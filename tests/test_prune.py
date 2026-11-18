import json
from datetime import datetime, timedelta, timezone

from tools.prune_sessions import (
    EMERGENCY_DELETE_COUNT,
    main,
    parse_timestamp,
    plan_deletions,
)

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def ago(**kwargs):
    return NOW - timedelta(**kwargs)


def write_session(directory, sid, last_active):
    payload = {"schema_version": 1, "created_at": "2026-11-01T00:00:00+00:00", "semesters": {}}
    if last_active is not None:
        payload["last_active"] = last_active.isoformat()
    (directory / f"{sid}.json").write_text(json.dumps(payload))


def test_parse_timestamp_variants():
    assert parse_timestamp("2026-11-17T12:00:00+00:00") == NOW
    assert parse_timestamp("2026-11-17T12:00:00Z") == NOW
    assert parse_timestamp("2026-11-17T12:00:00") == NOW  # naive is treated as UTC
    assert parse_timestamp("garbage") is None
    assert parse_timestamp(None) is None


def test_only_sessions_older_than_three_days_expire():
    sessions = {
        "fresh": ago(hours=1),
        "two_days": ago(days=2, hours=23),
        "just_over": ago(days=3, minutes=1),
        "old": ago(days=30),
    }
    expired, emergency = plan_deletions(sessions, NOW, "scheduled", size_mb=1)
    assert expired == ["just_over", "old"]
    assert emergency == []


def test_active_session_is_never_deleted_in_scheduled_mode():
    sessions = {"active": ago(minutes=5)}
    assert plan_deletions(sessions, NOW, "scheduled", size_mb=1) == ([], [])


def test_emergency_deletes_exactly_the_fifteen_least_recently_active():
    sessions = {f"s{i:02d}": ago(hours=i + 1) for i in range(40)}  # s00 newest ... s39 oldest, all < 3 days
    expired, emergency = plan_deletions(sessions, NOW, "emergency", size_mb=1)
    assert expired == []
    assert len(emergency) == EMERGENCY_DELETE_COUNT == 15
    assert set(emergency) == {f"s{i:02d}" for i in range(25, 40)}
    assert "s00" not in emergency  # most recently active session is untouched


def test_emergency_batch_comes_after_expired_ones_are_removed():
    sessions = {f"new{i}": ago(hours=i + 1) for i in range(20)}
    sessions.update({f"old{i}": ago(days=10 + i) for i in range(5)})
    expired, emergency = plan_deletions(sessions, NOW, "emergency", size_mb=1)
    assert len(expired) == 5
    assert len(emergency) == 15
    assert not set(expired) & set(emergency)
    assert all(sid.startswith("new") for sid in emergency)


def test_size_over_cap_triggers_emergency_even_in_scheduled_mode():
    sessions = {f"s{i}": ago(hours=i + 1) for i in range(20)}
    _, below = plan_deletions(sessions, NOW, "scheduled", size_mb=199.9)
    _, above = plan_deletions(sessions, NOW, "scheduled", size_mb=200.0)
    assert below == []
    assert len(above) == 15


def test_fewer_sessions_than_batch_size(tmp_path):
    sessions = {"a": ago(hours=1), "b": ago(hours=2)}
    _, emergency = plan_deletions(sessions, NOW, "emergency", size_mb=None)
    assert sorted(emergency) == ["a", "b"]


def test_cli_deletes_files_and_keeps_unreadable_ones(tmp_path, capsys):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    write_session(sessions_dir, "fresh", ago(hours=2))
    write_session(sessions_dir, "stale", ago(days=5))
    write_session(sessions_dir, "nostamp", None)
    (sessions_dir / "broken.json").write_text("{not json")

    assert main(["--sessions-dir", str(sessions_dir), "--now", NOW.isoformat()]) == 0
    remaining = sorted(p.stem for p in sessions_dir.glob("*.json"))
    assert remaining == ["broken", "fresh", "nostamp"]
    summary = json.loads(capsys.readouterr().out)
    assert summary["expired_deleted"] == ["stale"]
    assert sorted(summary["skipped_unreadable"]) == ["broken", "nostamp"]


def test_cli_dry_run_changes_nothing(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    write_session(sessions_dir, "stale", ago(days=5))
    assert main(["--sessions-dir", str(sessions_dir), "--now", NOW.isoformat(), "--dry-run"]) == 0
    assert (sessions_dir / "stale.json").exists()


def test_cli_writes_deleted_count_for_the_workflow(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    write_session(sessions_dir, "stale", ago(days=5))
    output = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    main(["--sessions-dir", str(sessions_dir), "--now", NOW.isoformat()])
    assert "deleted_count=1" in output.read_text()


def test_cli_missing_directory_is_not_an_error(tmp_path):
    assert main(["--sessions-dir", str(tmp_path / "nope")]) == 0
