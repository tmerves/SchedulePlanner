import base64
import json

import httpx
import pytest

from core.session import ScheduleSessionManager
from core.storage import (
    DISPATCH_COOLDOWN_SECONDS,
    SIZE_CACHE_SECONDS,
    GitHubStore,
    LocalFileStore,
    build_store,
    state_hash,
)
from models.schema import Course, Section
from utils.time_utils import parse_time_blocks

REPO = "owner/data"


class FakeGitHub:
    """Minimal in-memory stand-in for the GitHub Contents API."""

    def __init__(self):
        self.files = {}
        self.shas = {}
        self.counter = 0
        self.requests = []
        self.fail_mode = None  # None, "500" or "429"
        self.size_kb = 1024
        self.stale_sha_once = False

    def _sid(self, path):
        return path.rsplit("/", 1)[-1].removesuffix(".json")

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        if self.fail_mode == "500":
            return httpx.Response(500, json={"message": "boom"})
        if self.fail_mode == "429":
            return httpx.Response(429, headers={"retry-after": "60"}, json={"message": "limited"})

        if path == f"/repos/{REPO}" and request.method == "GET":
            return httpx.Response(200, json={"size": self.size_kb})
        if path.endswith("/dispatches") and request.method == "POST":
            return httpx.Response(204)
        if "/contents/sessions/" in path:
            sid = self._sid(path)
            if request.method == "GET":
                if sid not in self.files:
                    return httpx.Response(404, json={"message": "Not Found"})
                return httpx.Response(200, json={
                    "content": base64.b64encode(self.files[sid]).decode(),
                    "encoding": "base64",
                    "sha": self.shas[sid],
                })
            if request.method == "PUT":
                body = json.loads(request.content)
                if self.stale_sha_once:
                    self.stale_sha_once = False
                    return httpx.Response(409, json={"message": "conflict"})
                if sid in self.files and body.get("sha") != self.shas[sid]:
                    return httpx.Response(409 if body.get("sha") else 422, json={"message": "sha"})
                self.counter += 1
                self.files[sid] = base64.b64decode(body["content"])
                self.shas[sid] = f"sha{self.counter}"
                return httpx.Response(200, json={"content": {"sha": self.shas[sid]}})
            if request.method == "DELETE":
                body = json.loads(request.content)
                if self.shas.get(sid) == body["sha"]:
                    self.files.pop(sid)
                    self.shas.pop(sid)
                    return httpx.Response(200, json={})
                return httpx.Response(409, json={})
        return httpx.Response(404, json={})

    def puts(self):
        return [r for r in self.requests if r[0] == "PUT"]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def gh():
    return FakeGitHub()


@pytest.fixture
def clock():
    return Clock()


def make_store(gh, clock, tmp_path, name="local", **kwargs):
    client = httpx.Client(transport=httpx.MockTransport(gh.handler))
    return GitHubStore(
        token="t",
        repo=REPO,
        client=client,
        local=LocalFileStore(str(tmp_path / name)),
        clock=clock,
        start_thread=False,
        **kwargs,
    )


def sample_state(n=1):
    return {"schema_version": 1, "created_at": "2026-01-01T00:00:00+00:00",
            "last_active": "2026-01-01T00:00:00+00:00", "active_term": "0009",
            "semesters": {"0009": {"selected_courses": [], "excluded_section_ids": [f"s{n}"],
                                   "saved_schedules": [], "term_code": "0009"}}}


def test_local_store_round_trip_and_quarantine(tmp_path):
    store = LocalFileStore(str(tmp_path))
    assert store.load("abcdef123456") is None
    store.save("abcdef123456", sample_state())
    assert store.load("abcdef123456")["active_term"] == "0009"
    moved = store.quarantine("abcdef123456")
    assert moved is not None and moved.exists()
    assert store.load("abcdef123456") is None


def test_session_survives_simulated_redeploy(gh, clock, tmp_path):
    """Save, wipe the container disk, build a fresh store, and the session comes back."""
    store = make_store(gh, clock, tmp_path, name="before")
    store.save("abcdef123456", sample_state(3))
    assert store.flush_due() == 1

    fresh = make_store(gh, clock, tmp_path, name="after_redeploy")  # empty local disk
    loaded = fresh.load("abcdef123456")
    assert loaded["semesters"]["0009"]["excluded_section_ids"] == ["s3"]


def test_manager_state_round_trips_through_store(gh, clock, tmp_path):
    course = Course(course_id="CS 101", title="Intro", subject="CS", term_code="0009", sections=[
        Section(section_id="1", course_id="CS 101", instructor="A", term_code="0009",
                meeting_times=parse_time_blocks("M", "09:00 - 10:00"))])
    manager = ScheduleSessionManager(cache_dir=str(tmp_path / "c"), default_term="0009")
    manager.add_course(course)
    store = make_store(gh, clock, tmp_path)
    store.save("abcdef123456", manager.export_state())
    store.flush_due()

    restored = ScheduleSessionManager(cache_dir=str(tmp_path / "c"), default_term="0009")
    restored.import_state(make_store(gh, clock, tmp_path, name="other").load("abcdef123456"))
    assert list(restored.selected_courses) == ["CS 101"]
    assert restored.created_at == manager.created_at


def test_rapid_saves_are_coalesced_into_one_write(gh, clock, tmp_path):
    store = make_store(gh, clock, tmp_path)
    store.save("abcdef123456", sample_state(0))
    assert store.flush_due() == 1  # first write is immediate
    for n in range(1, 51):
        store.save("abcdef123456", sample_state(n))
    clock.t += 5
    assert store.flush_due() == 0  # still inside the 10 second window
    clock.t += 6
    assert store.flush_due() == 1
    assert store.flush_due() == 0
    assert len(gh.puts()) == 2
    written = json.loads(gh.files["abcdef123456"])
    assert written["semesters"]["0009"]["excluded_section_ids"] == ["s50"]  # newest state won


def test_unchanged_state_makes_no_requests(gh, clock, tmp_path):
    store = make_store(gh, clock, tmp_path)
    store.save("abcdef123456", sample_state())
    store.flush_due()
    before = len(gh.requests)
    same_but_touched = sample_state()
    same_but_touched["last_active"] = "2026-02-02T00:00:00+00:00"
    store.save("abcdef123456", same_but_touched)
    clock.t += 100
    assert store.flush_due() == 0
    assert len(gh.requests) == before
    assert state_hash(sample_state()) == state_hash(same_but_touched)


def test_force_writes_even_when_unchanged(gh, clock, tmp_path):
    store = make_store(gh, clock, tmp_path)
    store.save("abcdef123456", sample_state())
    store.flush_due()
    clock.t += 100
    store.save("abcdef123456", sample_state(), force=True)
    assert store.flush_due() == 1


def test_global_write_spacing_across_sessions(gh, clock, tmp_path):
    store = make_store(gh, clock, tmp_path)
    for sid in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"):
        store.save(sid, sample_state())
    assert store.flush_due() == 1
    assert store.flush_due() == 0  # same instant, global 1s spacing
    clock.t += 1.0
    assert store.flush_due() == 1
    clock.t += 1.0
    assert store.flush_due() == 1
    assert len(gh.puts()) == 3


def test_stale_sha_conflict_is_retried_once(gh, clock, tmp_path):
    store = make_store(gh, clock, tmp_path)
    store.save("abcdef123456", sample_state(1))
    store.flush_due()
    gh.stale_sha_once = True
    clock.t += 20
    store.save("abcdef123456", sample_state(2))
    assert store.flush_due() == 1
    assert json.loads(gh.files["abcdef123456"])["semesters"]["0009"]["excluded_section_ids"] == ["s2"]


def test_circuit_breaker_opens_after_three_failures(gh, clock, tmp_path):
    gh.fail_mode = "500"
    store = make_store(gh, clock, tmp_path)
    store.save("abcdef123456", sample_state())
    for _ in range(3):
        assert store.flush_due() == 1
        clock.t += 11
    requests_after_open = len(gh.requests)
    # Breaker is open: no further requests even as time passes within the pause
    for _ in range(5):
        clock.t += 20
        store.flush_due()
    assert len(gh.requests) == requests_after_open
    # After the pause one attempt is made again, and success closes the breaker
    gh.fail_mode = None
    clock.t += 400
    assert store.flush_due() == 1
    assert "abcdef123456" in gh.files


def test_rate_limit_pauses_writes_immediately(gh, clock, tmp_path):
    gh.fail_mode = "429"
    store = make_store(gh, clock, tmp_path)
    store.save("abcdef123456", sample_state())
    store.flush_due()
    n = len(gh.requests)
    gh.fail_mode = None
    clock.t += 60  # Retry-After was 60 but the minimum pause is 300
    store.flush_due()
    assert len(gh.requests) == n
    clock.t += 300
    assert store.flush_due() == 1


def test_failed_remote_keeps_local_cache_and_load_falls_back(gh, clock, tmp_path):
    gh.fail_mode = "500"
    store = make_store(gh, clock, tmp_path)
    store.save("abcdef123456", sample_state(7))
    store.flush_due()
    loaded = store.load("abcdef123456")  # remote failing, local cache answers
    assert loaded["semesters"]["0009"]["excluded_section_ids"] == ["s7"]


def test_load_404_without_local_copy_is_a_new_session(gh, clock, tmp_path):
    store = make_store(gh, clock, tmp_path)
    assert store.load("abcdef123456") is None


def test_load_404_keeps_and_requeues_unsynced_local_copy(gh, clock, tmp_path):
    store = make_store(gh, clock, tmp_path)
    store.local.save("abcdef123456", sample_state(4))
    loaded = store.load("abcdef123456")
    assert loaded["semesters"]["0009"]["excluded_section_ids"] == ["s4"]
    assert store.local.load("abcdef123456") is not None  # not deleted
    assert store.flush_due() == 1
    assert "abcdef123456" in gh.files  # remote caught up


def test_unreachable_remote_without_local_copy_is_not_a_new_session(gh, clock, tmp_path):
    from core.storage import SessionUnavailable
    gh.fail_mode = "500"
    store = make_store(gh, clock, tmp_path)
    with pytest.raises(SessionUnavailable):
        store.load("abcdef123456")


def test_open_breaker_without_local_copy_raises_unavailable(gh, clock, tmp_path):
    from core.storage import SessionUnavailable
    gh.fail_mode = "429"
    store = make_store(gh, clock, tmp_path)
    with pytest.raises(SessionUnavailable):
        store.load("abcdef123456")
    with pytest.raises(SessionUnavailable):  # breaker now open, no new request
        n = len(gh.requests)
        try:
            store.load("abcdef123456")
        finally:
            assert len(gh.requests) == n


def test_transient_error_on_raw_fetch_falls_back_not_corrupt(gh, clock, tmp_path):
    """A timeout on the second request must not be reported as a corrupt session."""
    store = make_store(gh, clock, tmp_path)
    big = sample_state(9)
    store.local.save("abcdef123456", big)

    def handler(request):
        if request.headers.get("accept") == "application/vnd.github.raw+json":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"content": "", "encoding": "none", "sha": "s"})

    store._client = httpx.Client(transport=httpx.MockTransport(handler))
    assert store.load("abcdef123456")["semesters"]["0009"]["excluded_section_ids"] == ["s9"]


def test_invalid_remote_json_raises_value_error(gh, clock, tmp_path):
    store = make_store(gh, clock, tmp_path)
    gh.files["abcdef123456"] = b"{not json"
    gh.shas["abcdef123456"] = "sha1"
    with pytest.raises(ValueError):
        store.load("abcdef123456")


def test_repo_size_is_cached_and_prune_dispatched_once_per_hour(gh, clock, tmp_path):
    gh.size_kb = 300 * 1024  # 300 MB, over the 200 MB cap
    store = make_store(gh, clock, tmp_path)
    assert store.guard_new_session() == "over_cap"
    for _ in range(20):
        clock.t += 1
        assert store.guard_new_session() == "over_cap"
    size_lookups = [r for r in gh.requests if r == ("GET", f"/repos/{REPO}")]
    dispatches = [r for r in gh.requests if r[1].endswith("/dispatches")]
    assert len(size_lookups) == 1
    assert len(dispatches) == 1

    clock.t += SIZE_CACHE_SECONDS + 1
    store.guard_new_session()
    assert len([r for r in gh.requests if r == ("GET", f"/repos/{REPO}")]) == 2
    assert len([r for r in gh.requests if r[1].endswith("/dispatches")]) == 1  # hour not up

    clock.t += DISPATCH_COOLDOWN_SECONDS
    store.guard_new_session()
    assert len([r for r in gh.requests if r[1].endswith("/dispatches")]) == 2


def test_under_cap_does_not_dispatch(gh, clock, tmp_path):
    store = make_store(gh, clock, tmp_path)
    assert store.guard_new_session() == "ok"
    assert not [r for r in gh.requests if r[1].endswith("/dispatches")]


def test_delete_removes_remote_file(gh, clock, tmp_path):
    store = make_store(gh, clock, tmp_path)
    store.save("abcdef123456", sample_state())
    store.flush_due()
    store.delete("abcdef123456")
    assert "abcdef123456" not in gh.files


def test_build_store_selects_backend(tmp_path):
    assert isinstance(build_store(None, str(tmp_path)), LocalFileStore)
    assert isinstance(build_store({}, str(tmp_path)), LocalFileStore)
    assert isinstance(build_store({"session_store": {"token": "", "repo": ""}}, str(tmp_path)), LocalFileStore)


def test_needs_touch_only_after_24_hours():
    from datetime import datetime, timedelta, timezone
    from core.storage import needs_touch
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    assert not needs_touch((now - timedelta(hours=23)).isoformat(), now)
    assert needs_touch((now - timedelta(hours=25)).isoformat(), now)
    assert needs_touch(None, now)
    assert needs_touch("not a date", now)


def test_revert_during_inflight_write_is_not_dropped(gh, clock, tmp_path):
    """Remote holds Y; user changes to X (write in flight) and reverts to Y. Y must still be saved."""
    store = make_store(gh, clock, tmp_path)
    y, x = sample_state(1), sample_state(2)
    store.save("abcdef123456", y)
    store.flush_due()
    clock.t += 20
    store.save("abcdef123456", x)

    original_put = store._client.put

    def put_with_revert(*args, **kwargs):
        store.save("abcdef123456", y)  # user reverts while the X write is on the wire
        return original_put(*args, **kwargs)

    store._client.put = put_with_revert
    assert store.flush_due() == 1  # X is written
    store._client.put = original_put
    clock.t += 20
    assert store.flush_due() == 1  # Y is written afterwards
    final = json.loads(gh.files["abcdef123456"])
    assert final["semesters"]["0009"]["excluded_section_ids"] == ["s1"]


def test_concurrent_local_saves_to_one_session_do_not_collide(tmp_path):
    import threading
    store = LocalFileStore(str(tmp_path))
    errors = []

    def worker(n):
        try:
            for i in range(40):
                store.save("abcdef123456", sample_state(n * 100 + i))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert store.load("abcdef123456") is not None  # valid JSON, not interleaved
    assert not list((tmp_path / "abcdef123456").glob("*.tmp"))


def test_flush_all_keeps_state_queued_when_write_fails(gh, clock, tmp_path):
    store = make_store(gh, clock, tmp_path)
    store.save("abcdef123456", sample_state(5))
    gh.fail_mode = "500"
    store.flush_all()
    assert "abcdef123456" in store._pending  # not dropped
    gh.fail_mode = None
    clock.t += 1000  # breaker pause over
    store._stopped = False
    assert store.flush_due() == 1
    assert "abcdef123456" in gh.files


def test_flush_all_writes_pending_state(gh, clock, tmp_path):
    store = make_store(gh, clock, tmp_path)
    store.save("abcdef123456", sample_state(6))
    store.save("bbbbbbbbbbbb", sample_state(7))
    store.flush_all()
    assert set(gh.files) == {"abcdef123456", "bbbbbbbbbbbb"}
    assert not store._pending


def test_delete_failure_is_reported_and_feeds_the_breaker(gh, clock, tmp_path):
    store = make_store(gh, clock, tmp_path)
    store.save("abcdef123456", sample_state())
    store.flush_due()
    original = gh.handler

    def forbidden_delete(request):
        if request.method == "DELETE":
            return httpx.Response(403, json={"message": "no"})
        return original(request)

    store._client = httpx.Client(transport=httpx.MockTransport(forbidden_delete))
    assert store.delete("abcdef123456") is False
    assert "abcdef123456" in gh.files  # remote copy survived
    assert store._breaker_open(clock())  # 403 paused further remote calls
    assert store._sha.get("abcdef123456")  # cached sha is kept for a later retry


def test_delete_success_clears_remote(gh, clock, tmp_path):
    store = make_store(gh, clock, tmp_path)
    store.save("abcdef123456", sample_state())
    store.flush_due()
    assert store.delete("abcdef123456") is True
    assert "abcdef123456" not in gh.files
