# Airlock: Dev-Sandbox Implementation Plan

## Current Sprint

## Phase 1: airlock core + pilot on context-keeper

**Objective:** Build `airlock` as a standalone Python tool (separate private repo) and validate the full dev-sandbox cycle on `context-keeper`. `ck-dev` and `.sandbox/` are NOT touched — they serve a different purpose (test isolation for `ck` itself).

**Architectural decisions (fixed, see Phase 5 for rationale):**
- Isolation: `git clone --local` + bare-remote at `~/.local/share/airlock/remotes/<project>.git`. NOT `git worktree`.
- Sandboxes live in `~/sandboxes/<project>_<id>/` (not hidden).
- Control: `airlock enter/exit/list/clean`, subshell with `PS1` marker.
- Push to GitHub from sandbox is forbidden (upstream = `no_push` URL + git wrapper).
- `git checkout`/`switch` in the main directory is blocked while a sandbox is active (git wrapper in subshell `PATH`).
- Tests run inside the sandbox.
- Version format: `.tool-versions` (simple, one version per tool). `mise.toml` read-support from day one; conversion is opt-in.
- Language: Python (managed via `uv` through `mise`).
- Repo: separate, private, `gh repo create airlock --private`.
- No publication (no PyPI, no curl|bash). Installed as a local script (`~/.local/bin/airlock` → symlink to repo). Update via `git pull`.

**Guardrails:**
- `airlock clean` must refuse if the sandbox has uncommitted changes or unpushed commits in the bare-remote.
- Merge into the main directory must show a separate diff for version configs (`.tool-versions`, `mise.toml`, `pnpm-lock.yaml`, `package.json`, `pyproject.toml`, `requirements*.txt`).
- `git push upstream` from sandbox must be impossible even if the wrapper fails (upstream URL = `no_push://`).
- `airlock enter` must determine the effective version config by mise rules: nearest file to project root wins; on the same level `mise.toml` > `.tool-versions`. If both exist in the same directory — warn, work with `mise.toml`.

- [x] Task 1.1: Repo skeleton and CLI entrypoint
  - `gh repo create airlock --private`.
  - Python package layout (`airlock/` + `tests/` + `pyproject.toml`).
  - `uv tool install --editable` or `~/.local/bin/airlock` symlink.
  - `airlock --version`, `airlock --help`.

- [x] Task 1.2: Project root detection
  - `airlock enter` without arguments: walk up to `.git`, take `basename` of the directory containing it as the project name.
  - Fallback markers if no `.git`: `mise.toml` / `.tool-versions` / `package.json` / `pyproject.toml` / `.ck/`.
  - If nothing found: clean error, suggest `airlock enter <name>`.

- [ ] Task 1.3: Sandbox creation (`airlock enter`)
  - Create `~/sandboxes/<project>_<id>/` (id = short timestamp or short hash).
  - `git clone --local` from the main directory.
  - Configure `origin` = bare-remote at `~/.local/share/airlock/remotes/<project>.git` (create if missing).
  - Configure `upstream` = GitHub URL from the main directory's `origin`, but with `no_push://` as push URL.
  - Run `mise install` in the sandbox (versions from the effective config).
  - Launch subshell: `cd` into sandbox, `PS1` marker (`(airlock:ck) $`), `PATH` prepended with wrapper `git`.

- [ ] Task 1.4: Git wrapper (guardrails)
  - Wrapper script in `~/.local/share/airlock/bin/git` (prepended to `PATH` in subshell).
  - Block `git push` to `upstream` (any remote whose push URL is not the bare-remote).
  - Block `git checkout`/`switch` in the main directory (detect: `pwd` outside sandbox and sandbox is active).
  - Pass through everything else to the real `git`.

- [ ] Task 1.5: Exit and merge (`airlock exit`)
  - `exit` from subshell → return to main directory.
  - `airlock exit --merge` (or `airlock merge <project>_<id>`): show diff of version configs separately, then `git fetch <bare> && git merge <branch>` in the main directory.
  - On merge: run `mise install` / `pnpm install` / `uv sync` in the main directory as needed.

- [ ] Task 1.6: `airlock list` and `airlock clean`
  - `list`: active sandboxes under `~/sandboxes/`, with project name, id, branch, `git status --porcelain` summary.
  - `clean <project>_<id>`: refuse if uncommitted changes or unpushed commits; `--force` to override (with confirmation).

- [ ] Task 1.7: Version adapter (read-only, both formats)
  - Module `airlock/versioning/` with a common interface: `read_versions(project_root) -> dict`.
  - `.tool-versions` parser (simple `tool version` lines).
  - `mise.toml` reader via `tomllib` (stdlib).
  - Effective config resolution: nearest file wins, `mise.toml` > `.tool-versions` on the same level, warn on both-in-same-dir.

- [ ] Task 1.8: `airlock check`
  - For each tool in the effective config: compare to current LTS/stable (`mise latest <tool>`).
  - Flag outdated versions (policy: configurable in `~/.config/airlock/policy.toml`, default: warn if > 1 major behind LTS).
  - Coverage check: tools used by the project (from `package.json` engines, `pyproject.toml` requires-python, `Cargo.toml`) but absent from the effective config → warn.
  - Suggest `airlock migrate versions` if `.tool-versions` exists and `mise.toml` does not.

- [ ] Task 1.9: `airlock update <tool>`
  - Inside sandbox only (refuse in main directory).
  - Update `.tool-versions` to current LTS/stable for that tool.
  - Run `mise install` in the sandbox.
  - Run the project's test command (detect: `package.json` scripts.test, `pyproject.toml` pytest, `Makefile test`, `tests/run.sh`).
  - Report pass/fail; leave changes in sandbox for merge.

- [ ] Task 1.10: `airlock migrate versions [--dry-run]`
  - Narrow scope: simple `.tool-versions` (`tool version` per line) → `mise.toml` (`tool = "version"`).
  - Refuse on: `path:`, `ref:`, fallback lists, existing `mise.toml` with sections other than `[tools]`.
  - `--dry-run`: show what would be written.
  - Confirmation prompt; non-interactive without `--force` → refuse.
  - README must state explicitly: what it does, what it does NOT do, and that unsupported cases must be migrated by hand.

- [ ] Task 1.11: Pilot validation on context-keeper
  - Full cycle: `airlock enter` in `context-keeper` → make a small feature → `airlock update <tool>` (dry-run at least) → run `ck` tests inside sandbox → `airlock exit --merge` → verify main directory is untouched except merged changes.
  - Verify guardrails: `git push upstream` fails, `git checkout` in main directory fails while sandbox active, `airlock clean` refuses with uncommitted changes.
  - Verify version config diff is shown separately on merge.

**Done when:** full cycle works on `context-keeper`; all guardrails verified; no changes leaked to the main directory except via explicit merge; `airlock` itself is installable and runnable.

## Phase 2: Bootstrap (airlock tests itself)

**Objective:** Prove `airlock` is stack-agnostic by using it on its own repo.

- [ ] Task 2.1: `airlock enter --self`
  - From inside the `airlock` repo, create a sandbox of `airlock` itself.
  - Same clone+bare mechanics, no special-casing.

- [ ] Task 2.2: Run `airlock` tests inside its own sandbox
  - `pytest` (or the project's test runner) inside the sandbox.
  - Verify that tests pass and that the sandbox is isolated (main repo untouched).

- [ ] Task 2.3: Document the bootstrap pattern
  - README section: "airlock tests itself via `airlock enter --self`".
  - This is the proof that the tool is generic, not hardcoded to `ck`.

**Done when:** `airlock enter --self` works, tests pass inside the sandbox, main repo untouched.

## Phase 3: SSoT Consolidation

**Objective:** Eliminate the split-brain between `/home/erip/projects/my/context-keeper` (SSoT) and `/home/erip/projects/sandbox/bash/context-keeper` (experimental). This phase is about directories and files — it does not touch `airlock` architecture.

**Guardrails:**
- Clean Git working tree in the SSoT repo before any transfer.
- Keep a compressed backup of the experimental repo until Phase 3 is validated.
- No changes to `ck` code, `.sandbox/`, or `ck-dev` in this phase.

- [ ] Task 3.1: Audit and backup
  - Diff the experimental repo against the SSoT repo (exclude `.git/`, `.sandbox/`).
  - Create a tarball backup of the experimental repo (`ck_sandbox_backup_$(date +%s).tar.gz`) in `~/.local/share/airlock/backups/` (not `/tmp`).
  - Run `git status --porcelain` in SSoT; must be empty before proceeding.

- [ ] Task 3.2: Merge core logic and scripts
  - Migrate any experimental bash scripts, configs, or CLI improvements from the sandbox into the SSoT repo.
  - Run `shellcheck` on migrated scripts.
  - Ensure English documentation and comment standards are maintained.

- [ ] Task 3.3: Migrate existing test cases
  - Identify valuable testing assets in the experimental repo's `.sandbox/`.
  - Do NOT move them into SSoT `.sandbox/` (that directory is for `ck` test isolation, not a storage).
  - If assets are reusable — move to `tests/fixtures/`; otherwise archive with the backup.

- [ ] Task 3.4: Validation and archival
  - Run the baseline `ck` test suite (`pytest`) in SSoT; must pass.
  - `git bundle create` the experimental repo history into `~/.local/share/airlock/backups/`.
  - Remove the experimental repo directory only after Phase 3 is validated and the bundle is confirmed readable.

**Done when:** SSoT passes `pytest`, experimental repo is archived (tarball + bundle), no split-brain remains, `git status` in SSoT is clean.

## Phase 4: Scale to dpx / vite-bridge / vite-njk-trace / njk-frontmatter

**Objective:** Apply the validated architecture to the remaining internal tools, one at a time.

**Guardrails:**
- Sequential rollout: fully validate one project before starting the next.
- Never touch client/customer directories (`cpukomplekt`, `steptec`, `evakuatori`) — allowlist only the four named tools.

- [ ] Task 4.1: Audit internal toolchain
  - Map current repo locations and test setups for `dpx`, `vite-bridge`, `vite-njk-trace`, `njk-frontmatter`.
  - Identify any split-brain repos among them (same as Phase 3 for `ck`).

- [ ] Task 4.2: Migrate `dpx` (bash/CLI)
  - `airlock enter` → tests → `airlock exit --merge`.
  - Validate bash stack runner.

- [ ] Task 4.3: Migrate `vite-bridge`
  - `airlock enter` → tests → `airlock exit --merge`.
  - Validate node stack runner (pnpm).

- [ ] Task 4.4: Migrate `vite-njk-trace`
  - Same cycle.

- [ ] Task 4.5: Migrate `njk-frontmatter`
  - Same cycle.

- [ ] Task 4.6: Stack-agnostic runner check
  - Confirm that `airlock update` and test detection work for bash, node, python, mixed.
  - Document any per-project overrides needed (e.g., custom test command in `.airlock.toml`).

**Done when:** all four tools run through `airlock` end-to-end; no client directories touched; test detection works for all stacks in use.

## Phase 5: Finalization

**Objective:** Write down architectural decisions so they are not re-litigated in six months.

- [ ] Task 5.1: `airlock.md` (EN + RU)
  - Architecture: clone+bare (and why not worktree — history isolation, not push blocking).
  - Sandbox layout: `~/sandboxes/<project>_<id>/`, bare-remotes in `~/.local/share/airlock/remotes/`.
  - Control: `enter/exit/list/clean`, subshell with `PS1` marker.
  - Guardrails: no_push upstream, git wrapper, clean-guard, merge diff for version configs.
  - Version adapter: reads `.tool-versions` and `mise.toml`; effective config by mise rules; warn on both-in-same-dir.
  - `migrate versions`: narrow scope, what it does NOT do, hand-migration required for unsupported cases.
  - `check`/`update`: outdated-version detection, coverage check.
  - Relationship with `ck`: `airlock` is standalone, `ck-dev`/`.sandbox/` untouched (different purpose).

- [ ] Task 5.2: README (EN + RU)
  - Install (local script, `git pull` to update).
  - Quick start: `airlock enter` in a project.
  - Common workflows: feature dev, version update, merge.
  - Limitations (explicit): no publication, no CI matrix, supports only `.tool-versions` + `mise.toml` (read), no other tool managers.

- [ ] Task 5.3: Backlog for future (not now)
  - `mise.toml` full support (ranges, `[env]`, `[tasks]`) — if needed.
  - Support for other tool managers (asdf without mise, nvm, pyenv) — if the tool ever goes public.
  - Public release (PyPI, curl|bash) — explicitly out of scope for now.

**Done when:** `airlock.md` and README are written, EN+RU, and a new reader can understand the architecture and limitations without re-reading this plan.

## Completed
