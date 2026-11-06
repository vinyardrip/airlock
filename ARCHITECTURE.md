# Architecture

One-page map of why `airlock` is built the way it is. For daily usage see
[README.md](README.md); the full decision history lives in `.ck/PLAN.md`.

## Isolation model: clone + bare remote (not git worktree)

- `airlock enter` runs `git clone --local` of the project into
  `~/sandboxes/<project>_<id>` and creates a bare "push target" remote at
  `~/.local/share/airlock/remotes/<project>.git` (one per project, reused).
- Inside the sandbox: `origin` = the bare remote; `upstream` = the project's
  real GitHub remote with its push URL rewritten to `no_push://<project>` —
  so the sandbox physically cannot push to GitHub even if every wrapper guard
  were bypassed. Defense in depth: repo config first, git wrapper second.
- Why not `git worktree`: a worktree shares the object store and refs with the
  main repo, so branch state leaks across the boundary and "main repo
  untouched" cannot be guaranteed. A full clone gives the sandbox independent
  history that diverges at clone time and returns through one explicit,
  reviewed merge step.

## Process boundary: guarded subshell, not a daemon

`airlock enter` is a plain subshell (the user's `$SHELL`) launched in the
sandbox with:

- `AIRLOCK_ACTIVE=1` and `AIRLOCK_MAIN=<project root>` — the reliable markers
  (custom prompt themes may override `PS1`, they cannot override the env);
- `airlock/bin` prepended to `PATH`, so `git` resolves to the wrapper;
- a best-effort `PS1` marker (`(airlock:<project>)`).

There is no daemon and no lock file. "Pausing" is simply leaving the subshell —
the sandbox directory keeps all work, committed or not.

## The git wrapper (`airlock/bin/git` + `airlock/git_wrapper.py`)

While `AIRLOCK_ACTIVE=1`, before the real git runs, the wrapper:

- scrubs every `GIT_*` environment variable (blocks `GIT_SSH_COMMAND`-style
  injection and any env-driven git redirection);
- forces `-c core.hooksPath=/dev/null -c diff.external=` and drops user `-c`
  overrides of exactly those keys — repo-level hooks and external diff
  drivers never execute;
- blocks `git push` to anything but `origin`, and only while origin's URL
  points inside `~/.local/share/airlock/remotes/` (re-pointing origin is
  blocked separately);
- blocks `git checkout`/`git switch` outside `~/sandboxes/` — i.e. in the main
  project directory while a sandbox session is active;
- blocks `git config --global`/`--system` and creating, re-pointing or
  renaming the `upstream` remote;
- logs every block with timestamp, cwd and reason to
  `~/sandboxes/.airlock_git_guard.log`.

Outside an active sandbox the wrapper is a pure pass-through: it cannot break
a normal git workflow even if it ends up on the global `PATH`.

## Merge: explicit and reviewed

`airlock merge` refuses dirty trees on both sides (sandbox and main project),
pushes the sandbox branch to the bare remote, fetches it into the main repo,
shows a `--stat` summary plus a separate diff restricted to version configs
(`.tool-versions`, `mise.toml`, `pnpm-lock.yaml`, `package.json`,
`pyproject.toml`), prompts y/N (Enter aborts), then merges `FETCH_HEAD`. If
runtime configs (`.tool-versions`, `mise.toml`) changed, `mise install` runs —
best effort: a missing or failing mise is a warning, never a failed merge.

Publishing is deliberately not part of the merge: sandboxes cannot push to
GitHub, so the reviewed main tree is the only place a `git push` can originate.

## Deletion guard

`airlock clean` compares the sandbox branch tip against the bare remote
(`git ls-remote origin`) and refuses to delete when the tree is dirty or
commits never reached the bare remote — that work would be lost. `--force`
bypasses those checks; the typed `yes` confirmation is unconditional, because
`rmtree` of a whole project directory is destructive regardless of state.

## Version adapter

`.tool-versions` and `mise.toml` are read with mise precedence: the nearest
file wins per tool, and `mise.toml` beats `.tool-versions` in the same
directory (a warning names the ignored file). "Outdated" is compared per
*release line* — the first two numeric components treated as one index
(python `3.12` vs `3.15` = 3 lines behind), because a strict semver-major
comparison would never flag a python pin until 4.0 exists. The threshold is
configurable in `~/.config/airlock/policy.toml` (`outdated_majors`, default 1).

## Constraints

- **Standard library only.** Python 3.11+, no third-party runtime
  dependencies. This keeps the security surface small and auditable — the
  entire guard logic is a few hundred lines of stdlib Python.
- **Trusted local repositories only.** The threat model is accidental damage,
  not adversarial code. The wrapper neutralizes the accidental channels
  (hooks, external diffs, env injection, stray pushes); a repository that
  actively fights the sandbox is out of scope.
- **No shell execution.** All external commands run via
  `subprocess.run([...], shell=False)`; paths are `pathlib.Path` throughout;
  no eval/exec/pickle.
