# ADR 0010: Checks are stored in the document

- **Status:** Proposed (Option A of #16, chosen by Andre on 2026-09-29; Accepted once both
  streams review the `contracts/checks-authors-labels` PR)
- **Date:** 2026-09-29
- **Contract:** `caliper/contracts/document.py`, `commands.py`, `queries.py`
- **Related:** ADR 0002 (commands and deltas), ADR 0005 (file format), ADR 0009 (constraints
  in the document), issue #16, known issue C-1

## Context

A check is a numeric requirement on the sketch ("the plate is 120 ± 0.01 wide"): an
`Expectation`, evaluated headlessly by `Queries.check`. Until now checks lived only in the
shell's session, so Save and reopen, New, or Open lost them (C-1). The assistant's checks
went with a proposal and were added to the session on Accept, and the assistant could only
read the user's checks, never change them. Issue #16 set out three places checks could live
and recommended A: in the document, changed only by commands.

## Decision

**A check is an entity.** `Expectation` moves to the document contract (it stays importable
from `queries`), gets kind `check`, and joins `Entity`. It is stored in
`Document.entities` under an `EntityId` like everything else. #16 sketched a separate
`Document.expectations` with `AddExpectation` and `RemoveExpectation`; storing checks as
entities gets the same guarantees with less new contract:

- One new command, `CreateCheck`. `ModifyEntity` edits a check and `DeleteEntities`
  removes one, so the delta, undo and redo, transactions, merge keys, change notifications,
  History, replay, and the file's entity table all work unchanged.
- Ids share the entity id space (#16, question 2). The Checks panel, History, and an agent
  name a check the way they name anything else.
- Order is creation order: the panel lists checks by id, e2 before e10 (#16, question 3).

**A check must be measurable when it's made.** `CreateCheck`, and `ModifyEntity` on a
check, are rejected with the error `Queries.check` gives when the check can't be evaluated
now: the wrong number of refs or ids, one that doesn't exist, a check of a check, an area of
an open profile. A check that *fails* is stored: a requirement the sketch doesn't meet yet
is still a requirement. Nothing is solved, and a check never moves geometry.

**Deleting what a check measures leaves the check** (#16, question 1: survive and fail).
Nothing refers to a check, and `DeleteEntities` doesn't cascade to it. It stays, and
`Queries.check` reports it failed with `entity.not_found` until it's edited or deleted. A
requirement disappearing because its geometry was deleted is the one outcome a checks
feature must not have. Loading a file doesn't validate a check's references, so a file
with such a check opens.

**The AI uses the same door** (invariant 5). The assistant's `run_check` measures a check
and stores it with `CreateCheck`, or corrects the sketch's check of the same measurement
with `ModifyEntity`; `remove_check` deletes one with `DeleteEntities`. There is no
`create_check` tool: one way to check, and a check that can't be measured isn't stored. A
check is a change, so it goes on the proposal and the user accepts it with the rest; a
check run with nothing else pending is proposed on its own.

**File schema 3.** A new kind is a schema change (ADR 0005). Migration 2 → 3 changes
nothing, since a schema-2 file has no checks; the version moves so an older Caliper says
"update Caliper" instead of failing on an unknown kind.

## Consequences

- Checks travel with the part, like tolerances on a drawing, and are in any file sent to a
  supplier. `export` keeps them: they are inputs, not history.
- Adding or removing a check is an undo step with an author in History. The assistant's
  checks are part of the step that accepting its proposal makes.
- Everything that walks `Document.entities` sees checks. The engine's geometry and solver
  code already filters by type; the shell keeps checks out of the canvas, the browser (the
  Checks panel lists them), and Select All.
- A check takes an entity id, so ids made after it are one higher than they'd have been
  without it. Scripts that predict ids rather than read them from results shift.
- A proposal's checks are worked out by comparing its base and result: the checks it adds
  or edits are the agent's, and a user's check it deletes is named on the card.

## Alternatives considered

- **`Document.expectations` with Add/Remove commands** (#16's sketch of A). Two new commands
  plus a new document field, delta support, and file section, for the same result; and no
  edit command without a third.
- **A section in the file outside `Document`** (#16, B). Not undoable, invisible to History,
  and the agent can't add one.
- **A sidecar file** (#16, C). Separated from the part by the first copy of one file.
- **Cascading deletes to checks.** Consistent with dimensions, but a requirement would vanish
  silently with the geometry it constrains.
