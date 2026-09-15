# ADR 0003: 2D constraint solver is planegcs

- **Status:** Proposed. Pending the macOS build test in Phase 0.5.
- **Date:** 2026-09-14
- **Related:** ADR 0005 (determinism scope for solved geometry), ADR 0006 (license policy)

## Context

V1.5 adds sketch constraints (coincident, horizontal, vertical, parallel, tangent, driving
dimensions). A geometric constraint solver is a deep specialty, and we are not writing one.

V1 prepares for it without depending on it:

- **Dimensions** reference entity features rather than coordinates.
- **V1 dimensions are driven:** they display measurements and never move geometry.
- **Rectangles** are single entities that a solver can expand internally.

## Decision (proposed)

Use **`planegcs`**, Python bindings for FreeCAD's PlaneGCS solver, LGPL-2.1-or-later
(github.com/spookylukey/planegcs).

What we found on 2026-09-14:

- **Latest release:** 0.8.0 (June 2026), for Python 3.12 and 3.13.
- **Prebuilt wheels:** Linux x86_64 and Windows only. **No macOS wheels.** On a Mac it builds
  from the source package, which needs a C++ toolchain plus Eigen and Boost.

Because this is a Mac-first project, the decision stays Proposed until the build is tested,
in week one rather than month three.

### Phase 0.5 build test, and what each outcome means

1. **Setup:** Xcode's clang plus Eigen and Boost (via Homebrew), on Apple Silicon.
2. **Build:** `planegcs` from the source package for Python 3.13.
3. **Solve:** a rectangle made of four lines with coincident, horizontal, and vertical
   constraints plus two distance constraints. The result must be correct and stable when
   solved again.
4. **Wheel:** check that a macOS arm64 wheel can be built in CI (cibuildwheel).

- **Steps 1–3 pass:** Accepted. We build our own macOS wheels in CI until upstream publishes
  them, and offer the CI setup upstream.
- **The build fails:** revisit before planning V1.5. Options include fixing and upstreaming
  the macOS build, vendoring and building the solver ourselves, or a commercial solver
  (Siemens D-Cubed).

## Consequences

- **Determinism:** the solver is iterative, so solved positions can differ in the last bits
  across platforms. ADR 0005 records that V1.5 must choose between storing solved positions
  compared within a tolerance, and storing only driving inputs and re-solving on load.
- **Risk:** it's a young binding maintained by a small team. It sits behind our own interface
  in `caliper/engine/constraints/`, so it can be replaced.

## Alternatives considered

- **SolveSpace / `py-slvs`:** a capable solver, but GPL-3. Banned by ADR 0006.
- **Writing our own:** no.
- **A general optimizer (SciPy least-squares):** works for toy sketches. It lacks the
  decomposition, redundancy detection, and robustness a real sketcher needs.
- **Siemens D-Cubed DCM:** the industry standard, commercially licensed. Not now.
