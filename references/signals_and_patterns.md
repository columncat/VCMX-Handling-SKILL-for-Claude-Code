# Signals: semantics, placement, and edit recipes

## ⚠️ CRITICAL runtime semantic (get this wrong → deadlock)
Statements in a process run **strictly in written order**. `IWaitSignalStatement` only begins waiting
**when execution reaches it** — it does not "listen" earlier.

`rSimBoolSignal` with `AutomaticReset 1` behaves like a **pulse**: an `ISendSignalStatement` fired while
the waiter has **not yet reached** its `IWaitSignalStatement` is **missed**. The waiter then reaches its
Wait and blocks forever (the pulse is gone). Therefore:

- **Place the WaitSignal early** — before the process's first real action (before `ITransportInStatement`,
  before `CreateProduct`), so the process is already waiting when the signal comes.
- **Fire the SendSignal only when the waiter is already waiting.** For a "transport done" hand-off,
  the safe place is the **receiver's side, right after its `ITransportInStatement`** (the transport truly
  completed and the downstream waiter is parked at its early Wait).
- Same-process `SEND(x)` immediately followed by `WAIT(x)` is fine — the Wait reads it on the very next step.

This is the #1 correctness rule. A structurally valid file (XML ok, refs resolve) can still **deadlock**
at runtime if a Wait sits after a long `IProcessDelayStatement`/`ITransportIn` and its sender fires during
that delay. When a run "hangs," find which node's Wait was reached but never released; move that Wait
earlier or move its Send to fire when the waiter is parked.

## Statement forms (escaped-XML properties)
- `IWaitSignalStatement`: `Component`(node hosting the signal) + `Signal`(name) + `WaitTrigger=True`
  (+ `Condition`, `Timeout=600`, `ValueOutputVariableName` — blank them). Build via `rsclib.make_wait`.
- `ISendSignalStatement`: `Component` + `Signal` + `Value`(`True`/`False`, or `&quot;&quot;` for resource).
  Build via `rsclib.make_send`.
- Signals referenced **by name**: the `Component` node must host an `rSimBoolSignal` with that `Name`
  at depth 1. Templates are extracted from the file itself so escaping stays exact.

## Baton chain (enforce a worker's task order)
Goal: make each worker perform its scheduled tasks in order; predecessor task triggers successor.
- Signal per task: `step<TaskID>`, hosted on that task's **worker controller** node
  (`Worker Ctrl #1`/`#2`) via `rsclib.op_add_signals`. Task k waits `step<pred>`, then (later) sends
  `step<self>` for its successor. First task of a worker has no wait; last has no send.
- **Anchor of a task**: Transport task "A→B" → sender **A**'s single `ITransportOutStatement`
  (avoids the multi-`TransportIn` matching problem at convergence/shared nodes). Work task "X [Work N]"
  → X's N-th `IWorkStatement`.
- **Placement (apply the CRITICAL semantic)**:
  - `WaitSignal(step<pred>)` → the anchor process's **first statement** (before IN/CREATE), so it parks early.
  - `SendSignal(step<self>)` for a **transport** → just **before** the sender's `TransportOut` (or, safer,
    on the **receiver** right after its `TransportIn`). For a **work** → right after the `IWork`.
- Convergence nodes (multiple TransportIns: Cook_*, Combine_*, Serve_Tonkatsu) and the shared `Serve`
  node: gate on the unique **sender** side, never on their shared/multiple TransportIns.

## Existing trigger mechanism (may coexist)
Some models use `Precall_*` / `AlbabSet` signals as pre-fetch triggers, plus `Reserve/ReleaseResource`
and `ShortestTravelReply`. Preserve unless replacing. When a model was partly stripped, leftover
Wait/Send with an empty `Signal` (no `value=`) are **dangling** → delete them (`rsclib.has_empty_signal`).

## Op-editing pattern (safe multi-edit)
Collect all edits as ops `(start, end, text)` against the **whole raw**, then `rsclib.apply_ops` (it
sorts end-first and does replaces before same-position inserts). Compute every op's absolute position
from the ORIGINAL raw; apply once. Never edit-then-recompute-positions.

```python
import sys; sys.path.insert(0, r"C:\Users\<u>\.claude\skills\vcmx-handling\scripts"); import rsclib as R
raw = R.load(LAY); SEP = R.sep10(raw); wt = R.wait_template(raw); st = R.send_template(raw)
bl = R.blocks(raw); marks = R.node_marks(raw); ops = []
base, inner = bl["Fry_Tonkatsu"]
op, ce = R.find_stmt(inner, "IWorkStatement", 1)
ops.append(R.op_insert_before(base, inner, op, [R.make_wait(wt, "Worker Ctrl #1", "step16")], SEP))
ops.append(R.op_insert_after (base, inner, ce, [R.make_send(st, "Worker Ctrl #1", "step36", "True")], SEP))
# add the hosted signals on the controller node:
i = next(k for k,(p,n) in enumerate(marks) if n == "Worker Ctrl #1")
ops.append(R.op_add_signals(raw, marks, i, ["step16","step36"]))
out = R.apply_ops(raw, ops)
assert R.validate_xml(out) == [] and R.braces_balanced(out)
assert R.unresolved_signal_refs(out, only_prefix="step") == []
R.save(TEST, out)
```

## Recipe: fix logging (담당) to match current workers
Logs hardcode 담당; after worker rename/reassignment they go stale. Recompute each log's 담당:
- Work log → the process's `IWorkStatement` `Controller` (via `rsclib.work_controllers`).
- Transport log → the schedule transport task's worker: sender-task worker for a `TransportOut` log,
  receiver-task worker for a `TransportIn`/`TransportIn(Async)` log (match the N-th such log to the
  N-th receiving task ordered by schedule `Start`). Async logs ↔ `IStartTransportInStatement`.
- Rewrite only the `담당: <old>` token inside each `Message`; leave `null` (ChangeType) and
  component `BOMname`s. Then re-validate XML/braces. (Sim logic statements are untouched.)

## Recipe: strip a signal system (recover a clean base)
Remove `Functionality "rSimBoolSignal"{...Name "step..."...}` blocks and every Wait/Send referencing
those signals **plus one adjacent separator** (`SEP + statement`), so exactly one separator remains
between the true neighbors. Verify: 0 leftover refs, XML ok, braces balanced, process count unchanged.

## Recipe: reassign a Work's worker
`set_prop` the `IWorkStatement` `Controller` to the target `Worker Ctrl #N::TC`; if that process has
`Reserve/ReleaseResource` sends bound to the old worker, `set_prop` their `Component` too.

## Schedule CSVs (DAG optimizer output)
Columns incl `Task ID, Description ("A TransportOut -> B TransportIn[(Async)]" or "X [Work N]"),
SegmentType (Transport|Work|Delay), Allowed Worker, Start(s), Resource Lane (Worker 1|2|resource)`.
Read `utf-8-sig`. Sync tasks (Transport/Work) consume a worker; Delay (Async) does not. Per-worker order
= tasks with that `Resource Lane`, sorted by `Start(s)`.
