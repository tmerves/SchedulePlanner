# Session data repository setup

The app stores each user session as `sessions/<id>.json` in a separate **private** GitHub repo, so sessions survive Streamlit Cloud redeploys, reboots and force pushes.

The repo uses two branches:
- `main` holds the retention workflow and script. The workflow runs from here.
- `sessions` holds only the session files. History on this branch is squashed after deletions, and keeping the workflow file off it avoids GitHub rejecting the push (`GITHUB_TOKEN` may not create or update workflow files).

## Setup
1. Create a private repo, for example `SchedulePlanner-sessions`, with a `main` branch.
2. On `main`, add `prune_sessions.py` (a copy of `tools/prune_sessions.py` from the app repo) at the root, and `.github/workflows/prune.yml` (copy of `deploy/session-data-repo/.github/workflows/prune.yml`).
3. Create the data branch with one placeholder file:
   ```bash
   git checkout --orphan sessions
   git rm -rf .
   mkdir sessions && touch sessions/.gitkeep
   git add sessions/.gitkeep && git commit -m "data branch" && git push origin sessions
   ```
4. Create a fine-grained personal access token limited to the data repo with **Contents: read and write** and **Actions: read and write**.
5. In the Streamlit Cloud app settings, add under Secrets:

```toml
[session_store]
token = "github_pat_..."
repo = "your-user/SchedulePlanner-sessions"
branch = "sessions"
workflow_ref = "main"
```

Without these secrets the app falls back to local disk storage, which is wiped on every redeploy.

## Retention rules
- A session is deleted after 3 days without activity.
- When the data repo reaches 200 MB, the 15 least recently active sessions are deleted. The app requests this when a new session is created, and the scheduled job also checks every 6 hours. GitHub's reported size lags a history squash, so after any deletion a marker file (`.last_prune`) blocks further size-driven deletion for 24 hours. Expiry-based deletion is never blocked.
- History on the `sessions` branch is squashed after any deletion so the space is actually freed.

Change `RETENTION_DAYS`, `SIZE_CAP_MB` or `EMERGENCY_DELETE_COUNT` at the top of `prune_sessions.py`, and `SIZE_CAP_MB` in `core/storage.py` to match.
