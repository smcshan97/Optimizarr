---
name: optimizarr-ship
description: Commit, push or open a PR for Optimizarr — use for the house commit style and the Windows SSH push setup this repo needs.
---

# Ship Optimizarr changes

## Before committing

- Run the suite: `"/c/Python314/python.exe" -m pytest -q tests`. Commit only on green, and quote the pass count in the wrap-up.
- Stage files by name. Leave `.claude/launch.json`, `.claude/settings.local.json`, `.claude/worktrees/`, `data/`, logs and the `*.crt`/`*.key` files out of commits.
- Work goes on a feature branch (e.g. `improvements/audit-round-N`); PRs target `main`.

## Commit message

Match `git log`: a Conventional-Commits subject, scoped when it helps — `feat(encode): …`, `fix(queue): …`, `chore(upscale): …` — in plain words about the user-visible effect. The body explains *why* and quotes measured numbers where a decision rests on them (see `library-stats`), wrapped at ~72 columns. Bigger features list changes as `- path: what changed` bullets. End with the attribution line the session's system reminder gives.

## Push

The user's SSH key is passphrase-protected and lives in the **Windows** ssh-agent service, which Git Bash's ssh cannot reach. This repo already has `core.sshCommand = C:/Windows/System32/OpenSSH/ssh.exe`; confirm with `git config core.sshCommand` if a push fails.

On `Permission denied (publickey)`: the agent likely needs re-priming. Give the user this to run in PowerShell, which asks for their passphrase:

```powershell
Start-Service ssh-agent; ssh-add
```

Then retry the push. Push and open PRs only when the user asks.
