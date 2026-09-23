# ADR 0008: 2D constraint solver is our own, in Python

- **Status:** Accepted. Supersedes ADR 0003's choice of planegcs.
- **Date:** 2026-09-22
- **Implementation:** `caliper/engine/constraints/`
- **Related:** ADR 0003 (planegcs), ADR 0009 (how constraints live in the document)

## Context

ADR 0003 chose planegcs and rejected writing our own solver, because a general optimizer
"lacks the decomposition, redundancy detection, and robustness a real sketcher needs". The
build test passed, but using planegcs has costs that surfaced when the constraint work began:

- **No macOS wheels.** Every Mac (both streams' and the macOS CI job) would build it from
  source against Homebrew's Eigen and Boost until we build wheels in CI, which ADR 0003's
  step 4 never did. The engine would import it, so the shell couldn't run without it.
- **Maintainer-owned changes** to `pyproject.toml`, `uv.lock`, and `.github/` before any
  constraint could be tested.
- **Answers the shell needs that planegcs doesn't give:** degrees of freedom per entity
  (issue #18, decision 3), and conflicts explained in our own constraint ids.
- **Types we need anyway.** Normal, symmetric circles, curvature (G2), and the reference
  kinds of our entities (rectangles as one entity) would be composed from its primitives.

## Decision

Write the solver in Python, with no dependencies, and answer each of ADR 0003's objections
directly:

- **Equations, not code paths.** Each constraint type is a table entry: the selections it
  accepts and the equations it adds. Dual numbers give exact derivatives, so an equation is
  written once as ordinary arithmetic (`relations.py`, `ad.py`).
- **Decomposition.** Constraints join geometry into clusters that solve independently; only
  clusters a command touched are solved (`sketch.py`).
- **Redundancy and DOF.** One rank-revealing Gram-Schmidt over the Jacobian rows gives the
  Newton step, the rank (so DOF is unknowns minus rank, per cluster and per entity), and,
  for a dependent row, which rows it repeats (`linalg.py`).
- **Robustness.** Minimum-norm Newton with backtracking; steps never pass through invalid
  geometry; staged solves that move as little as possible; a nudged retry for saddles;
  collapsed geometry counts as a failure, not a solution.
- **Conflicts.** QuickXplain finds the smallest set of existing constraints that can't hold
  with the change, and those ids go into `Error.ids`.
- **Determinism.** Only IEEE arithmetic, `math.hypot`, and (for arcs and angles) `math`'s
  trig. A solve with lines and circles is bit-identical everywhere; one involving arcs or
  angles is bit-identical on a platform and may differ in the last bits across platforms.

The solver stays behind the command layer: nothing outside `caliper/engine/constraints/`
knows how it works, so planegcs (or another solver) can replace it without a contract change.

## Measured (2026-09-22, M2 Pro, Python 3.13)

A zig-zag of n lines, joined end to end, alternately horizontal and vertical, each with a
driving length and the first corner fixed: one fully constrained cluster.

| Lines | Unknowns | Solve status | Change one length | Reject a conflict |
|---|---|---|---|---|
| 10 | 40 | 1.4 ms | 6.6 ms | 22 ms |
| 20 | 80 | 7.9 ms | 10.7 ms | 97 ms |
| 40 | 160 | 51 ms | 56 ms | 336 ms |

Random sketches, with constraints chosen from what `applicable_constraints` offers for a
random selection:

- **On fresh geometry** (1,048 constraints, each between different entities): no conflicts.
  The only refusals are constraints that always hold, such as two rectangles' sides made
  parallel.
- **On sketches that build up** (400 sketches, 2,157 constraints): 1,304 applied and 853
  refused (as conflicts or redundancy), each naming the constraints involved. No applied
  command ever left a constraint unsatisfied.

## Consequences

- No new dependency, no native build, and CI unchanged. The engine keeps its "no
  third-party runtime dependencies" rule.
- Per-entity DOF and conflict ids come for free, which is what the shell's P7 colouring and
  an agent's retry loop need.
- **Performance is the risk.** Dense linear algebra in Python grows with the cube of a
  cluster's unknowns. The table is fine for commands; a live drag preview at 60 Hz (issue
  #18, decision 6) would fit clusters up to about 100 unknowns. Past that: sparse
  elimination, or planegcs behind the same seam.
- **It is a local solver.** A change that needs a large jump to reach its solution (a
  horizontal line made vertical while other constraints hold it) can fail where a global
  method would succeed. It then reports a conflict with the constraints involved rather
  than a wrong result.
- Robustness is ours to keep: the property tests in `tests/engine/constraints/` are the
  guard.

## Alternatives considered

- **planegcs (ADR 0003).** Still a good solver. Rejected for now for the costs above, not
  its quality; the seam keeps it available.
- **SciPy least squares.** A large dependency for a small part of what's needed; no
  redundancy or conflict analysis.
- **SolveSpace / `py-slvs`.** GPL-3, banned by ADR 0006.
