# airlock

Dev-sandbox isolation for internal tools. `airlock` clones the current project
into an isolated sandbox under `~/sandboxes/`, opens a guarded subshell there,
and merges the result back into the main working tree only after an explicit
review step. The main repo is never touched by accident.

Requires: Python 3.11+, git, [mise](https://mise.jdx.dev). Python standard
library only — no third-party dependencies.

## Commands

| Command | Purpose |
| --- | --- |
| `airlock enter` | Clone the current project into `~/sandboxes/<project>_<id>`, set up the bare remote, open a guarded subshell. |
| `airlock merge [<id>]` | Push the sandbox branch, show the version-config diff separately, confirm (y/N), merge into the main repo; runs `mise install` if version configs changed. |
| `airlock list` | All sandboxes with project, id, branch and clean/dirty status. |
| `airlock clean [<id>]` | Delete a sandbox; refuses when the tree is dirty or commits are unpushed (`--force` bypasses the checks but still asks to type `yes`). |
| `airlock check` | Compare pinned tool versions with `mise latest`; flags outdated pins and tools used by the project but not pinned. |
| `airlock update <tool>` | Inside a sandbox only: bump a pinned tool to `mise latest`, run `mise install`, run the project's tests. |
| `airlock migrate versions` | Convert a simple `.tool-versions` into `mise.toml` (strict scope; try `--dry-run` first). |

If the sandbox argument is omitted or partial (e.g. `airlock merge 003629`),
it is resolved against the current project: a single match is auto-selected,
several matches offer a numbered list.

## Bootstrap: airlock sandboxes itself

There is no special "self" mode — and none is needed. Project detection is a
plain walk up to the nearest `.git` (`airlock/project.py`), so the `airlock`
repository is just another project. Running `airlock enter` from the airlock
repo root creates an `airlock_<id>` sandbox with exactly the same mechanics
and guardrails as for any other project:

```text
cd ~/projects/my/airlock
airlock enter                  # sandbox airlock_<id>, branch sandbox/<id>
pytest tests/ -v               # the whole suite runs inside the sandbox
exit
airlock merge                  # review the diff, confirm y/N — or discard: airlock clean
```

This is the proof that the tool is generic and not hardcoded to any particular
project: `airlock` dogfoods its own isolation. Until the explicit merge, the
main working tree stays untouched.

Note: the sandbox receives `mise install` on entry, so the pinned Python (and
any other pinned tools) is ready to use. Gitignored files (`.venv` and friends)
are not cloned — run tests through the same mise shims as in the main repo.

## Самобутстрап: airlock тестирует сам себя в песочнице

Специального режима «для себя» нет — он и не нужен. Определение проекта — это
простой поиск `.git` вверх по дереву каталогов (`airlock/project.py`), поэтому
репозиторий `airlock` — просто ещё один проект. Команда `airlock enter` из
корня репозитория создаёт песочницу `airlock_<id>` с той же механикой и теми
же предохранителями, что и для любого другого проекта:

```text
cd ~/projects/my/airlock
airlock enter                  # песочница airlock_<id>, ветка sandbox/<id>
pytest tests/ -v               # весь набор тестов выполняется внутри песочницы
exit
airlock merge                  # просмотр diff, подтверждение y/N — или airlock clean
```

Это доказательство универсальности инструмента: `airlock` проверяет свою
собственную изоляцию на себе. До явного `merge` основное рабочее дерево не
меняется.

Примечание: при входе в песочницу выполняется `mise install`, поэтому
закреплённый Python (и остальные инструменты) готовы к работе. Файлы из
`.gitignore` (например, `.venv`) не клонируются — тесты запускаются через те
же mise-шимы, что и в основном репозитории.

## Status

Phase 1 (core) is complete and piloted on a real project; Phase 2 (bootstrap)
verifies the cycle above on the airlock repo itself. Architecture, decisions
and limitations are tracked in `.ck/PLAN.md`; the full EN+RU documentation
(architecture, security model, limitations) lands in Phase 5.
