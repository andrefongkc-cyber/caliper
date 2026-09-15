# ADR 0004: Desktop shell is PySide6, Mac-first

- **Status:** Accepted
- **Date:** 2026-09-14
- **Code:** `caliper/app/` (Stream B)
- **Related:** ADR 0006 (Qt module restrictions)

## Context

The shell is where engineers spend their time. It needs to feel like serious CAD software on
macOS first, and a Windows build should be possible later without touching the engine. The
engine is Python, so a shell in another language puts a process boundary between UI and
engine.

## Decision

- **PySide6 (Qt for Python)**, installed as **`pyside6-essentials`** through the `app` extra.
  PySide6 is LGPL-3 when dynamically linked. The essentials package leaves out
  `pyside6-addons`, where the GPL-only Qt modules live (ADR 0006).
- **The V1 viewport is a custom `QWidget` painted with `QPainter`,** not `QGraphicsView` and
  not QML. It gives full control over the grid, snapping, and drawing. Hit-testing and
  snapping ask the engine's query API, so the shell holds no geometry logic of its own.
  Revisit at V2 for 3D.
- **Mac-first.** Build and test on macOS, dark theme first, compact chrome, a
  canvas-dominant layout, and CAD conventions familiar from SolidWorks and Fusion. Draw
  Retina-sharp by honoring the device pixel ratio.
- **The model uses Y-up millimetres;** Qt uses Y-down pixels. The viewport owns that
  transform, and nothing outside `caliper/app/viewport/` sees pixel coordinates.
- **The shell holds no document state.** It sends Commands to the bus, reads the immutable
  `bus.document`, and repaints from `Change` notifications. Selection, hover, and previews
  live only in the shell.
- **OS-specific code stays in `caliper/app/`,** behind small interfaces. Windows support
  later should touch only this directory, and the engine is already tested on Linux in CI.
- **UI tests** use pytest-qt with `QT_QPA_PLATFORM=offscreen` and live in `tests/app/`.

## Consequences

- One language across engine and shell; the shell calls the engine in-process.
- Qt widgets are mature for dense engineering panels: trees, property grids, docks.
- Shipping a signed macOS `.app` (PyInstaller or Briefcase) is future work. LGPL compliance
  means keeping the Qt libraries as separate, replaceable dynamic libraries (ADR 0006).
- A 3D viewport at V2 can't use Qt Quick 3D, which is GPL-only. The options that remain
  are OCCT's own viewer, VTK, or a custom renderer (ADR 0006).

## Alternatives considered

- **Electron or Tauri with a web UI:** great UI tooling, but it means two languages and IPC
  between the UI and a Python engine, and it feels less like native CAD.
- **SwiftUI / AppKit:** the best Mac feel, but a second language and a separate Windows
  rewrite later.
- **PyQt6:** same Qt, but GPL or commercial. Banned by ADR 0006.
- **Dear ImGui:** fast to build, but doesn't feel native and scales poorly to dense
  document UIs.
- **`QGraphicsView`:** built-in scene graph and hit-testing, but those would duplicate what
  the engine's query API already owns, and it's harder to control at 120 Hz.
