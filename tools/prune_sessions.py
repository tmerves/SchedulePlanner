"""
Retention job for the session data repository.

Run it inside a checkout of the data repo (the GitHub Action does this every 6 hours):

    python prune_sessions.py --sessions-dir sessions --mode scheduled --size-mb 12

Rules
- A session whose last_active is older than RETENTION_DAYS is deleted.
- In emergency mode, or when the repo is at or over SIZE_CAP_MB, the EMERGENCY_DELETE_COUNT
  sessions with the oldest last_active are also deleted. If the repo is still over the cap,
  the next run removes another batch.
- Files that cannot be parsed or have no last_active are kept and reported, never deleted.

Only the standard library is used so the job needs no installs.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

RETENTION_DAYS = 3
SIZE_CAP_MB = 200.0
EMERGENCY_DELETE_COUNT = 15


def parse_timestamp(value) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def plan_deletions(
    last_active: Dict[str, datetime],
    now: datetime,
    mode: str,
    size_mb: Optional[float],
    retention_days: int = RETENTION_DAYS,
    size_cap_mb: float = SIZE_CAP_MB,
    emergency_count: int = EMERGENCY_DELETE_COUNT,
) -> Tuple[List[str], List[str]]:
    """Returns (expired_ids, emergency_ids). The two lists never overlap."""
    cutoff = now - timedelta(days=retention_days)
    expired = sorted(sid for sid, ts in last_active.items() if ts < cutoff)

    emergency: List[str] = []
    over_cap = size_mb is not None and size_mb >= size_cap_mb
    if mode == "emergency" or over_cap:
        remaining = sorted(
            ((ts, sid) for sid, ts in last_active.items() if sid not in set(expired))
        )
        emergency = [sid for _, sid in remaining[:emergency_count]]
    return expired, emergency


def load_last_active(sessions_dir: Path) -> Tuple[Dict[str, datetime], List[str]]:
    found: Dict[str, datetime] = {}
    skipped: List[str] = []
    for path in sorted(sessions_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            skipped.append(path.stem)
            continue
        ts = parse_timestamp(data.get("last_active")) if isinstance(data, dict) else None
        if ts is None:
            skipped.append(path.stem)
            continue
        found[path.stem] = ts
    return found, skipped


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sessions-dir", default="sessions")
    parser.add_argument("--mode", choices=["scheduled", "emergency"], default="scheduled")
    parser.add_argument("--size-mb", type=float, default=None, help="Current repo size in MB")
    parser.add_argument("--now", default=None, help="ISO timestamp to use instead of the current time (testing)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    sessions_dir = Path(args.sessions_dir)
    now = parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print("Invalid --now value", file=sys.stderr)
        return 2
    if not sessions_dir.is_dir():
        print(f"No sessions directory at {sessions_dir}; nothing to do")
        return 0

    last_active, skipped = load_last_active(sessions_dir)
    expired, emergency = plan_deletions(last_active, now, args.mode, args.size_mb)
    to_delete = expired + emergency

    if not args.dry_run:
        for sid in to_delete:
            (sessions_dir / f"{sid}.json").unlink(missing_ok=True)

    summary = {
        "kept": len(last_active) - len(to_delete),
        "expired_deleted": expired,
        "emergency_deleted": emergency,
        "skipped_unreadable": skipped,
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, indent=2))

    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(f"deleted_count={len(to_delete)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
