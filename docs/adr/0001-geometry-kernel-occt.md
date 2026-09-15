# ADR 0001: Geometry kernel is OpenCascade (OCCT), behind a provisional Kernel protocol

- **Status:** Accepted
- **Date:** 2026-09-14
- **Contract:** `caliper/contracts/kernel.py` (Provisional)
- **Related:** ADR 0005 (inputs-only files), ADR 0006 (license policy)

## Context

Caliper's wedge is a CAD system an agent can verify: measure, query, and assert against
real boundary-representation (B-rep) geometry that it can also edit. That rules out
mesh-only and implicit-surface kernels: an engineering part needs exact faces and edges,
because manufacturing, drawings, and constraints refer to them.

Writing a B-rep kernel is a decade-scale project. The commercial kernels (Parasolid, ACIS)
cost six figures a year.

## Decision

- Use **OCCT through `cadquery-ocp`** (Python bindings, Apache-2.0, over OCCT under
  LGPL-2.1 with a linking exception). At the time of writing: version 8.0.1 (OCCT 8.0),
  with Apple Silicon wheels for Python 3.11–3.14.
- Install it through the optional **`occt` extra**. The engine, the shell, and most CI
  never need it.
- Only `caliper/engine/geometry/occt_kernel.py` imports `OCP` (invariant 7, enforced by
  `tests/test_architecture.py`).
- Put everything OCCT-specific behind the **`Kernel` protocol**, which is **Provisional**. It
  has four methods (`make_face`, `area_properties`, `bounding_box`, `is_valid`), sized to
  what V1 queries need. It is expected to change shape at V2, when solids, booleans, and
  topology arrive.
- **2D sketch geometry is analytic.** Lines, circles, arcs, and rectangles and their
  distance, bounding-box, and hit-test queries are plain Python. The kernel is used only
  for real B-rep work (profiles into faces, area properties, later extrusion and booleans).
- **`FakeKernel`** is an analytic implementation for tests. **One conformance suite runs
  against both kernels**, so tests can't pass on the fake and fail on the real kernel.
- **Kernel output is never serialized** (ADR 0005). It can differ in the last bits across
  platforms and OCCT versions.

## Consequences

- We get an industrial kernel (booleans, fillets, STEP I/O) for free, with commercial use
  allowed.
- `cadquery-ocp` is a large download, and our Python version is capped by its wheel
  availability. Today that allows 3.13.
- OCCT has quirks: tolerance handling, bounding boxes enlarged by default, and naming of
  generated faces that isn't stable across rebuilds (the "topological naming problem").
  The conformance suite catches the first two. The third gets its own ADR at V2.
- A different kernel later means replacing one module and passing the same conformance
  suite, not a rewrite.

## Alternatives considered

- **Parasolid / ACIS:** best in class, licensed at six figures. Not now.
- **Write our own:** no.
- **CGAL:** most of its useful packages are GPL or commercial. Banned by ADR 0006.
- **truck / Fornjot (Rust):** permissively licensed and promising, but not yet mature enough
  for industrial fillets and booleans.
- **libfive and other signed-distance kernels:** implicit geometry with no exact faces or
  edges to reference.
- **Manifold:** fast, robust mesh booleans, but meshes rather than exact geometry.
- **CadQuery or build123d directly:** excellent high-level APIs on the same OCP bindings.
  We wrap OCP ourselves to keep full control of introspection and error reporting, and can
  borrow their Apache-2.0 code where useful.
