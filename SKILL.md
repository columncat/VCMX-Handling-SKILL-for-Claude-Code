---
name: vcmx-handling
description: >
  Handle Visual Components .vcmx model files and their layout.rsc: extract, edit signals/processes/
  logging, validate, repackage, and version-backup. Use whenever a task touches a .vcmx, a layout.rsc,
  or an unzipped VC work folder (geo-* + layout.rsc) — editing process programs, wait/send signals,
  worker assignment, baton/trigger chains, log 담당, or a DAG schedule applied to the model. Provides a
  parsing/editing library (scripts/rsclib.py) and format+semantics references.
---

# VCMX handling

A `.vcmx` is a **ZIP** of a Visual Components model; `layout.rsc` (at the zip root) is the model.
This skill lets you parse and safely edit `layout.rsc` and repackage the `.vcmx`.

**On first use in a task, read the two references** (they hold the hard-won details):
- `references/rsc_format.md` — the file format, two-layer/two-newline structure, node & process anatomy,
  where every piece of info lives (owner node, worker, product GUIDs), and the validation checklist.
- `references/signals_and_patterns.md` — the **critical runtime signal semantic**, wait/send placement,
  baton/trigger chains, and edit recipes (logging fix, strip signals, reassign worker, schedule CSVs).

Use the library instead of re-deriving regexes:
`import sys; sys.path.insert(0, r"<this-skill>\scripts"); import rsclib as R`

## The one thing that bites: signal timing
Statements run in written order; a `Wait` only listens when reached, and `AutomaticReset 1` signals are
**pulses** — a `Send` fired before the waiter reaches its `Wait` is **lost** → deadlock. So put `Wait`
**early** (before `TransportIn`/`CreateProduct`) and fire the "done" `Send` **when the waiter is already
parked** (typically the receiver side, right after its `TransportIn`). Full explanation in the signals ref.

## Always validate after editing (library calls)
`R.validate_xml(out)==[]` · `R.braces_balanced(out)` · `R.dangling_signal_stmts` didn't rise ·
`R.unresolved_signal_refs(out, only_prefix="step")==[]` (catches "No signal selected") · node/exec counts
unchanged unless intended. Edit a **test copy**, verify, then overwrite `layout.rsc`.

## Path & encoding (this environment)
Non-ASCII (Korean) paths break inline in bash/PowerShell — in Python scripts discover paths with `glob`
(`R.find_one(r"C:\Users\*\Desktop\*\*\VCMX\<folder>\layout.rsc")`), never hardcode Korean. A "not found"
on a path that worked before usually means the **folder was renamed** (re-glob). Read/write `.rsc` with
`encoding='utf-8', newline=''`; read schedule CSVs with `utf-8-sig`.

---

# .vcmx packaging rules

`.vcmx` entries sit at the **root** of the zip (`layout.rsc`, `geo-*`, `model.xml`, `*.dat`,
`producttype*`, textures, icons) — no folder wrapper. Extracting `X.vcmx` (rename→`.zip`, unzip) yields
folder `X/` with those files. VC opens the `.vcmx` directly. Library: `R.extract_vcmx`, `R.zip_vcmx`,
`R.folder_stale`, `R.backup_then_path`.

## Rule 1 — Freshness check BEFORE editing the work folder
The user may update the `.vcmx` directly (without extracting) and then ask for work. Before editing,
confirm the folder is not stale: if the `.vcmx` mtime is **newer** than the newest file in the folder
(`R.folder_stale(vcmx, folder)` → True), **re-extract the `.vcmx` over the folder first**. If no folder
exists, extract to create it. Otherwise edit the folder as-is.

## Rule 2 — Version-backup BEFORE writing a new `.vcmx`
Before creating/overwriting `<name>.vcmx`, if it exists, rename the current one (extension only) to
`<name>.bk<idx>`, `idx = max(existing .bk*)+1` (start 1). `R.zip_vcmx`/`R.backup_then_path` do this.
Backups `*.bk*` are archives — leave them; use one only to roll back.

## Rule 3 — Repackaging convention
Zip the folder's **contents at the zip root** (not the folder itself); exclude `*bk*` and other `*.vcmx`;
assert `layout.rsc` is at the zip root; name it `<foldername>.vcmx`, placed as a **sibling of the folder**
(one level up), unless told otherwise. Keep `_HANDOFF*`/notes **outside** the folder (the folder is
re-extracted; anything inside is lost on the user's next extract).

## Per-task checklist
1. Locate `.vcmx` + work folder (glob). If `.vcmx` newer than folder → re-extract first (Rule 1).
2. Read the references if this is the first edit of the task.
3. Edit `layout.rsc` on a test copy with `rsclib`; run the full validation set.
4. Overwrite `layout.rsc`; repackage `.vcmx` (Rule 2 backup, Rule 3 zip).
5. Update the HANDOFF/notes in the **parent** dir.
