# ADR 0011: A part is ordered features, and sketches sit on planes

- **Status:** Proposed (F1 of the Caliper Engine Plan, drafted by Andre on 2026-09-30;
  Accepted once Lucas reviews the contract from the app's side, F7)
- **Date:** 2026-09-30
- **Contract:** `caliper/contracts/document.py`, `commands.py`, `queries.py`, `errors.py`
- **Related:** ADR 0002 (commands and automatic deltas), ADR 0005 (file format; it planned
  this move), ADR 0009 (constraints in the document), ADR 0010 (checks in the document),
  the dependency and recomputation graph in `docs/workplan/core.md`

## Context

A V1 document is a flat map of 2D entities with no sketch or plane: geometry, dimensions,
constraints, and checks, keyed by id. ADR 0005 put off sketches and planes until V2 so V1
commands wouldn't carry a sketch parameter no V1 feature needed, and said the move would
be mechanical because the file holds inputs only.

V2's first milestone is one extruded plate: a 120 × 50 sketch on XY, extruded 10 mm, a
volume check of 60,000 mm³, the width changed to 140 (70,000 mm³), then undo. For that, a
sketch has to be a thing placed on a plane, and features have to come in order after it,
each reading what came before. F1 settles that shape, and the file that stores it, before
the kernel grows solids (F2) or the extrude exists (F3). That way, the engine and the app
(F5–F7) build against one fixed contract.

What constrains the choice:

- **Invariant 4 and ADR 0005.** Every V1 file, fixture, and script must migrate and replay
  unchanged: the same geometry, the same ids, the same `next_id`.
- **The entity map is read everywhere.** About 450 places in the engine, the AI layer, the
  app, and the tests read `Document.entities` as the sketch's contents. 73 tests compare it
  whole or count it, and the app's Select All, browser, and canvas loop over all of it.
- **ADR 0002.** Deltas are computed automatically from the entity map, which is what makes
  undo, transactions, History, and replay work without hand-written inverses.
- **No speculative generality** (CLAUDE.md). Only what V2's milestone needs, shaped so the
  next things (an extrude, a sketch on a face) add to it instead of replacing it.

## Decision

**A document is one part.** Besides its entities and `next_id`, it holds
`Document.features`: the part's features in order, each with its id. F1 has one kind,
`Sketch`; F3 adds `Extrude`, and later features join the same list. Order is recompute
order. A feature may refer only to features before it, which keeps the feature graph
acyclic by construction (F3 enforces it). Reordering is a command for later. The union of
feature kinds is `PartFeature`.

**A sketch sits on a plane.** `Sketch(id, plane)`. `Plane` is one of the part's three origin
planes, with the sketch's x and y axes fixed so every 2D coordinate has one meaning in 3D:

| Plane | Sketch x | Sketch y | Normal (x × y) |
|---|---|---|---|
| `xy` | +X | +Y | +Z |
| `xz` | +X | +Z | −Y |
| `yz` | +Y | +Z | +X |

Each plane's origin is the part's origin. Offset planes and a sketch on a face come later,
as new fields or kinds, each a schema change with a migration. A sketch on a face waits for
persistent naming (F4).

> **Changed by [ADR 0016](0016-sketching-at-any-angle-and-on-faces.md)** (Proposed): a sketch sits on a plane or on a flat face of
> an extrude, named as ADR 0014 names faces (`FaceRef`), with its plane worked out from that
> extrude's inputs (file schema 5).

**The entity map keeps its meaning: what sketches hold, plus the part's checks.** A sketch
isn't an entity. It lives in `features`, so nothing that loops over `entities` meets a new
kind:

- **Geometry** (point, line, circle, arc, rectangle) names its sketch in a new field,
  `sketch`. Its coordinates are in that sketch's plane.
- **Dimensions and constraints** belong to the sketch of the geometry they refer to. That
  isn't stored: it can be worked out, and a stored copy could disagree (ADR 0005: inputs
  only). All of one dimension's or constraint's references must be in one sketch. Deleting
  geometry already deletes what refers to it, so a dimension or constraint never outlives
  the sketch it's in.
- **Checks** belong to the part. A check may measure any sketch, but a 2D measurement reads
  one sketch: two points in different planes have no 2D distance. Volume and other 3D checks
  (F3) will read features.

**One id space.** Entity and feature ids are unique across the document. The engine
allocates `e1`, `e2`, ... from `next_id`, as before. The sketch a part starts with is `e0`
(`FIRST_SKETCH`), an id the counter never gives out. So every V1 id and every `next_id`
stays as it was, and a V1 script's references to `e1`, `e2`, ... still name what they named.

**A new part has one sketch, `e0` on XY** (`Document.empty()`, and `Document`'s default).
Today's app and every V1 script work unchanged: they draw in the only sketch.

**Commands.**

- **`CreateSketch(plane, id)`** adds a sketch at the end of the feature list.
- **Commands that create geometry** take an optional `sketch`. Left out, it means the
  part's only sketch. A part with none, or with several, refuses it with `sketch.required`.
  The resolved command records the sketch, like an allocated id, so a recorded command
  replays into the same sketch however many there are by then.
- **Dimensions and constraints** are refused with `sketch.mixed` when their references
  are in different sketches. `CreateDimension` checks this before inferring anything.
- **`FilletCorner`**: both lines must be in one sketch, and the arc goes in it.
- **`MoveEntities`** moves geometry of one sketch by (dx, dy) in that sketch's plane. It
  refuses geometry from more than one sketch with `sketch.mixed`, and refuses a sketch's
  id.
- **`DeleteEntities`** given a sketch's id deletes the sketch, its geometry, and the
  dimensions and constraints on that geometry, in one delta. Checks stay, failing (C-1).
- **`ModifyEntity`** can change a sketch's `plane`. The geometry keeps its 2D coordinates,
  so the sketch moves to the new plane as a whole. A sketch's `id` and an entity's `sketch`
  can't be changed. Moving geometry to another sketch would have to move its relations too,
  so it waits for a command that does all of it.

**Deltas** keep entity values before and after, as ADR 0002 has them. They also hold the
feature list before and after, only when it changed (`None` otherwise), so undo and redo
restore it exactly. `added`, `removed`, and `modified` still name entities only.

**Queries.**

- **New:** `sketch_of(id)` says which sketch an entity is in. It returns `None` for a
  check, a feature, or an unknown id.
- **Measurements read one sketch:** `measure_distance`, `bounding_box`, `area_properties`,
  and `check` say `sketch.mixed` otherwise. `bounding_box()` with no ids still means all the
  geometry, and that geometry has to be in one sketch.
- **Relations stay inside a sketch.** `suggest_constraints` only suggests within one sketch.
  `applicable_constraints` and `infer_dimension` refuse references from more than one.
- **Picking stays part-wide for now:** `entity_at_point`, `entities_in_box`,
  `nearest_feature`, and `reference_at_point`. So does `solve_status`, which adds up every
  sketch's degrees of freedom. Whether picking should take a sketch is the app's call (see
  below).

**New error codes:** `sketch.required` and `sketch.mixed`. No existing code changes meaning.

**File schema 4.** The document gains `"features"`, an ordered list. The list carries
extrudes too (ADR 0013): schema 4 reaches `main` with F3, so it isn't bumped again.

```json
{"id": "e0", "kind": "sketch", "plane": "xy"}
```

Each geometry entity gains `"sketch"`. Migration 3 → 4 puts everything a schema-3 file
holds into one sketch on XY: it adds `e0` to the feature list and `"sketch": "e0"` to every
geometry entity, and leaves ids and `next_id` alone. A schema-3 file that already used the
id `e0` gets `e{next_id}` instead, with `next_id` moved past it. Files from schemas 1 and 2
go through every migration in turn. Golden files are rewritten at schema 4. The schema-3
originals are kept as migration fixtures.

**The AI tools** follow the same rules:

- **Drawing:** the create tools, `create_outline`, and `create_arc_through_points` take an
  optional `sketch`.
- **Repeats:** a mirror or pattern draws its copies and layout geometry in its originals'
  sketch. Originals from two sketches are refused.
- **The summary:** `inspect_document` lists the part's features, and shows an entity's
  sketch only when the part has more than one.

**Not exposed yet.** `CreateSketch` isn't an AI tool. The palette can't show it either: it
has no form for a plane. Both wait until the app can show a sketch on its plane and has an
active sketch to draw in (F6). With one sketch, the app and the AI behave exactly as in V1.

**Names.** `Feature`, the enum of a geometry's points and curves in a `Ref`, keeps its name.
Renaming it would touch every reference in the code for no change in the files. "Features"
on a document means part features, and their union is `PartFeature`.

## Consequences

- **V1 compatibility:** V1 files, fixtures, and scripts load and replay to the same geometry
  and ids. Only the schema-4 additions change bytes, and tests pin both the migration and the
  replay (`tests/engine/io/test_snapshot.py`, `tests/engine/test_replay.py`, the bench).
- **Code that reads the entity map** sees what it always saw. Code that builds a `Document`
  from another one must keep its features (`dataclasses.replace`). A test runs every command
  on a part with two sketches to catch a place that drops them.
- **Multi-sketch parts:** they're valid documents headlessly from F1. The app still draws
  every sketch on one 2D canvas and can't choose which sketch to draw in. That's F6.
- **What's next:** F3 adds `Extrude` to `PartFeature`, reading a sketch by id, and a volume
  check reads a feature by id, with no restructuring. The recompute graph (core.md, items 1
  to 3) gets its nodes: sketches and features, in order, as item 2 needed.
- **Costs:** a schema bump, a migration, and rewritten golden files. Deltas grow two fields.
  Geometry has one more field, which Properties shows as read-only text.

## Alternatives considered

- **Sketches as nested containers** (`Sketch.entities`). This is the most literal reading of
  "a container", but every read of `document.entities` and the automatic delta would change
  at once, and ids would need a sketch prefix or a global index anyway. Revisit if per-sketch
  storage is ever needed, for size or for sharing a sketch between parts.
- **Sketches as entities in the map**, as checks became in ADR 0010. One less structure,
  but every loop over entities would meet a new kind: 73 tests, and the app's Select All,
  browser, and canvas. Select All then Delete would delete the sketch.
- **Every entity stores its sketch, dimensions and constraints included.** That's
  redundant: it can be worked out from their references, and could disagree with them.
- **Readable sketch ids** (`sketch1`), or a counter per kind. Ids are opaque, and a second
  counter is more document state. A V1 file could already use such an id, and `e0` can't
  collide with an allocated id.
- **A new part with no sketch**, as some CAD systems start. Every V1 script and replay would
  need a `CreateSketch` first, and allocated ids would shift by one. The app can still open
  differently once it chooses to (F6).
- **Arbitrary planes now** (an origin and a normal). No feature needs them yet. Adding them
  later is a schema change with a migration, which ADR 0005 makes cheap.

## What the app has to decide (F7, Lucas)

Nothing here changes what the app does today. These are the choices it makes when it grows
sketch mode (F6), and the contract supports any answer:

1. **An active sketch:** UI state, never in the document. When the part has more than one
   sketch, drawing must pass `sketch` in every create command, or it's refused with
   `sketch.required`.
2. **File → New:** start with `e0` on XY, as today, or with no sketch and ask for a plane.
3. **What the canvas draws:** the active sketch only, or every sketch on its plane, which
   needs the 3D view (F5).
4. **Picking:** `entity_at_point`, `entities_in_box`, `nearest_feature`, and
   `reference_at_point` are part-wide. They could take a sketch.
5. **Properties** shows "Sketch e0" read-only on geometry: keep it, hide it, or show a name.
   Sketches have no name field. Should they get one, as "Sketch 1" in a feature list?
6. **The feature list** (the browser grown, F6): read `Document.features` in order, and
   `Queries.sketch_of` for where an entity is. A view that follows deltas gets
   `Delta.features_before` and `Delta.features_after`.
7. **The 3D viewport's mesh** (F5) belongs to the kernel contract (F2), not this one.

### The answers, as built (F6 and F7, 2026-10-01; for Lucas to confirm)

The app was built on this contract unchanged: nothing in `caliper/contracts/` changed for
F6 or F7. One rule the window and the AI share moved into the engine (`part.in_sketch`, item 1).

1. **An active sketch:** yes, UI state in `DocumentSession` (`active_sketch`). A new part edits
   `e0`; a file opens on its last sketch; deleting or undoing the edited sketch falls back to
   another. Create commands and `CreateExtrude` that name no sketch are given the edited one
   by `part.in_sketch`, which the session, the in-app assistant, and Claude Desktop's calls
   all use, and only when the part has more than one sketch. With one, every V1 file, the
   command goes as it came, so recorded commands and replays are unchanged.
2. **File → New:** `e0` on XY, as today. New Sketch on XY, XZ, or YZ adds and edits another.
   *Changed by [ADR 0015](0015-sketching-in-3d-from-the-parts-planes.md): in the 3D tab a new
   part has no sketch, only its planes; the 2D tab's new document keeps `e0` on XY.*
3. **What the canvas draws:** the edited sketch only, in its own 2D coordinates. Sketches on
   their planes in 3D wait for a later version; the 3D view (ADR 0012) shows the solid.
   *Changed by ADR 0015: the 3D view shows every sketch on its plane, and a sketch is edited
   in 3D, facing its plane, by the same canvas over the part.*
4. **Picking:** the queries stay part-wide. The session keeps a view of the document holding
   only the edited sketch's entities (`sketch_view`, `sketch_queries`), and the canvas, its
   tools, the browser, and Select All use it, so nothing in another sketch can be picked or
   edited by accident. No query needed a `sketch` argument.
5. **Properties:** the sketch row stays read-only. Sketches have no name field: the Part
   panel numbers them by kind ("Sketch 1", "Extrude 1"), which needs no contract change.
6. **The feature list** is the Part panel: `Document.features` in order, `feature_error` for
   a failing feature, and `solid_properties` for the volume in its heading. It rebuilds on
   each change rather than following `Delta.features_*`, which is fast at V2's sizes.
7. **The mesh:** `Queries.mesh`, from F2. The view keeps the last good mesh, with the reason,
   when a feature fails.

Found by the review and fixed (F7): with two sketches, the assistant's and Claude Desktop's
drawing was refused unless the model named a sketch; the Checks panel's sketch size measured
every sketch at once; a proposal's preview drew another sketch's changes; and an extrude
proposed with a check was labelled "Assistant Changes". Each has a test that fails without
its fix.

## How to review it

What changed, in the order to read it:

1. **The contract:**
   - `caliper/contracts/document.py`: `Plane`, `Sketch`, `Document.features`, and geometry's
     `sketch`.
   - `commands.py`: `CreateSketch`, `sketch` on the create commands, and `Delta`'s features.
   - `queries.py`: `sketch_of`.
   - `errors.py`: the two new codes.
2. **The rules:** `caliper/engine/part.py`, and where they're used in
   `engine/commands/validation.py` and `handlers.py`.
3. **The file:** `engine/io/snapshot.py`, migration 3 → 4. Every schema-3 golden file is kept
   in `tests/engine/fixtures/v3/`.
4. **The tests that hold it:**
   - `tests/engine/test_part.py`: every rule here, every command on a two-sketch part, and a
     property test.
   - `tests/engine/io/test_snapshot.py`: the migration, byte for byte.
   - `tests/engine/test_replay.py`: a two-sketch replay.
   - `tests/engine/constraints/test_numerics.py`: N1's digests of `main`'s own files, with the
     part taken out again.
   - `tests/ai/test_sketches.py`: the AI tools.

Accept it by changing **Status** to Accepted, with the date and who reviewed it. To change
something, say what. The contract isn't frozen until then.
