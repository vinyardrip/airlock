# Roadmap

Everything here is backlog — not scheduled, not promised. Completed work and
the reasoning behind it live in [ARCHITECTURE.md](ARCHITECTURE.md) and
`.ck/PLAN.md`.

## Backlog

- **`mise.toml` full support** — version ranges (`python = ">=3.11,<4"`),
  `[env]` and `[tasks]` sections. Today they are read but ignored: `check`,
  `update` and `migrate` handle only simple pinned versions.
- **Other tool managers** — native asdf, nvm, pyenv support, if `airlock`
  ever needs to run on machines without mise.
- **Single-binary distribution** — PyInstaller build so a machine needs
  neither a Python setup nor a repo checkout; updates would become "replace
  the binary" instead of `git pull`.
- **`airlock resume`** — re-enter an existing sandbox with the full guarded
  environment. Today pausing and resuming via a plain `cd` works, but the
  interactive git wrapper is not on `PATH` outside the `airlock enter`
  session.
- **Sandbox metadata in `airlock list`** — last-used timestamps and a marker
  for "branch already merged" to make `clean` decisions easier.
- **Deferred validation: `vite-bridge`** — the pnpm/node cycle was skipped for
  time; the tool has proven itself on Python, Bash and PHP stacks, and the
  merge/clean paths are stack-agnostic. Revisit before declaring node stacks
  fully supported.
- **Public release** (PyPI, `curl | bash`) — explicitly out of scope for now;
  `airlock` is an internal tool installed from a private repo.

## Non-goals

- **Publishing from sandboxes** — by design; only the reviewed main tree
  reaches GitHub (see ARCHITECTURE.md).
- **Untrusted repositories** — the threat model is accidental damage, not
  adversarial code; do not point `airlock enter` at repos you do not trust.
- **CI usage** — `airlock` is an interactive developer tool, not a CI matrix
  runner.
