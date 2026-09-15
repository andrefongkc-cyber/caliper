# ADR 0006: License policy — no GPL or AGPL dependencies, ever

- **Status:** Accepted
- **Date:** 2026-09-14
- **Enforced by:** `pyproject.toml` (pyside6-essentials, ruff banned-api), `tests/test_licenses.py`,
  `.github/workflows/licenses.yml`

## Context

We want every commercial option open: selling licenses, closed-source distribution,
acquisition. GPL or AGPL code linked into the product would force the whole product's
source to be released under the same license. License contamination tends to be found in
acquisition due diligence, at the worst possible moment.

## Decision

### Rules

- **Banned:** GPL-2, GPL-3, and AGPL dependencies, anywhere in the lockfile. That includes
  dev tools, which keeps the rule simple and checkable.
- **Allowed:**
  - Permissive licenses (MIT, BSD, Apache-2.0, PSF, and similar).
  - MPL-2.0 (file-level copyleft, fine for unmodified dependencies).
  - LGPL, through dynamic linking.
- **Dual-licensed packages** are allowed if any of their options is allowed. PySide6 is
  "LGPL-3.0 OR GPL-2.0 OR GPL-3.0", and we use it under LGPL-3.0.
- **Named bans:**
  - `py-slvs` / SolveSpace (GPL-3).
  - PyQt5 and PyQt6 (GPL or commercial).
  - CGAL's GPL packages.
  - **GPL-only Qt modules:** Qt Charts, Qt Data Visualization, Qt Graphs, Qt Quick 3D, and
    Qt Virtual Keyboard. PySide6 as a whole is LGPL, but these modules are GPL-only in
    open-source Qt, so importing one would violate this policy without anyone noticing.

### Enforcement (three layers)

1. **Qt modules can't be installed.** The `app` extra uses `pyside6-essentials`, which leaves
   out `pyside6-addons`, where those modules ship. Ruff's `banned-api` rule also rejects
   imports of those modules and of `py_slvs`/`slvs`.
2. **The license test fails** on any installed GPL or AGPL distribution without an allowed
   alternative. It runs in every CI test run, and against all extras whenever `uv.lock`
   changes. False positives go in its `REVIEWED` table with a reason, in a PR that cites
   this ADR.
3. **Review:** any PR that adds a dependency states its license.

### LGPL obligations when we distribute the app

These apply to PySide6/Qt, OCCT, and planegcs.

- **Separate libraries:** keep LGPL libraries as separate dynamic libraries the user could
  replace. Don't statically link them, and don't merge them into our binaries.
- **Notices:** ship their license texts and copyright notices.
- **Modifications:** if we modify an LGPL library, publish those modifications.

This is an engineering policy, not legal advice. Have counsel confirm the distribution
setup before the first commercial release.

## Consequences

- **V2 3D viewport:** Qt Quick 3D is ruled out. OCCT's own viewer (LGPL), VTK (BSD), or a
  custom renderer remain. Not decided now; this ADR only records the constraint.
- **Solvers and kernels:** some good ones are off the table (SolveSpace, much of CGAL).
  ADRs 0001 and 0003 chose around them.
- **Packages with missing license metadata** pass the automated test unnoticed, so review
  still matters.
