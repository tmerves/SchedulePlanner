"""
Session persistence backends.

Streamlit Community Cloud wipes the container disk on redeploys and reboots, so the
durable copy of each session lives in a private GitHub repository (``GitHubStore``).
The local disk is only a write-through cache. ``LocalFileStore`` is used for development,
tests, and whenever no GitHub credentials are configured.

Nothing in this module polls or loops on a timer. Remote work happens only when a
caller asks for it, and the single background writer thread sleeps on a condition
variable until a write is actually due.
"""
import atexit
import base64
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

import httpx

from core.session import get_user_session_filepath

logger = logging.getLogger(__name__)

# --- Tunables -------------------------------------------------------------
SIZE_CAP_MB = 200.0                 # data repo size that triggers the emergency prune
SIZE_CACHE_SECONDS = 600.0          # repo size is looked up at most once per 10 minutes
DISPATCH_COOLDOWN_SECONDS = 3600.0  # emergency prune is requested at most once per hour
PER_SESSION_INTERVAL = 10.0         # at most one remote write per session every 10 seconds
GLOBAL_INTERVAL = 1.0               # at most one remote write per second overall
BREAKER_THRESHOLD = 3               # consecutive failures before remote writes pause
BREAKER_PAUSE_SECONDS = 300.0       # minimum pause once the breaker opens
HTTP_TIMEOUT = 10.0
PRUNE_WORKFLOW_FILE = "prune.yml"


class SessionUnavailable(Exception):
    """The session store could not be read and there is no local copy to fall back on."""


def state_hash(state: Mapping[str, Any]) -> str:
    """Hash of a session state, ignoring last_active so a pure touch is not treated as a change."""
    body = {k: v for k, v in state.items() if k != "last_active"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


TOUCH_INTERVAL_HOURS = 24.0


def needs_touch(last_active: Optional[str], now: Optional[datetime] = None) -> bool:
    """True when a loaded session has not been written for 24 hours, so viewing it counts as activity."""
    if not last_active:
        return True
    try:
        parsed = datetime.fromisoformat(last_active.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - parsed).total_seconds() >= TOUCH_INTERVAL_HOURS * 3600


class LocalFileStore:
    """Stores each session as data/sessions/<id>/session_state.json."""

    def __init__(self, base_dir: str = "data/sessions"):
        self.base_dir = base_dir

    def _path(self, session_id: str) -> Path:
        return get_user_session_filepath(session_id, base_dir=self.base_dir)

    def load(self, session_id: str) -> Optional[dict]:
        path = self._path(session_id)
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save(self, session_id: str, state: dict, force: bool = False) -> None:
        path = self._path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Unique per process and thread so two tabs saving the same session cannot collide
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, path)

    def quarantine(self, session_id: str) -> Optional[Path]:
        """Moves an unreadable local file aside so it is kept but never overwritten."""
        path = self._path(session_id)
        if not path.exists():
            return None
        target = path.with_name(f"session_state.corrupt-{int(time.time())}.json")
        os.replace(path, target)
        return target

    def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        path.unlink(missing_ok=True)

    def repo_size_mb(self) -> Optional[float]:
        return None

    def guard_new_session(self, cap_mb: float = SIZE_CAP_MB) -> str:
        return "ok"


class GitHubStore:
    """
    Durable session store backed by a private GitHub repo (one file per session).

    Reads happen once per browser connection. Writes are coalesced: the newest state per
    session is queued, written at most once per PER_SESSION_INTERVAL per session and once per
    GLOBAL_INTERVAL overall, skipped entirely when nothing changed, and paused by a circuit
    breaker after repeated failures or any rate-limit response. The local file always holds
    the latest state, so a GitHub outage never breaks the app.
    """

    def __init__(
        self,
        token: str,
        repo: str,
        branch: str = "main",
        *,
        workflow_ref: str = "main",
        api_url: str = "https://api.github.com",
        client: Optional[httpx.Client] = None,
        local: Optional[LocalFileStore] = None,
        clock: Callable[[], float] = time.monotonic,
        per_session_interval: float = PER_SESSION_INTERVAL,
        global_interval: float = GLOBAL_INTERVAL,
        breaker_threshold: int = BREAKER_THRESHOLD,
        breaker_pause: float = BREAKER_PAUSE_SECONDS,
        start_thread: bool = True,
    ):
        self.repo = repo
        self.branch = branch
        self.workflow_ref = workflow_ref  # branch that holds the prune workflow, not the data branch
        self.api_url = api_url.rstrip("/")
        self.local = local or LocalFileStore()
        self._clock = clock
        self._per_session_interval = per_session_interval
        self._global_interval = global_interval
        self._breaker_threshold = breaker_threshold
        self._breaker_pause = breaker_pause

        self._client = client or httpx.Client(timeout=HTTP_TIMEOUT)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "scheduleplanner-session-store",
        }

        self._cv = threading.Condition()
        self._io_lock = threading.Lock()  # one remote write at a time (writer thread vs flush_all)
        self._pending: Dict[str, dict] = {}
        self._last_write: Dict[str, float] = {}
        self._last_global = float("-inf")
        self._sha: Dict[str, str] = {}
        self._synced_hash: Dict[str, str] = {}
        self._pending_hash: Dict[str, str] = {}
        self._inflight: set = set()  # sessions whose write is on the wire right now
        self._failures = 0
        self._paused_until = float("-inf")
        self._size_cache: Optional[tuple] = None
        self._last_dispatch = float("-inf")
        self._stopped = False
        self._thread: Optional[threading.Thread] = None

        if start_thread:
            self._thread = threading.Thread(target=self._run, name="session-writer", daemon=True)
            self._thread.start()
            atexit.register(self.flush_all)

    # ------------------------------------------------------------------ HTTP
    def _url(self, session_id: str) -> str:
        return f"{self.api_url}/repos/{self.repo}/contents/sessions/{session_id}.json"

    def _breaker_open(self, now: float) -> bool:
        return now < self._paused_until

    def _record_failure(self, now: float, retry_after: Optional[float] = None) -> None:
        self._failures += 1
        if retry_after is not None:
            self._paused_until = now + max(self._breaker_pause, retry_after)
        elif self._failures >= self._breaker_threshold:
            self._paused_until = now + self._breaker_pause
        if self._paused_until > now:
            logger.warning("Session store paused for %.0fs after remote failures", self._paused_until - now)

    def _record_success(self) -> None:
        self._failures = 0

    @staticmethod
    def _retry_after(response: httpx.Response) -> Optional[float]:
        """Seconds to wait if this response is a GitHub rate-limit signal, else None."""
        if response.status_code not in (403, 429):
            return None
        header = response.headers.get("retry-after")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        reset = response.headers.get("x-ratelimit-reset")
        if reset and response.headers.get("x-ratelimit-remaining") == "0":
            try:
                return max(0.0, float(reset) - time.time())
            except ValueError:
                pass
        # A 403/429 with no hint is still treated as rate limiting
        return 0.0

    # ------------------------------------------------------------------ read
    def _local_or_unavailable(self, session_id: str) -> dict:
        """Local cache when the remote cannot be read. Never reports a missing session as new."""
        cached = self.local.load(session_id)
        if cached is None:
            raise SessionUnavailable("Session storage could not be reached and no local copy exists")
        return cached

    def load(self, session_id: str) -> Optional[dict]:
        """
        Returns the saved state, or None only when the remote confirms the session does not exist.
        If the remote cannot be read, returns the local cache or raises SessionUnavailable, so a
        returning user is never mistaken for a new one. Invalid saved content raises ValueError.
        """
        now = self._clock()
        if self._breaker_open(now):
            return self._local_or_unavailable(session_id)
        try:
            response = self._client.get(
                self._url(session_id), params={"ref": self.branch}, headers=self._headers
            )
        except httpx.HTTPError as exc:
            logger.warning("Session load failed (%s); using local cache", exc)
            self._record_failure(now)
            return self._local_or_unavailable(session_id)

        if response.status_code == 404:
            self._record_success()
            cached = self.local.load(session_id)
            if cached is None:
                return None
            # Remote never received this session (failed or paused writes): keep the local copy
            # and queue it so the remote catches up.
            self.save(session_id, cached, force=True)
            return cached
        retry_after = self._retry_after(response)
        if retry_after is not None:
            self._record_failure(now, retry_after)
            return self._local_or_unavailable(session_id)
        if response.status_code != 200:
            self._record_failure(now)
            return self._local_or_unavailable(session_id)

        try:
            payload = response.json()
            raw = payload.get("content") or ""
            if payload.get("encoding") == "base64" and raw:
                text = base64.b64decode(raw).decode("utf-8")
            else:
                # Files over 1 MB come back without inline content; fetch the raw body instead
                raw_response = self._client.get(
                    self._url(session_id),
                    params={"ref": self.branch},
                    headers={**self._headers, "Accept": "application/vnd.github.raw+json"},
                )
                raw_response.raise_for_status()
                text = raw_response.text
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Session load incomplete (%s); using local cache", exc)
            self._record_failure(now)
            return self._local_or_unavailable(session_id)

        self._record_success()
        try:
            state = json.loads(text)
        except ValueError as exc:
            raise ValueError(f"Saved session is not valid JSON: {exc}") from exc
        self._sha[session_id] = payload.get("sha", "")
        self._synced_hash[session_id] = state_hash(state)
        try:
            self.local.save(session_id, state)
        except OSError:
            pass
        return state

    # ----------------------------------------------------------------- write
    def save(self, session_id: str, state: dict, force: bool = False) -> None:
        """Writes the local cache now and queues the remote write. Never blocks on the network."""
        digest = state_hash(state)
        with self._cv:
            if not force:
                if self._pending_hash.get(session_id) == digest and session_id in self._pending:
                    return
                if (session_id not in self._pending and session_id not in self._inflight
                        and self._synced_hash.get(session_id) == digest):
                    return
            self._pending[session_id] = state
            self._pending_hash[session_id] = digest
            self._cv.notify()
        try:
            self.local.save(session_id, state)
        except OSError as exc:
            logger.warning("Local session cache write failed: %s", exc)

    def quarantine(self, session_id: str) -> Optional[Path]:
        return self.local.quarantine(session_id)

    def delete(self, session_id: str) -> bool:
        """Removes the session locally and remotely. Returns True when the remote copy is gone."""
        self.local.delete(session_id)
        with self._cv:
            self._pending.pop(session_id, None)
            self._pending_hash.pop(session_id, None)
        now = self._clock()
        if self._breaker_open(now):
            return False
        try:
            with self._io_lock:
                sha = self._sha.get(session_id)
                if not sha:
                    lookup = self._client.get(
                        self._url(session_id), params={"ref": self.branch}, headers=self._headers
                    )
                    if lookup.status_code == 404:
                        return True
                    if lookup.status_code != 200:
                        self._record_failure(now, self._retry_after(lookup))
                        return False
                    sha = lookup.json().get("sha")
                response = self._client.request(
                    "DELETE",
                    self._url(session_id),
                    headers=self._headers,
                    json={"message": f"delete session {session_id}", "sha": sha, "branch": self.branch},
                )
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Session delete failed: %s", exc)
            self._record_failure(now)
            return False
        if response.status_code in (200, 204, 404):
            self._sha.pop(session_id, None)
            self._synced_hash.pop(session_id, None)
            return True
        self._record_failure(now, self._retry_after(response))
        return False

    def _due_time(self, session_id: str) -> float:
        return max(
            self._last_write.get(session_id, float("-inf")) + self._per_session_interval,
            self._last_global + self._global_interval,
            self._paused_until,
        )

    def _next_due(self) -> Optional[float]:
        if not self._pending:
            return None
        return min(self._due_time(sid) for sid in self._pending)

    def flush_due(self, now: Optional[float] = None) -> int:
        """Writes the most overdue pending session if one is due. Returns the number written (0 or 1)."""
        now = self._clock() if now is None else now
        with self._cv:
            due = [(self._due_time(sid), sid) for sid in self._pending]
            due = [d for d in due if d[0] <= now]
            if not due:
                return 0
            _, session_id = min(due)
            state = self._pending.pop(session_id)
            digest = self._pending_hash.pop(session_id, state_hash(state))
            self._inflight.add(session_id)

        try:
            ok = self._write_remote(session_id, state, now)
        finally:
            with self._cv:
                self._inflight.discard(session_id)
        with self._cv:
            if ok:
                self._last_write[session_id] = now
                self._last_global = now
                self._synced_hash[session_id] = digest
            else:
                # Keep the latest state for the next allowed attempt; a newer queued state wins
                if session_id not in self._pending:
                    self._pending[session_id] = state
                    self._pending_hash[session_id] = digest
                self._last_write[session_id] = now
                self._last_global = now
        return 1

    def flush_all(self) -> None:
        """
        Best-effort write of everything pending (used at interpreter exit). Respects the breaker.
        A session stays queued until its write succeeds, so a failure never drops the state.
        """
        with self._cv:
            self._stopped = True
            self._cv.notify_all()
        deadline = self._clock() + 15.0
        while self._clock() < deadline:
            with self._cv:
                pending = list(self._pending.items())
            if not pending or self._breaker_open(self._clock()):
                return
            for session_id, state in pending:
                digest = state_hash(state)
                if not self._write_remote(session_id, state, self._clock()):
                    return
                with self._cv:
                    if self._pending.get(session_id) is state:  # not replaced by a newer state meanwhile
                        self._pending.pop(session_id, None)
                        self._pending_hash.pop(session_id, None)
                    self._synced_hash[session_id] = digest

    def _write_remote(self, session_id: str, state: dict, now: float) -> bool:
        with self._io_lock:
            return self._write_remote_locked(session_id, state, now)

    def _write_remote_locked(self, session_id: str, state: dict, now: float) -> bool:
        if self._breaker_open(now):
            return False
        body = json.dumps(state, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        payload = {
            "message": f"session {session_id}",
            "content": base64.b64encode(body).decode("ascii"),
            "branch": self.branch,
        }
        try:
            sha = self._sha.get(session_id)
            for attempt in range(2):
                if sha:
                    payload["sha"] = sha
                response = self._client.put(self._url(session_id), headers=self._headers, json=payload)
                if response.status_code in (200, 201):
                    self._sha[session_id] = response.json().get("content", {}).get("sha", "")
                    self._record_success()
                    return True
                retry_after = self._retry_after(response)
                if retry_after is not None and response.status_code in (403, 429):
                    self._record_failure(now, retry_after)
                    return False
                if attempt == 0 and response.status_code in (409, 422):
                    # Stale or missing sha: look up the current one and retry once
                    lookup = self._client.get(
                        self._url(session_id), params={"ref": self.branch}, headers=self._headers
                    )
                    sha = lookup.json().get("sha") if lookup.status_code == 200 else None
                    payload.pop("sha", None)
                    continue
                break
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Session write failed: %s", exc)
        self._record_failure(now)
        return False

    def _run(self) -> None:
        """Writer thread: sleeps until the next write is due, never spins."""
        while True:
            with self._cv:
                if self._stopped:
                    return
                due = self._next_due()
                if due is None:
                    self._cv.wait()
                    continue
                delay = due - self._clock()
                if delay > 0:
                    self._cv.wait(timeout=delay)
                    continue
            try:
                self.flush_due()
            except Exception:  # never let the writer thread die
                logger.exception("Unexpected error in session writer")

    # --------------------------------------------------------- repo guards
    def repo_size_mb(self) -> Optional[float]:
        """Data repo size from the repo metadata, cached for SIZE_CACHE_SECONDS."""
        now = self._clock()
        if self._size_cache and now - self._size_cache[1] < SIZE_CACHE_SECONDS:
            return self._size_cache[0]
        size: Optional[float] = None
        if not self._breaker_open(now):
            try:
                response = self._client.get(f"{self.api_url}/repos/{self.repo}", headers=self._headers)
                if response.status_code == 200:
                    size = response.json().get("size", 0) / 1024.0  # API reports KB
                else:
                    retry_after = self._retry_after(response)
                    self._record_failure(now, retry_after)
            except (httpx.HTTPError, ValueError):
                self._record_failure(now)
        self._size_cache = (size, now)
        return size

    def dispatch_prune(self) -> bool:
        """Asks the data repo's workflow to run an emergency prune. At most once per hour."""
        now = self._clock()
        if now - self._last_dispatch < DISPATCH_COOLDOWN_SECONDS or self._breaker_open(now):
            return False
        self._last_dispatch = now
        try:
            response = self._client.post(
                f"{self.api_url}/repos/{self.repo}/actions/workflows/{PRUNE_WORKFLOW_FILE}/dispatches",
                headers=self._headers,
                json={"ref": self.workflow_ref, "inputs": {"mode": "emergency"}},
            )
            return response.status_code == 204
        except httpx.HTTPError as exc:
            logger.warning("Prune dispatch failed: %s", exc)
            return False

    def guard_new_session(self, cap_mb: float = SIZE_CAP_MB) -> str:
        """Called when a new session is created. Returns 'ok', 'over_cap' or 'unknown'."""
        size = self.repo_size_mb()
        if size is None:
            return "unknown"
        if size >= cap_mb:
            self.dispatch_prune()
            return "over_cap"
        return "ok"


def build_store(secrets: Optional[Mapping[str, Any]] = None, local_dir: str = "data/sessions"):
    """GitHubStore when a token and repo are configured, otherwise LocalFileStore."""
    local = LocalFileStore(local_dir)
    config = (secrets or {}).get("session_store") if secrets is not None else None
    if config and config.get("token") and config.get("repo"):
        return GitHubStore(
            token=config["token"],
            repo=config["repo"],
            branch=config.get("branch", "main"),
            workflow_ref=config.get("workflow_ref", "main"),
            local=local,
        )
    return local
