# airlock

Dev-sandbox isolation for internal tools. `airlock` clones the current project
into an isolated sandbox under `~/sandboxes/`, opens a guarded subshell there,
and merges the result back into the main working tree only after an explicit
review step. The main repo is never touched by accident.

Requires: Python 3.11+, git, [mise](https://mise.jdx.dev). Python standard
library only — no third-party dependencies.

## Install

```text
git clone https://github.com/vinyardrip/airlock.git
cd airlock
pip install -e .          # add '.[dev]' to also get the test suite (pytest)
```

Updates are a `git pull` in that directory — there is no PyPI release, by
design (see `.ck/PLAN.md`, Phase 5).

## Daily usage

### 1. Start working: enter the sandbox

From the project root:

```text
cd ~/projects/my/myproject
airlock enter
```

The project is cloned into `~/sandboxes/<project>_<id>`, pinned tool versions
are installed (`mise install`), and you land in a subshell marked
`(airlock:myproject)`. Everything you do happens in the clone. The main
working tree is protected: `git checkout`/`switch` are blocked there, pushes
go only to the sandbox's private bare remote, and repo-level git hooks are
neutralized.

### 2. Know your versions (optional)

```text
airlock check
```

For every pinned tool, `airlock` asks mise for the latest version and compares:

- `[ok]` — the pin is current (or behind within policy, shown as `[behind]`);
- `[OUTDATED]` — the pin is more than one release line behind `mise latest`
  (e.g. python `3.12` vs `3.15`). Exit code is `1`, so it is script-friendly;
- `[MISSING]` — the project uses a tool (`package.json` engines,
  `requires-python` in `pyproject.toml`) that is not pinned in the version
  config.

To actually bump a version, do it inside the sandbox: `airlock update python`
pins `mise latest`, reinstalls the tools and runs the project's tests. The
change stays uncommitted for the merge.

### 3. Pause: the work is NOT ready

Just leave:

```text
exit
```

Nothing is merged. The sandbox stays in `~/sandboxes/<project>_<id>` with all
your work — committed and uncommitted alike. Resume later directly:

```text
cd ~/sandboxes/<project>_<id>
```

(A plain `cd` gives you a normal shell: the repo-level guards baked into the
sandbox keep working, while the interactive git wrapper belongs to the
`airlock enter` session.)

### 4. Finish: the work IS ready

**Commit inside the sandbox first.** The merge refuses to run while the
sandbox tree is dirty — merging into a dirty tree is the classic way to
clobber work, so `airlock` checks both repos before doing anything:

```text
git add -A && git commit -m "my change"
exit
```

Then merge from the project root:

```text
cd ~/projects/my/myproject
airlock merge
```

`airlock merge` pushes your sandbox branch to the private bare remote, fetches
it into the main repo, shows what will be merged (a `--stat` summary plus a
separate diff of version configs), and asks:

```text
Proceed with merge? [y/N]:
```

Enter or `n` aborts without touching anything; `y` merges. If
`.tool-versions`/`mise.toml` changed, `mise install` runs in the main project
afterwards (a missing mise is a warning, never a failure).

Finally, clean up:

```text
airlock clean
```

Deleting a sandbox is destructive, so you always confirm by typing `yes` —
with or without `--force`. Before deleting, `airlock` refuses if the tree is
dirty or the branch holds commits that never reached the bare remote (that
work would be lost); `--force` bypasses those checks, never the confirmation.

### Picking the sandbox (smart selection)

You almost never type the full id. Without an argument, `merge`/`clean` look
at the current project: one sandbox is auto-selected
(`[i] Auto-selected sandbox: ...`), several produce a numbered list where
Enter picks the first. A partial id works too: `airlock merge 003629`.

## Command reference

| Command | Purpose |
| --- | --- |
| `airlock enter` | Clone the current project into `~/sandboxes/<project>_<id>`, set up the bare remote, open a guarded subshell. |
| `airlock merge [<id>]` | Push the sandbox branch, show the version-config diff separately, confirm (y/N), merge into the main repo; runs `mise install` if version configs changed. |
| `airlock list` | All sandboxes with project, id, branch and clean/dirty status. |
| `airlock clean [<id>]` | Delete a sandbox; refuses when the tree is dirty or commits are unpushed (`--force` bypasses the checks but still asks to type `yes`). |
| `airlock check` | Compare pinned tool versions with `mise latest`; flags outdated pins and tools used by the project but not pinned. |
| `airlock update <tool>` | Inside a sandbox only: bump a pinned tool to `mise latest`, run `mise install`, run the project's tests. |
| `airlock migrate versions` | Convert a simple `.tool-versions` into `mise.toml` (strict scope; try `--dry-run` first). |

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

## Ежедневная работа

### 1. Начало: вход в песочницу

Из корня проекта:

```text
cd ~/projects/my/myproject
airlock enter
```

Проект клонируется в `~/sandboxes/<проект>_<id>`, устанавливаются закреплённые
версии инструментов (`mise install`), открывается подоболочка с меткой
`(airlock:myproject)`. Всё, что вы делаете, происходит в клоне. Основное
рабочее дерево защищено: `git checkout`/`switch` там заблокированы, push уходит
только в приватный bare-remote песочницы, хуки git в репозитории отключены.

### 2. Версии (по желанию)

```text
airlock check
```

Для каждого закреплённого инструмента — сверка с `mise latest`:

- `[ok]` — версия актуальна (или отстаёт в пределах политики — `[behind]`);
- `[OUTDATED]` — закрепление отстаёт больше чем на одну версию от `mise
  latest` (например, python `3.12` против `3.15`); код возврата `1`;
- `[MISSING]` — проект использует инструмент (`package.json` engines,
  `requires-python`), который не закреплён в конфиге версий.

Обновить версию можно внутри песочницы: `airlock update python` закрепляет
`mise latest`, переустанавливает инструменты и прогоняет тесты проекта.
Изменения остаются незакоммиченными до merge.

### 3. Пауза: работа НЕ готова

Просто выйдите:

```text
exit
```

Ничего не сливается. Песочница остаётся в `~/sandboxes/<проект>_<id>` вместе
со всей работой — и закоммиченной, и нет. Продолжить позже можно прямо там:

```text
cd ~/sandboxes/<проект>_<id>
```

(Обычный `cd` даёт обычную оболочку: предохранители уровня репозитория
продолжают работать, а интерактивная git-обёртка относится к сессии
`airlock enter`.)

### 4. Завершение: работа ГОТОВА

**Сначала коммит внутри песочницы.** Merge отказывается работать с «грязным»
деревом — слияние в грязное дерево это классический способ потерять работу,
поэтому airlock проверяет оба репозитория:

```text
git add -A && git commit -m "моя правка"
exit
```

Затем слейте из корня проекта:

```text
cd ~/projects/my/myproject
airlock merge
```

`airlock merge` отправляет ветку песочницы в приватный bare-remote, подтягивает
её в основной репозиторий, показывает сводку изменений (`--stat`) и отдельный
diff конфигураций версий, затем спрашивает:

```text
Proceed with merge? [y/N]:
```

Enter или `n` — отмена без каких-либо изменений; `y` — слияние. Если менялись
`.tool-versions`/`mise.toml`, после слияния в основном проекте выполнится
`mise install` (отсутствующий mise — предупреждение, а не ошибка).

В конце уберите за собой:

```text
airlock clean
```

Удаление песочницы необратимо, поэтому подтверждение вводом `yes` требуется
всегда — и с `--force`, и без. Перед удалением airlock откажется работать,
если дерево «грязное» или есть коммиты, не дошедшие до bare-remote (эта работа
была бы потеряна); `--force` обходит проверки, но не подтверждение.

### Выбор песочницы

Полный id вводить не нужно. Без аргумента `merge`/`clean` смотрят на текущий
проект: одна песочница выбирается автоматически (`[i] Auto-selected
sandbox: ...`), несколько — нумерованный список, Enter выбирает первую.
Работает и частичный id: `airlock merge 003629`.

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

Phase 1 (core) is complete and piloted; Phase 2 (self-bootstrap) is documented
above; the daily workflow now lives in this README (Task 5.2, workflow part).
Architecture decisions and limitations are tracked in `.ck/PLAN.md`; the
remaining Task 5.2 pieces (security model, limitations) land with the rest of
Phase 5.
