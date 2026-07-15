# Signals: semantics, placement, and edit recipes

## ⚠️ CRITICAL runtime semantic — confirmed with a domain user (2026-07)
`IWaitSignalStatement` is **EDGE-triggered** and only starts listening **when execution reaches it**.
This section corrects earlier guesses in this file (there is no "level trigger", and same-process
send→wait is NOT safe).

- **`Timeout` MUST be `0`** for a wait to actually block. A nonzero `Timeout` (the old templates used
  `600`, one cloud build used `5`) makes the wait **give up and fall through** after that many seconds —
  so it enforces nothing. Config for EVERY wait: `WaitTrigger=True`, `Condition=""` (blank), `Timeout=0`.
- **`Condition` is meaningless.** A wait completes **only when a `SendSignal` fires while the wait is
  already parked**. A `Condition` already satisfied when the wait is reached does NOT release it; and a
  Send fired *before* the wait was reached is **missed** → the wait hangs forever. (`Condition="Signal ==
  True"` does NOT make it level-triggered.)
- Therefore **park the Wait EARLY** — before any blocking statement (`ITransportInStatement`,
  `IStartTransportInStatement`, `IProcessDelayStatement`, `IWorkStatement`, `IWaitTransportStatement`) —
  so the process is already parked when the Send comes. A structurally valid file (XML ok, refs resolve)
  still **deadlocks** if a Wait sits after a blocking statement and its sender fires during that block.
- **`SEND(g)` immediately followed by `WAIT(g)` in the SAME process deadlocks** (the wait misses the pulse
  it just sent). So for consecutive tasks that should run continuously, put **no signal between them**;
  only signal a **cross-process handoff**. (Corrects the old "same-process send→wait is fine".)
- **Transport is driven by the `TransportOut` (sender) side.** A `TransportIn` can begin once the sender's
  `TransportOut` turn arrives. Send after a `TransportOut`; that Send can release a Wait placed before the
  receiver's `TransportIn`.

## Statement forms (escaped-XML properties)
- `IWaitSignalStatement`: `Component`(node hosting the signal) + `Signal`(name) + `WaitTrigger=True` +
  `Condition=""` + **`Timeout=0`** (+ `TimedOutVariableName`, `ValueOutputVariableName` blank). `rsclib.make_wait`.
- `ISendSignalStatement`: `Component` + `Signal` + `Value`(`True`/`False`, or `&quot;&quot;` for a resource).
  `rsclib.make_send`.
- Signal referenced **by name**: the `Component` node must host an `rSimBoolSignal` with that `Name` at
  depth 1. Extract wait/send templates FROM THE FILE (copy an existing enabled ord_/dep_ statement, swap the
  `Signal` value) so escaping + config stay exact.

## Worker-order chain (current design — supersedes the old `step<TaskID>` per-task baton)
Per worker, take that worker's own **sync** tasks in schedule `Start` order (ignore other workers &
Delays). For each consecutive pair t_i → t_{i+1} on that worker, one signal `ord_NN` (hosted on
`Worker Ctrl #1`, level config above):
- **WAIT before t_{i+1}'s START action**: before its `Work`, or before the sender's `TransportOut` (the pick).
- **SEND after t_i's END action**:
  - Work → after its `IWorkStatement`.
  - **Sync** transport → after the **destination's `ITransportInStatement`** (the place — worker frees after placing).
  - **Async** transport → after the **destination's `IWaitTransportStatement`** (`wX`, when the product
    actually arrives) — NOT after the sender `TransportOut`.
- **Consecutive SAME-node pair → NO signal.** If completion-process(t_i) == start-process(t_{i+1}) the two
  run sequentially in one process; a signal there self-loops (send then wait, same process) → deadlock.
  Detect + drop: send-owner-process == wait-owner-process.
- **Convergence matching**: a dest with N sync senders ↔ its N `TransportIn`s → match sender→IN by **arrival
  order (Start)** (k-th-earliest sync sender = k-th `TransportIn`; the `Base`/`Part`/`Part3` log vars confirm
  order). Same for async: k-th async sender = k-th `IWaitTransportStatement`. Switch-case nodes with ONE
  shared `TransportIn` for many menus (e.g. `Serve`) can't be matched 1:1 → leave those sends after the
  sender `TransportOut`.
- To FORCE a worker to stay across consecutive same-node tasks: wrap the block `ReserveResource … work …
  ReleaseResource → SendSignal(next node) → WaitSignal(own next turn)`.

## Worker assignment (who executes each task)
- **Work** task worker = the `IWorkStatement`'s `Controller` (`Worker Ctrl #1::TC` / `Robot Ctrl::TC`).
- **Transport** task worker = the matching `TransportLink`'s `Implementer` (OUTER layer, plain quotes):
  `TransportLink { Id "…" Implementer "…::TC" Source "<physNode>::TransportNode" Destination "<physNode>::TransportNode" SupportedGroup "…" }`.
  **Source/Destination are PHYSICAL node names** (e.g. `Kitchen Sink #2` == process `Wash_Noodle`), not the
  process/station names in the schedule. Build a process→node map (each `<process name=..>`'s nearest
  preceding `Node "rSimResource"` `Name`) to find the right link for a transport `A→B`.
- ⚠️ **`Robot Ctrl` is a transport-only resource — do NOT give it Work tasks.** Its
  `TaskLogic._write_task_action` implements only `Transport` tasks; a non-transport (**Work**) task hits a
  generic branch `_VC_PROP_TYPE_MAP[type(task_prop).__name__]` that raises **`KeyError: 'NoneType'`** when a
  task property is `None` (e.g. work→transport handoff where the next-link is unset). Simple assembly works
  (Set/Stuff/Wash/Combine) happened to survive; **fryer/process works crash**. Keep Work on the human
  `Worker Ctrl #1`, or confirm each robot-Work actually runs.

## Always AUDIT `IsEnabled` after edits
`IsEnabled=False` = the statement is **commented out / inert**. After any chain edit, verify `IsEnabled`
on everything you depend on — new `ord_` send/waits, the `dep_` handshakes you "kept", transport/work
anchors. When you copy a statement as a template, confirm the SOURCE was enabled (else every clone is dead).
The VC editor lets a user silently toggle a needed wait off (seen: a `dep_` WAIT disabled while its SEND
stayed on → broken handshake). Value lives in `name=\"IsEnabled\" value=\"True|False\"`.

## `dep_` handshakes (product / async-cook deps — schedule-INDEPENDENT)
Beyond worker order, a small set of product handshakes gate async cook/fry completion: a producer sends,
a consumer WAITs (parked early) before its work — e.g. `Feed_Pot→Pour_Soup`, `Fry_Shrimp→Set_Shrimp_Fry`,
`Cook_Albab→Serve_Albab`. These are structural to the recipe, NOT the worker schedule, so they survive a
schedule swap unchanged — keep them. (Older models named them `step<N>`; a clean rebuild renames `dep_NN`.)

## Escaping trap (Windows + Python)
Inside ProgramData, quotes are `\"` (backslash+quote) and Korean is UTF-8 (prints as mojibake in the
Windows console but the Python string is intact — match/replace works). A regex char-class that INCLUDES a
backslash (`[^"\\]`, `[^\\]`) blows up as "unterminated character set". Two safe tactics:
(a) **sentinel**: `S='\x01'; u = raw.replace('\\"', S)`, run clean `[^\x01]`-style regexes over `u`,
edit `u`, then `u.replace(S, '\\"')` back (positions self-consistent within `u`); or
(b) plain `str.find`/slicing with the literal 2-char `\"`. For 담당 log edits match on **ASCII tokens**
(`[Node] Work `, `Worker Ctrl #1::TC`), never the Korean `담당`.

## Op-editing pattern (safe multi-edit)
Collect edits as ops `(start, end, text)` against the whole raw, compute EVERY position from the ORIGINAL
raw, then apply end-first (`rsclib.apply_ops`, or `sorted(ops, key=lambda x:(-x[0],-x[1]))`). Never
edit-then-recompute-positions.

## Recipe: implement a schedule into an existing model
1. Load `schedule_result_*.csv` (`utf-8-sig`); sync tasks only; per-worker lanes sorted by `Start`.
2. Strip old `ord_` chain: delete all `ord_` wait/send statements + their `rSimBoolSignal` behaviors.
3. Rebuild the worker-order chain (see above): skip same-node pairs; wait-before-start, send-after-end
   (sync→dest IN, async→dest wX), convergence by arrival order.
4. Set every **Work** `Controller` and every changed **Transport** `Implementer` to the schedule worker.
5. Update each changed task's **log 담당** (work logs from the Controller; transport OUT log in the sender
   process, IN log in the receiver process — scope by `[Node] <Action>` + old-worker token, replace
   old→new; convergence handled by old-token scoping).
6. Validate: `validate_xml==[]`, `braces_balanced`, `dangling_signal_stmts==0`,
   `unresolved_signal_refs(only_prefix="ord_"/"dep_")==[]`, **no self-loops** (send-owner==wait-owner),
   every signal exactly 1 send + 1 wait. Then repackage (SKILL rules).
- **Multi-candidate**: copy one working `.vcmx` into each candidate folder, re-run steps 1–6 with that
  folder's schedule. Same recipe → only worker order/assignment changes (geometry too if node positions
  actually differ — a schedule optimized for candidate X's layout applied to candidate Y's geometry can put
  the robot on an unreachable/incompatible task).

## Recipe: fix logging (담당) to match current workers
Logs hardcode 담당; after reassignment they go stale. Recompute:
- Work log → the process's `IWorkStatement` `Controller`.
- Sync transport → the transport task's worker: sender-worker for a `TransportOut` log, receiver-worker for
  a `TransportIn` log. Async `TransportIn(Async)` logs ↔ `IStartTransportInStatement`/`IWaitTransportStatement`.
- At convergence nodes the arrival order (baton / product `Base`/`Part`), NOT schedule `Start` order, decides
  which log line is which worker — matching by Start order silently swaps 담당 at convergence (a real bug we hit).
- Rewrite only the `담당: <old>` worker token (ASCII) inside each `Message`; leave `null` ChangeType and
  component `BOMname`s. Re-validate XML/braces.

## Recipe: strip a signal system (recover a clean base)
Remove `Functionality "rSimBoolSignal"{...Name "ord_/step..."...}` blocks + every Wait/Send referencing them
(plus one adjacent separator, so exactly one separator remains between the true neighbors). Verify 0 leftover
refs, XML ok, braces balanced, process count unchanged. Keep `dep_` handshakes, `Reserve/ReleaseResource`,
`ShortestTravelReply`, `Precall_*`, robot `Trigger`/`Grasp` signals.

## Schedule CSVs (DAG optimizer output)
Columns: `Task ID, Node Name, Description ("A TransportOut -> B TransportIn[(Async)]" or "X [Work N]"),
SegmentType (Transport|Work|Delay), Sync, Start(s), End(s), Resource Lane (Worker 1|Worker 2|<async lane>)`.
Read `utf-8-sig`. Sync tasks consume a worker; Delay (Async) does not. Per-worker order = tasks with that
`Resource Lane`, sorted by `Start(s)`. `Worker 2` may be a robot (`Robot Ctrl`) — see the Work-task warning above.
