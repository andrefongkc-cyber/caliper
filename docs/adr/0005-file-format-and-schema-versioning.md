# ADR 0005: File format and schema versioning

- **Status:** Accepted
- **Date:** 2026-09-14
- **Implementation:** `caliper/engine/io/` (Phase 0.6 onward)
- **Related:** ADR 0002 (command bus and snapshot document)

## Context

The project file is a snapshot of the document (ADR 0002) and is the most expensive thing
in the system to change later. It must be portable, diffable, and readable by a person. The
same inputs must produce the same bytes, which is what makes replay a meaningful check. And
it must be safe to email to a machine shop.

## Decision

### Container

One UTF-8 JSON file with the extension `.caliper`:

```json
{
  "document": {
    "entities": {
      "e1": {
        "corner": {"x": 0.0, "y": 0.0},
        "height": 50.0,
        "kind": "rectangle",
        "width": 120.0
      }
    },
    "next_id": 2
  },
  "format": "caliper.document",
  "schema_version": 1,
  "units": {"angle": "deg", "length": "mm"}
}
```

(Shown compacted; the real output puts every key on its own line.)

### Canonical encoding

Byte-identical output depends on every one of these rules:

- **Encoder settings:** `json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)`
  plus a trailing newline, encoded as UTF-8.
- **Line endings:** always `\n`, on every OS. Windows text mode would silently write `\r\n`.
- **Float formatting:** Python's shortest round-trip `repr`, which is what `json` uses.
  Never fixed decimal places, which silently lose precision around the 17th significant
  digit.
- **Float fields stay floats:** always written as floats (`120.0`, never `120`). The bus
  normalizes ints before they reach the document.
- **No NaN or infinity:** the bus rejects non-finite values, and the writer refuses them.
- **Entity order:** entities are keyed by id and ordered by `sort_keys`, so `"e10"` sorts
  before `"e2"`. The order carries no meaning.
- **Enums and type tags:** enums are written as their string values, and each entity's type
  tag is `"kind"`.

### Inputs only

The file stores what was specified, never what can be computed. That means no arc endpoints,
no driven dimension values, no kernel output (areas, tessellations, B-rep), and no caches.

Two reasons, and they point the same way:

1. **Determinism.** Values computed with `sin`, `cos`, `atan2`, or by OCCT can differ in the
   last bits across CPUs, operating systems, and library versions. Keep them out, and a V1
   file is byte-identical on every platform. Replay itself only does basic IEEE-754
   arithmetic (`+ − × ÷`) on stored inputs, which is deterministic everywhere.
2. **A declarative file.** A file of inputs is exactly the shape the V2 parametric feature tree
   needs: a feature is its parameters, and the solid is regenerated from them.

### Schema versioning and migrations

- **Version number:** `schema_version` is an integer starting at 1. Any change to what gets
  written increments it, including new entity kinds or fields, because readers are strict
  and reject unknown content rather than guessing.
- **Migrations:** each is a pure function over raw JSON (`dict -> dict`) from version n to
  n+1. They are applied in sequence on load, and each has a fixture test (old file in,
  expected new file out).
- **Newer files:** load refuses a file with a newer `schema_version` than it supports, and
  says so clearly.
- **Saving:** save always writes the latest version.
- **The framework exists from day one,** even with zero migrations.

### Replay is a check, not the load path

Loading reads the snapshot and is O(entities), however long the project's history.
`python -m caliper.engine replay script.json` runs a command script headlessly against an
empty document and writes a snapshot. Tests assert that snapshot is byte-identical to the
expected file.

### History and transcript

- **History section:** an optional `"history"` key can hold the resolved commands. It is off
  by default. Export always strips it. It never contains prompts or AI conversation.
  (Project files get sent to suppliers, and "the last vendor quoted us $14k" must never
  travel with a part.)
- **AI transcript:** the AI/session transcript is a separate sidecar file. It is never
  embedded in a `.caliper` file and never exported.

### Determinism scope

- **V1:** byte-identical across operating systems and CPUs for the same `schema_version`.
- **V1.5 (constraint solver):** planegcs solves iteratively, and solved positions will differ
  in the last bits across platforms. V1.5 must choose between storing solved positions and
  comparing across platforms within a tolerance, or storing only driving inputs and
  re-solving on load. Recorded here so it isn't a surprise.

## Consequences

- Project files diff cleanly in git and can be read and reviewed by a person.
- Load time doesn't grow with history.
- Files are safe to send outside the company by default.
- JSON is larger than a binary format. Revisit only if profiling demands it; heavy derived
  data never goes in the file anyway.
- Every schema change costs a migration and a fixture test. That cost is intended.
- The V1 document is a flat entity map with no sketch or plane container. V2 will migrate it
  into sketches inside a part's feature tree. Modeling sketches now would add a sketch
  parameter to every V1 command for no V1 feature, and the migration is mechanical because
  the file is declarative.

## Alternatives considered

- **Command log as the file.** See ADR 0002.
- **Binary format (CBOR, SQLite).** Not diffable or readable by a person, and there's no
  measured need.
- **Fixed-decimal float formatting.** Loses precision and breaks round-tripping.
- **Storing derived geometry for faster load or interop.** Breaks cross-platform determinism.
  Caches belong in memory or in separate cache files.
- **A serialization library's own JSON output.** Float formatting and key order would be the
  library's decision and could change between versions.
