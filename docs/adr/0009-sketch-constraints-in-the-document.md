# ADR 0009: Sketch constraints and dimensions in the document

- **Status:** Accepted
- **Date:** 2026-09-22
- **Contract:** `caliper/contracts/document.py`, `commands.py`, `queries.py`, `errors.py`
- **Related:** ADR 0002 (commands and deltas), ADR 0005 (file format; this settles its V1.5
  choice), ADR 0008 (the solver), issue #18 (the shell's proposal)

## Context

V1.5 adds geometric constraints and driving dimensions. They have to fit the rules already
in place: every change is a command, undo is an automatic delta, files store inputs, the
AI uses exactly what the UI uses. Issue #18 proposed a contract from the shell's side and
left eight decisions open.

## Decision

**Constraints are one generic entity.** `Constraint(type, refs)` with a `ConstraintType`
of 14 kinds, stored in `Document.entities` like anything else, so selection, deletion,
undo, and files work unchanged. Not one dataclass per kind (issue #18, decision 1): what a
type accepts and means lives in the engine's registry, so adding a type is one table entry,
and the shell lists types from `applicable_constraints` instead of from the `Command` union.

**References name points or curves.** `Ref(entity, feature)` gains curve features: a line,
circle, or arc's `CURVE`, and a rectangle's four sides (issue #18, decision 2: in).

**Dimensions keep their V1 types.** `DistanceDimension` and `RadialDimension` stay (the
shell draws and edits them), `AngleDimension` joins them, and all three get
`value: float | None`: None is driven, a number is driving (issue #18, decision 4). The
seven kinds a user sees (length, distance, horizontal, vertical, radius, diameter, angle)
are a classification, `Queries.dimension_type`, not seven entity types. `CreateDimension`
infers the kind from the selection and where the label is placed; `infer_dimension` previews
it. The value is a plain number now; an expression field can be added beside it later
without changing what a driving dimension means.

**The solve runs inside each command.** A command that changes geometry, a constraint, or
a driving value solves the clusters it touched before the bus diffs. The geometry the
constraints moved is part of the same delta, so undo, redo, merge keys, transactions, and
change notifications need nothing new.

**Solved positions are stored** (ADR 0005's open V1.5 choice; issue #18, decision 7). The
file holds where geometry ended up. Nothing is re-solved on load: opening is O(entities),
tools without a solver can read files, and a file edited by hand opens as it is, reported
as `CONFLICTING` by `solve_status` until the next command solves it. Replay is
byte-identical everywhere for lines and circles, and within the last bits across platforms
once arcs or angles are solved (ADR 0008).

**Commands never leave a document inconsistent** (issue #18, decision 5, stricter):

- A change the constraints can't allow is `Rejected` with `constraint.conflict`, and
  `Error.ids` names the smallest set of constraints in the way (and the edited geometry, if
  the edit itself is the problem).
- A new constraint the others already imply is `Rejected` with `constraint.redundant`,
  naming them, rather than stored as over-constrained.
- A type that doesn't fit the selection is `constraint.not_applicable`; Pierce, which needs
  3D geometry from outside the sketch, is `constraint.unsupported`.

**What moves is predictable.** A new constraint moves its last reference (the second
selected, the point in a midpoint, the mirror in a symmetry); a dimension change moves its
`b` side; an edit keeps the rest of the edited entity and moves what's connected to it.
Only if that can't work does more of the sketch move.

**Suggestions are not constraints.** `suggest_constraints` reports relationships the
geometry nearly has. They become constraints only through `CreateConstraint`; rejecting or
switching them off is session state (`caliper.engine.constraints.suggest.Suggestions`),
never in the document.

**Construction geometry** is geometry with `construction=True`: solved, constrained, and
dimensioned like the rest, and refused as a profile.

**File schema 2** adds these fields; migration 1 → 2 marks V1 geometry as not construction
and V1 dimensions as driven.

## Consequences

- An agent can do everything a sketcher does through commands and queries: ask what applies
  to a selection, add it, read DOF and conflicts, change a dimension, and see exactly which
  constraints block a change.
- The shell's palette walks the `Command` union and drops commands with fields it can't
  render; the new `construction` flag hides the create commands there until it skips
  defaulted fields. Other shell code keys on `entity.kind` (icons, browser groups) and needs
  entries for `point`, `constraint`, and `angle_dimension`.
- Dragging (issue #18, decision 6) isn't decided here. `MoveEntities` and `ModifyEntity`
  already re-solve; a `DragFeature` command can come with the shell's drag work.
- Over-constrained documents can still exist (hand-edited files); `solve_status` reports
  them, and deleting or changing the named constraint fixes them.

## Alternatives considered

- **One dataclass per constraint kind** (issue #18). Fourteen entity types and fourteen
  create commands, each a contract change, and applicability spread across them.
- **A new unified Dimension entity replacing the V1 two.** Cleaner on paper, but it breaks
  every dimension the shell draws and every V1 file, for no capability the classification
  doesn't give.
- **Re-solving on load, storing only driving inputs.** Opening would depend on the solver
  and could move geometry the user never touched; deltas would stop describing what a
  command did.
- **Accepting redundant constraints as over-constrained** (Onshape, FreeCAD). Leaves
  sketches one dimension change away from a conflict; rejecting with the implying ids is
  more useful to an agent and keeps documents consistent.
