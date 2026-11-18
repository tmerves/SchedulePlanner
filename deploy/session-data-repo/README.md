# Session data repository setup

The app stores each user session as `sessions/<id>.json` in a separate **private** GitHub repo, so sessions survive Streamlit Cloud redeploys, reboots and force pushes.

The repo uses two branches:
- `main` holds the retention workflow and script. The workflow runs from here.
- `sessions` holds only the session files. History on this branch is squashed after deletions, and keeping the workflow file off it avoids GitHub rejecting the push (`GITHUB_TOKEN` may not create or update workflow files).

## Retention rules
- A session is deleted after 3 days without activity.
- When the data repo reaches 200 MB, the 15 least recently active sessions are deleted. The app requests this when a new session is created, and the scheduled job also checks every 6 hours. GitHub's reported size lags a history squash, so after any deletion a marker file (`.last_prune`) blocks further size-driven deletion for 24 hours. Expiry-based deletion is never blocked.
- History on the `sessions` branch is squashed after any deletion so the space is actually freed.
