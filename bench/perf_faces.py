"""Benchmark cases for ADR 0016: sketching at an angle, picking a face, and parts built on
their own faces. `bench/perf.py` runs them with the rest (`--only 3d/`, `--only v2/face`).

- `3d/sketch-edit-tilted/holes-24`: a line drawn in a sketch on Front seen at an angle, with the
  24-hole part behind; against `3d/sketch-edit-over-part`, the same facing it.
- `3d/face-pick/holes-24`: a click on the part's top in the 3D view, named through
  `face_at`, and the picked face's triangles found for the tint.
- `v2/face-chain`: ten bosses, each on the one before's top; editing the first one's depth
  moves every sketch on top of it, so each boss is rebuilt; editing the last sketch rebuilds
  one.
"""

import time
from collections.abc import Callable

from perf_v22 import _dispose, _holes, _median_ms, _result, _window_with, counting

from caliper.contracts.commands import (
    CreateCircle,
    CreateExtrude,
    CreateLine,
    CreateRectangle,
    CreateSketch,
    ModifyEntity,
)
from caliper.contracts.document import FaceRef, Plane, Point2
from caliper.contracts.queries import Point3
from caliper.engine import features
from caliper.engine.commands.bus import Bus
from caliper.engine.geometry import default_kernel
from caliper.engine.geometry.fake_kernel import FakeKernel


def sketch_edit_tilted() -> object:
    """A line drawn in a sketch on Front, turned 25 degrees from it, the 24-hole part behind."""
    from perf import _window

    from caliper.app.viewport import scene3d

    features.forget()
    app, window = _window()
    window.set_mode("3d")  # type: ignore[attr-defined]
    session = window.session  # type: ignore[attr-defined]
    window.new_sketch(Plane.XY)  # type: ignore[attr-defined]
    session.execute(CreateRectangle(corner=Point2(x=0.0, y=0.0), width=240.0, height=160.0))
    for i in range(6):
        for j in range(4):
            session.execute(
                CreateCircle(center=Point2(x=20.0 + 40 * i, y=20.0 + 40 * j), radius=8.0)
            )
    window.finish_sketch()  # type: ignore[attr-defined]
    session.execute(CreateExtrude(depth=10.0))
    app.processEvents()  # type: ignore[attr-defined]
    window.new_sketch(Plane.XZ)  # type: ignore[attr-defined]
    canvas = window.canvas  # type: ignore[attr-defined]
    backdrop = canvas.backdrop
    backdrop.orbit(canvas.view, canvas.width(), canvas.height())
    backdrop.free = backdrop.free.orbited(40, -20)
    canvas.repaint()
    tilt = backdrop.tilt
    samples, runs = [], 8
    with counting(scene3d.Scene, "of") as scenes:
        for k in range(runs):
            started = time.perf_counter()
            session.execute(
                CreateLine(start=Point2(x=10.0 * k, y=20.0), end=Point2(x=10.0 * k + 5, y=40.0))
            )
            app.processEvents()  # type: ignore[attr-defined]
            canvas.repaint()
            samples.append(time.perf_counter() - started)
    drawable = float(backdrop.drawable)
    _dispose(window)
    return _result(
        "3d/sketch-edit-tilted/holes-24",
        {
            "edit_ms": _median_ms(samples),
            "scene_builds": scenes[0] / runs,
            "tilt": tilt,
            "drawable": drawable,
        },
    )


def face_pick() -> object:
    """Picking the 24-hole part's top: the click, and the picked face's triangles."""
    from caliper.app.viewport.camera3d import Camera

    features.forget()
    _, window = _window_with(_holes(6, 4), mode="3d")
    view = window.view3d  # type: ignore[attr-defined]
    view.refresh()
    w, h = view.width(), view.height()  # as the window lays it out
    view.camera = Camera().fitted(view.scene.box(), w, h)
    seen = view.camera.project(Point3(x=40.0, y=40.0, z=10.0), w, h)  # between the holes
    picks, tints = [], []
    picked = None
    for _ in range(9):
        started = time.perf_counter()
        picked = view.pick(seen.x, seen.y)
        picks.append(time.perf_counter() - started)
        view._tint = None
        started = time.perf_counter()
        tinted = view._tinted(picked)
        tints.append(time.perf_counter() - started)
    triangles = float(len(view.scene.mesh.triangles))
    _dispose(window)
    return _result(
        "3d/face-pick/holes-24",
        {
            "pick_ms": _median_ms(picks),
            "tint_ms": _median_ms(tints),
            "picked_a_face": float(isinstance(picked, FaceRef)),
            "tinted": float(len(tinted)),
            "triangles": triangles,
        },
    )


def face_chain() -> object:
    """Ten bosses, each a 20 x 20 square on the top of the one before, on a plate."""
    kernel = default_kernel() or FakeKernel()
    features.forget()
    bus = Bus(kernel=kernel)
    bus.execute(CreateRectangle(corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0))
    below = bus.execute(CreateExtrude(depth=10.0)).created_ids[0]  # type: ignore[union-attr]
    plate, last_square = below, None
    for k in range(10):
        on = bus.execute(CreateSketch(plane=FaceRef(feature=below, face="end"))).created_ids[0]  # type: ignore[union-attr]
        last_square = bus.execute(
            CreateRectangle(
                corner=Point2(x=10.0 + 2 * k, y=10.0 + k), width=20.0, height=20.0, sketch=on
            )
        ).created_ids[0]  # type: ignore[union-attr]
        below = bus.execute(CreateExtrude(depth=2.0, sketch=on)).created_ids[0]  # type: ignore[union-attr]
    bus.queries.solid_properties()

    def timed(command: object) -> tuple[float, int]:
        with counting(type(kernel), "extrude") as prisms:
            started = time.perf_counter()
            bus.execute(command)  # type: ignore[arg-type]
            found = bus.queries.solid_properties()
            took = time.perf_counter() - started
        assert not hasattr(found, "code"), found
        return took, prisms[0]

    first_ms, first_prisms = timed(ModifyEntity(id=plate, changes={"depth": 12.0}))
    last_ms, last_prisms = timed(ModifyEntity(id=last_square, changes={"width": 18.0}))
    return _result(
        "v2/face-chain",
        {
            "edit_first_ms": first_ms * 1e3,
            "edit_first_prisms": float(first_prisms),
            "edit_last_ms": last_ms * 1e3,
            "edit_last_prisms": float(last_prisms),
            "occt": float(default_kernel() is not None),
        },
    )


def cases() -> list[tuple[str, Callable[[], list[object]]]]:
    return [
        ("3d/sketch-edit-tilted", lambda: [sketch_edit_tilted()]),
        ("3d/face-pick", lambda: [face_pick()]),
        ("v2/face-chain", lambda: [face_chain()]),
    ]
