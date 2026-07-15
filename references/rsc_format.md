# layout.rsc format & where information lives

`layout.rsc` (inside a `.vcmx` zip) is the Visual Components model. ~5 MB ASCII-ish text.
It has an **outer** tree structure and, embedded in it, **escaped XML** program blocks.

## Two layers, two newline conventions
- **Outer structure**: `Key value`, `Key "string"`, and `Name\n{ ... \n}` blocks. Line endings **LF** (`\n`).
  Examples: `Node "rSimResource"`, `Functionality "..."`, `Feature "..."`, `ProcessFlowGroups`.
- **`ProgramData "..."`**: one long quoted string holding a process's program as **escaped XML**.
  - Quotes are `\"` (backslash+quote). XML line breaks are literal `\r` / `\n` (2 chars each).
  - The serializer also inserts a **real CR** before each escaped-XML line for wrapping.
  - So the separator between two `<statement>`s = `real-CR + \n(literal) + spaces`. **Derive it**
    (`rsclib.sep10`), never hardcode. `</statements>` sits at 8 spaces, statements at 10.
- Always read/write `encoding='utf-8', newline=''` so neither convention is mangled.
- Non-ASCII paths (Korean folder names) break when typed inline in bash/PowerShell — locate files
  with `glob` wildcards inside a Python script (`rsclib.find_one`). "Path not found" on a path that
  worked before usually means the **folder was renamed** (e.g. a `.vcmx` re-extracted under a new name).

## Node / component structure
- `Node "rSimResource"` = a component (station, feeder, worker controller, ...). Header:
  `Name "..."`, `Id <n>`, then `NodeClass { ... }` (inline geometry) **or** `SharedNodeClass <n>`,
  then a list of **`Functionality "<type>" { Id <n> Name "..." ... }`** behaviors.
- 44 nodes in the sample kitchen. Notable behavior types:
  - `rProcessExecutor` — runs a process program (the `ProgramData`). A node may have >1 (e.g. a Precall executor).
  - `rProductCreator` — a **feeder** (creates products). Feeder processes are named `Feed_*`.
  - `rSimBoolSignal` — a boolean signal behavior (see signals_and_patterns.md). `AutomaticReset 1` = pulse.
  - `rTransportNode`, `rSimInterface`, `rSimContainer`, `rSimStatistics`, `rPythonScript`, `rTransportController`.
- **Adding a signal behavior**: insert a `Functionality "rSimBoolSignal"{...}` block **before the
  last depth-1 Functionality** (`rsclib.op_add_signals`). ⚠️ Do NOT use the file-last `Functionality`:
  some nodes have an `rPhysicsEntity` Functionality at **brace depth 0** (outside the node body, after
  `PropertyHandling`); inserting there puts the signal outside the node → VC "No signal selected".
  New behavior `Id` = (max `Id` in node span) + 1.
- **Id-duplication is normal**: the same `Id` number recurs across sub-scopes (connections, features).
  A "duplicate behavior Id" check will false-alarm identically on the original file — ignore it; only
  ensure your NEW signal Ids exceed the node's max.

## Process programs (ProgramData)
- Each `rProcessExecutor` holds a `ProgramData` = `<programs>...<process name="X">...<statements>...`.
- `rsclib.blocks(raw)` → `{process_name: (abs_start_of_inner, inner)}`.
- **process → owning node**: the nearest **preceding** `Node "rSimResource"` (`rsclib.process_owner_map`).
- Statements execute **in written order** (critical — see signals doc). Leaf statement types
  (`IWorkStatement`, `ITransport*Statement`, `I*SignalStatement`, prints, delays) contain no nested
  `<statement>`; container types (`IWhileStatement`, `ISwitchCaseStatement`) do — use `rsclib.leaf()`
  to match a single leaf, and the flat `STMT` regex only for counting.
- Common statements & meaning:
  - `ITransportInStatement` (sync arrival), `IStartTransportInStatement`+`IWaitTransportStatement`
    (async arrival: start, then wait), `ITransportOutStatement` (departure).
  - `IWorkStatement` — has a `Controller` property = the worker doing the work
    (`Worker Ctrl #1::TC` / `#2::TC` in the current model; older files say `Human Transport Controller`).
  - `IProcessDelayStatement` — a timed wait (cooking) that does NOT occupy a worker.
  - `IChangeProductTypeStatement` (`NewType` GUID), `IAttachProductStatement`.
  - `IProcessPrintStatement` — a log line; `Message` property is an expression string, often
    `"[Proc] <Step> <착수|완료> | 담당: <worker> | ..."`. "담당" = who performs the step.
  - `IWaitSignalStatement` / `ISendSignalStatement` — see signals doc. Properties via `rsclib.prop`.
- Read a property: `rsclib.prop(stmt, "Controller")`. Set: `rsclib.set_prop`. A **dangling** signal
  ref shows as `<property name="Signal" isvisible=...>` with **no `value=`** (its signal was deleted)
  → `rsclib.has_empty_signal` / `dangling_signal_stmts`. These raise VC "No signal selected".

## Workers
- Two worker resources. Names vary by model: **`Worker Ctrl #1`**/**`Worker Ctrl #2`**, or in the robot
  models **`Worker Ctrl #1`** (=schedule Worker 1) + **`Robot Ctrl`** (=Worker 2, a robot arm). Older models:
  `Human Transport Controller[ #2]`. `BOMname`/`BOMdescription` metadata is cosmetic — leave it.
- A **Work** step's worker = its `IWorkStatement` `Controller` (deterministic; set per schedule).
- A **Transport** step's worker = the matching **`TransportLink`'s `Implementer`** (an outer-layer block:
  `TransportLink { Id "…" Implementer "…::TC" Source "<physNode>::TransportNode" Destination "<physNode>" SupportedGroup "…" }`).
  ⚠️ Source/Destination are **PHYSICAL node names** (e.g. `Kitchen Sink #2`), not the process names in the
  schedule — build a process→node map to find the link for a transport `A→B`. (Some models also have a
  dynamic `ShortestTravelReply` fallback, but the Implementer is the assignment.)
- ⚠️ **`Robot Ctrl` is transport-only**: giving it a **Work** task crashes its TaskLogic at runtime
  (`_write_task_action` → `KeyError: 'NoneType'`). Keep Work tasks on the human controller. See signals ref.
- `Reserve/ReleaseResource` sends (Component = a worker) reserve a worker around a step.
- **`IsEnabled=False` on any statement = disabled/commented-out (inert).** Always audit `IsEnabled` on the
  statements you rely on after an edit — a needed Wait/Send silently toggled off breaks the chain. See signals ref.

## Product types
- Product type **names are not stored plainly**. GUIDs appear in `ProcessFlowGroups` (per menu) and in
  each `ITransportInStatement`'s product filter (`<value>GUID</value>`) and `IChangeProductTypeStatement`
  `NewType`. Identify which TransportIn carries which product by matching its accepted GUID to a flow
  group (`rsclib.product_flow_groups`) — e.g. Cook_Katsudon's first (Base) TransportIn accepts a
  `Katsudon_Pan` GUID = the stuffed pan.

## ProcessFlowGroups
- A block near the top: `ProcessFlowGroup { Name "..." ProductTypes { ProductType "GUID" ... } }`.
  Defines menu groupings by product GUID only (no ordering). Cosmetic for the sim; used to identify products.

## Validation after ANY edit (always run)
- `rsclib.validate_xml(raw)` — every process's escaped XML must parse (minidom). Must be `[]`.
- `rsclib.braces_balanced(raw)` — outer `{`==`}` count.
- `rsclib.dangling_signal_stmts(raw)` — should not increase.
- `rsclib.unresolved_signal_refs(raw, only_prefix=...)` — every Wait/Send must reference a signal that
  exists at depth 1 on the named node. Non-empty = future "No signal selected".
- Structural sanity: node / `rProcessExecutor` / `rProductCreator` counts unchanged unless intended.
- Work on a COPY / test file; diff sizes; only overwrite `layout.rsc` after checks pass. Then repackage
  the `.vcmx` (SKILL.md rules: back up old → `.bk<idx>`, zip contents at root).
