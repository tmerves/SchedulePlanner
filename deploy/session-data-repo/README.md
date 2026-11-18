# Session data repository setup

The app stores each user session as `sessions/<id>.json` in a separate **private** GitHub repo, so sessions survive Streamlit Cloud redeploys, reboots and force pushes.

1. Create a private repo, for example `SchedulePlanner-sessions`, with a `main` branch containing at least one commit.
2. Copy `tools/prune_sessions.py` from the app repo to the root of the data repo as `prune_sessions.py`.
3. Copy `deploy/session-data-repo/.github/workflows/prune.yml` to `.github/workflows/prune.yml` in the data repo.
4. Create a fine-grained personal access token limited to the data repo with **Contents: read and write** and **Actions: read and write**.
5. In the Streamlit Cloud app settings, add under Secrets:

```toml
[session_store]
token = "github_pat_..."
repo = "your-user/SchedulePlanner-sessions"
branch = "main"
```

Without these secrets the app falls back to local disk storage, which is wiped on every redeploy.

## Retention rules
- A session is deleted after 3 days without activity.
- When the data repo reaches 200 MB, the 15 least recently active sessions are deleted. The app requests this when a new session is created, and the scheduled job also checks every 6 hours.
- History is squashed after any deletion so the space is actually freed.

Change `RETENTION_DAYS`, `SIZE_CAP_MB` or `EMERGENCY_DELETE_COUNT` at the top of `prune_sessions.py`, and `SIZE_CAP_MB` in `core/storage.py` to match.
