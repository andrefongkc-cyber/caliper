# Vision (V2–V10)

> **Not loaded into agent context by default.** This is where Caliper is headed, not what to
> build. Don't design for these versions and don't leave hooks for them. Each one gets its
> own ADRs when it becomes current work.

## Destination

An AI-native engineering platform that understands a project as a whole:

**Idea → Sketch → CAD → Simulation → Optimization → Manufacturing → Test → Redesign**

The long-run loop:

```text
User → AI agent → engineering tools → engineering core
     → CAD / simulation / electronics / manufacturing
     → optimization → physical testing → real-world data → AI
```

The AI works only through well-defined tools, never with uncontrolled access to internal
state.

Over time the engineering core needs structured representations of:

- geometry, dimensions, and constraints
- materials, parts, assemblies, and components
- simulation results, manufacturing methods, and costs
- tests and project history

The guiding question for every decision: **will this make Version 5 easier or harder?**

## Roadmap

| Version | Theme | Capabilities |
|---|---|---|
| V1 | AI sketching (current) | 2D canvas, lines/circles/arcs/rectangles, selection, move, delete, undo/redo, dimensions, save/load. V1.5: constraints |
| V2 | Parametric CAD | Driving dimensions, more constraints, extrude, cut, revolve, fillet, chamfer, 3D parts, assemblies |
| V3 | AI CAD | Text → CAD, image or sketch → CAD, natural-language modification, AI-generated parts and assemblies |
| V4 | Simulation | FEA: stress, strain, deflection, safety factor; thermal; eventually CFD |
| V5 | Generative design | The loop: design → simulate → evaluate → modify → repeat, optimizing strength, weight, stiffness, cost, material, and manufacturability |
| V6 | Manufacturing AI | CNC, 3D printing, laser cutting, sheet metal, DFM, toolpaths, feeds/speeds, time and cost estimation |
| V7 | Electronics / PCB | Schematics, component selection, layout, routing, electrical and thermal checks, debugging (inspiration: Flux) |
| V8 | Robotics | Mechanisms, motors, gearboxes, sensors, controls, software, and simulation together |
| V9 | Testing / inspection | CAD → simulation → manufacturing → physical test → data → AI → redesigned CAD |
| V10 | Full engineering AI | From a requirement ("lift 10 kg, fit this volume, safety factor > 2, CNC-machinable") to researched components, CAD, simulation, optimization, manufacturing checks, drawings, BOMs, and analysis of test results |

## How today's foundation points toward this

These are notes, not commitments.

- **Commands and queries as the only interface:** V3's AI and V5's optimizer drive the same
  bus the UI does. Batch transactions keep optimizer runs out of the undo stack.
- **Declarative, inputs-only files:** V2's feature tree is parameters plus regeneration.
  V5 edits those parameters.
- **Feature references instead of coordinates:** V1.5 constraints and V2 features attach to
  stable targets. Referencing generated B-rep faces (the topological naming problem) gets
  its own ADR at V2.
- **`Expectation` / `CheckResult`:** the seed of V4–V5's evaluate step and V9's
  simulation-versus-reality comparison.
- **No GPL:** keeps commercial distribution and acquisition open. It rules out Qt Quick 3D
  for the V2 viewport (ADR 0006).
