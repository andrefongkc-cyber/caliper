# ADR 0003: 2D constraint solver is planegcs

- **Status:** Superseded by [ADR 0008](0008-constraint-solver-our-own.md). It had been
  Accepted after the Phase 0.5 build test (see the result below).
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

## Build test result (2026-09-15)

Steps 1-3 pass on Apple Silicon, so this ADR is Accepted. Details:

- **Setup:** Xcode 26.6 (Apple clang 21), Homebrew `eigen@3` 3.4.1 and `boost` 1.92.0, used as
  headers only. CMake and pybind11 come from PyPI through scikit-build-core, so they need no
  Homebrew package. `eigen@3` rather than Homebrew's `eigen` (5.0.1), because planegcs builds
  against Eigen 3.4 upstream.
- **Build:** `planegcs` 0.8.0 builds from its source package for Python 3.13 in about 15
  seconds, with no errors. Its own test suite passes: 227 passed, 1 skipped.
- **Solve:** a rectangle of four lines with coincident, horizontal and vertical constraints
  plus width and height distances, and one corner fixed, reports 0 degrees of freedom with no
  conflicting or redundant constraints and solves to exactly (0,0)-(100,50). Five further
  solves and a freshly built sketch give bit-identical results. Changing the width to 120
  re-solves correctly. Over-constraining it is reported as conflicting rather than silently
  solved.
- **Wheel:** `MACOSX_DEPLOYMENT_TARGET=14.0 uv build --wheel` produces a
  `macosx_14_0_arm64` wheel that links only libc++ and libSystem, so it is self-contained.
  The default deployment target is the build machine's OS, so CI must set it explicitly.
- **Not yet done:** step 4, building those wheels in CI with cibuildwheel. It is only needed
  when V1.5 starts depending on planegcs.

One design note for V1.5, from the solve test: an unconstrained rectangle also solves, but a
width change moves both sides. The sketcher has to pin what stays put; the V1 default is the
bottom-left corner, matching how `ModifyEntity` treats rectangles today.

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
