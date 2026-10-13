# Migrations

A migration changes an existing brain folder when a release moves or reshapes something that
belongs to the person (a renamed file, a new field in `.kit/config.json`, a split note). Kit-owned
files never need one: updates replace them by manifest.

## Writing one

- File name: `NNNN_short_name.py` (four digits, then letters, digits or `_`), for example
  `0002_rename_people_file.py`. Numbers only go up. Never renumber, edit or delete a migration
  that has shipped.
- It defines `migrate(workspace: Path) -> str`, gets the brain folder as a `Path`, and returns one
  line saying what it did ("renamed people.md to team.md", or "nothing to do").
- Standard library only, Python 3.9, no network, no prompts.
- Idempotent: running it twice must be harmless. `brain rollback` keeps the user's newer files, so
  a migration can meet its own output on the next update.
- Never delete the user's content. Rename or copy instead, and leave the old file when unsure.
- Raise an exception to stop: the update is rolled back from the snapshot taken just before.

## How they run

- `brain update` runs the new release's `brain _apply`, which runs pending migrations in number
  order after the kit files are in place, and records each one in
  `.kit/state/migrations.json`.
- A fresh install already has the newest layout, so every shipped migration is recorded as done
  without running. Re-running the installer over an existing brain runs pending ones like an
  update.
- `tools/build_release.py` refuses a release whose migration names don't match the pattern or
  that lack a top-level `migrate`.
