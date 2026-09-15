# Shell (Stream B)

Read `docs/workplan/shell.md` first. You own `caliper/app/` and `tests/app/`. Don't edit
`caliper/engine/`, `caliper/contracts/`, or other tests.

- **Build against `caliper.contracts` and the in-memory engine** (bus, document, queries). Never import a kernel, `caliper/engine/geometry/`, or `OCP`.
- **Every user action becomes a `Command` sent to the bus.** Read `bus.document` and repaint from `Change` notifications. If you want to reach into document state, the contract is missing something: stop and flag it to the user.
- **Selection, hover, rubber-band previews, and tool state live only in the shell.** A drag sends one command on release. Use `merge_key` for continuous edits.
- **Tool modes** (select, line, circle, rectangle, arc) are a state machine, not branching `if` chains.
- **Coordinates:** the model is Y-up millimetres, Qt is Y-down pixels. Only `app/viewport/` converts between them. Hit-testing and snapping go through queries with a tolerance in mm.
- **Design:** serious engineering software, dark first. The canvas dominates; chrome is compact; follow SolidWorks/Fusion conventions. Keep feedback subtle. No gradients, rounded cards, oversized buttons, decorative icons, or chat bubbles.
- **Qt modules:** only those in `pyside6-essentials`. GPL-only modules are banned (ADR 0006).
- **Tests:** pytest-qt in `tests/app/`, runs with `QT_QPA_PLATFORM=offscreen`. Install with `uv sync --extra app --group app-test`.
