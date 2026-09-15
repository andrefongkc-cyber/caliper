# ADR 0002: Command bus with automatic deltas, and a snapshot document

- **Status:** Accepted
- **Date:** 2026-09-14
- **Contract:** `caliper/contracts/commands.py`, `caliper/contracts/document.py`, `caliper/contracts/errors.py`
- **Related:** ADR 0005 (file format)

## Context

Every change to a project must go through one path, whether it comes from the UI, the AI
layer, or a script (invariants 2–5). That path has to support undo/redo, headless replay,
AI access with no privileged back door, and eventually collaboration.

The original plan made one command log serve as the undo history, the file format, and the
AI transcript (event sourcing). Kickoff review rejected that; see Alternatives.

## Decision

**Commands are plain data.** Each V1 mutation is a frozen, keyword-only standard-library
dataclass in `commands.py`. Constructing one never validates, so an invalid command is
representable and can be rejected with structured errors. All V1 command types are defined
up front, so freezing the contract doesn't force a joint PR per command.

**The bus returns results, never raises on bad input.** `CommandBus.execute` returns
`Applied(command, delta, label, created_ids)` or `Rejected(command, errors)`. Each `Error`
carries a stable `ErrorCode` (e.g. `value.not_positive`), a human message, and the offending
field. The AI loop branches on codes, so codes may be added but never renamed or
repurposed. An exception means a bug in Caliper or API misuse.

**Undo is automatic.** The bus compares entity values before and after each command and
records a `Delta`. Commands never implement an inverse; undo applies `delta.inverted()`.
Deltas hold entity definitions only, never derived geometry.

**The undo stack is session state.** It is never saved. It is bounded by entry count and by
approximate size (the serialized size of its deltas). Each entry has a display label
("Undo Create Rectangle"). A new command clears the redo stack. Consecutive executes with
the same `merge_key` merge into one entry, so dragging a value is one undo step.

**Transactions group commands.**
- `transaction(label)` commits as one undo entry holding the *net* delta: the first before-value
  and the last after-value per entity. An entity created and deleted inside it leaves no trace.
  A 5,000-step optimizer run costs one entry, sized by entities touched rather than steps taken.
- `transaction(label, undoable=False)` records nothing and clears the undo and redo stacks on
  commit. Keeping them would be wrong: their before-values no longer match the document, so
  undoing would corrupt it.
- A `Rejected` command inside a transaction does not roll back by itself, so an agent can retry.
  `rollback()` or an escaping exception reverts everything inside. Nested transactions merge
  into the outermost.

**IDs are opaque strings.** When a create command has no `id`, the bus allocates `e{n}` from
`Document.next_id`. The counter is document state and appears in deltas, so undo and replay
allocate identical ids. Callers may choose their own ids matching `ID_PATTERN`. The resolved
command in `Applied` always carries the concrete id.

**Subscribers are told what changed.** After every change, listeners receive
`Change(reason, delta, label)` with reason execute, undo, redo, or rollback. The shell
repaints from the delta.

**Three records, kept physically separate:**
1. The document: an immutable snapshot, saved as the project file (ADR 0005).
2. The undo stack: session-scoped and never saved.
3. The AI/session transcript: a sidecar that references commands and entity ids. It never
   enters the project file. Its format is decided with the AI layer.

**UI state is not document state.** Selection, hover, and drawing or drag previews are never
commands and never appear in the document. A drag issues one command on release.

**Queries** are reached through `bus.queries`, bound to the current snapshot (see
`queries.py`).

## Consequences

- Undo correctness comes from one generic mechanism rather than one inverse per command.
- The same `Delta` feeds undo, repaint, dirty tracking, and eventually collaboration.
- The AI layer can do exactly what the UI can, through the same path.
- Each command copies the entity mapping. That is O(entities) and negligible below roughly
  10,000 entities. A persistent map can replace it later without a contract change, because
  the contract only promises a `Mapping`.
- Deltas copy whole entity values. That's fine because entities are small parameter
  records. It would not be fine for B-rep payloads, which is why derived geometry is banned
  from deltas.
- After the Phase 0.5 freeze, a new command type needs a joint contract PR.

## Alternatives considered

- **Command log as the file (event sourcing).** Rejected. Opening a file would get slower
  as history grows. Every command type ever shipped would have to replay forever, and
  migrations would apply to history instead of shape. Optimizer runs would pollute
  permanent history. AI prompts would end up in files sent to suppliers. It also confuses
  the undo log with the V2 parametric feature tree, which is document state: a declarative,
  reorderable, suppressible definition of the part.
- **Hand-written inverses per command.** Rejected. A common, recurring source of undo bugs.
- **Whole-document snapshot per undo step.** Rejected. Memory grows with entities × steps.
- **Pydantic models.** Rejected for now. Free JSON Schema generation is attractive for AI tool
  definitions. But the contract and file format must not depend on a third-party serializer's
  float formatting, ordering, or major-version changes. A small schema generator over these
  dataclasses can cover the AI need when it arrives.
- **Selection as document state.** Rejected. It isn't undoable in mainstream CAD and would
  pollute files and deltas.
