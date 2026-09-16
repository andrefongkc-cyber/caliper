# ADR 0007: Keep the GPL-only Qt modules out of the app bundle

- **Status:** Accepted
- **Date:** 2026-09-15
- **Extends:** ADR 0006 (license policy), ADR 0004 (PySide6 shell)
- **Enforced by:** `pyproject.toml` (ruff banned-api), `tests/test_qt_gpl_modules.py`, and the
  packaging configuration when packaging lands

## Context

ADR 0006 bans GPL and AGPL dependencies, and `pyside6-essentials` was chosen over the full
`pyside6` because the GPL-only Qt add-ons live in `pyside6-addons`.

Checking that assumption in Phase 0.5 showed it is not quite true. `pyside6-essentials`
6.11.2 bundles libraries from three modules that Qt licenses under the GPL v3 only
(doc.qt.io/qt-6/licensing.html):

- **Qt Lottie Animation:** `QtLottie`, `QtLottieVectorImageGenerator`, `QtLottieVectorImageHelpers`
- **Qt Quick Timeline:** `QtQuickTimeline`, `QtQuickTimelineBlendTrees`
- **Qt Qml Compiler:** `QtQmlCompiler`, which the bundled `qmllint`, `qmlls` and `qmlformat` use

It also ships Qt's tools (Designer, Linguist, Assistant, the qml* utilities), which are GPL v3
with the Qt GPL exception: using them to build software does not affect the software's license.

None of the Python modules it ships are GPL-only, and the shell never loads the libraries
above: it is Qt Widgets, imports no QML, and a running main window loads only QtCore, QtGui,
QtWidgets, QtDBus, PySide6 itself, and the platform plugin. The dependency license test
can't see any of this, because the package as a whole is "LGPL-3.0-only OR GPL-...".

## Decision

1. **Keep `pyside6-essentials`.** What Caliper links against is LGPL, which ADR 0006 allows.
2. **Distributed bundles must exclude the GPL-only libraries and Qt's GPL tools.** Packaging
   configuration lists the exclusions explicitly and the packaging build fails if one of them
   appears in the bundle. Shipping a GPL-only library next to the product is the risk ADR 0006
   exists to prevent, whether or not the product loads it.
3. **A runtime guard:** `tests/test_qt_gpl_modules.py` opens the real main window in a separate
   interpreter and fails if the process has loaded any GPL-only Qt library. It runs in the
   macOS app job.
4. **No QML in the shell.** `PySide6.QtQml` and `PySide6.QtQuick` are banned in ruff. Both are
   LGPL themselves, but QML is how Qt Quick Timeline and Lottie would get loaded at runtime,
   and the shell has no use for them (ADR 0004 chose widgets).

## Consequences

- Packaging inherits a concrete checklist item and a build-time check, rather than a comment.
- If the shell ever needs QML, this ADR has to be revisited and superseded: the ban would have
  to go, and the bundle would then need the GPL-only plugins kept out by other means.
- The guard test is macOS-only, because it reads the loaded libraries through dyld. That
  matches where the app is developed and packaged first (ADR 0004).
