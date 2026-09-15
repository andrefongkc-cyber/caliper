# Engine (Stream A)

Read `docs/workplan/core.md` first. You own `caliper/engine/`, `bench/`, and `tests/` except
`tests/app/`. Don't edit `caliper/app/`. If you need a contract change, stop and write up
what and why for the user.

- **Headless and platform-independent.** CI runs the engine on Linux with no Qt or OCCT installed.
- **`mypy --strict` must stay clean.** Keep the engine free of third-party runtime dependencies unless an ADR says otherwise.
- **2D geometry is analytic.** Use the `Kernel` only for B-rep work. `OCP` is imported only in `geometry/occt_kernel.py`.
- **Kernel changes:** any change to `FakeKernel` or `OCCTKernel` must keep `tests/engine/geometry/test_kernel_conformance.py` passing for both.
- **Deltas:** hold entity values only, never derived geometry. Undo inverts deltas; commands never define inverses.
- **Serialization:** canonical JSON per ADR 0005, with `repr` floats, sorted keys, `\n` line endings, no derived values. Every schema change needs a migration and a fixture test.
- **Property tests:** use hypothesis for geometry invariants (translation invariance, round-trips, undo-then-redo equals the original).
- **Bench:** add a `bench/` case when a user-visible capability lands.
