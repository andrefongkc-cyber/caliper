"""The persistent-naming spike (V2's F4, ADR 0014): which names for an extruded part's faces and
edges survive a rebuild, tested against OCCT.

A later feature that refers to generated topology (a sketch on a face, a fillet on an edge)
has to find the same face after the part is rebuilt with other numbers. Two ways to name a
face are tried on a 120 x 50 plate extruded 10 mm, rebuilt after each change the milestone
and its neighbours make:

- by position: the order OCCT lists the faces in;
- by history: what made the face. The prism's start and end caps, and the side swept by
  each sketch edge (`BRepPrimAPI_MakePrism.Generated`), named by the sketch entity and, for a
  rectangle, which side. A boolean carries names through its own history
  (`Modified`, `IsDeleted`).

Edges are named by the faces they join. These tests import OCP directly, as a spike: nothing
in `caliper/` names topology yet, and invariant 7 is about the package, not its tests.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

OCP = pytest.importorskip("OCP", reason="the occt extra isn't installed")

from OCP import (  # noqa: E402  (after the skip)
    BRepAlgoAPI,
    BRepBuilderAPI,
    BRepGProp,
    BRepPrimAPI,
    BRepTools,
    GProp,
    TopAbs,
    TopExp,
    TopoDS,
    gp,
)

type Signature = tuple[float, float, float, float]
"""A face's centre (x, y, z) and area, or an edge's centre and length, rounded: what it is,
whatever it's called."""


@dataclass
class Named:
    shape: Any
    faces: dict[str, Any]
    """Each name and the face it names now."""


# --- Building, with names from history ------------------------------------------------------


def _line(p: tuple[float, float], q: tuple[float, float]) -> Any:
    return BRepBuilderAPI.BRepBuilderAPI_MakeEdge(gp.gp_Pnt(*p, 0.0), gp.gp_Pnt(*q, 0.0)).Edge()


def _circle(center: tuple[float, float], radius: float) -> Any:
    axis = gp.gp_Ax2(gp.gp_Pnt(*center, 0.0), gp.gp_Dir(0.0, 0.0, 1.0))
    return BRepBuilderAPI.BRepBuilderAPI_MakeEdge(gp.gp_Circ(axis, radius)).Edge()


def _wire(edges: list[Any]) -> Any:
    made = BRepBuilderAPI.BRepBuilderAPI_MakeWire()
    for edge in edges:
        made.Add(edge)
    return made.Wire()


def _wire_edges(face: Any) -> list[Any]:
    """The face's own edges, wire by wire, in the order each runs. OCCT copies edges when it
    joins a wire, so history must be asked about these, not the edges first made."""
    found = []
    wires = TopExp.TopExp_Explorer(face, TopAbs.TopAbs_WIRE)
    while wires.More():
        walk = BRepTools.BRepTools_WireExplorer(TopoDS.TopoDS.Wire(wires.Current()))
        while walk.More():
            found.append(walk.Current())
            walk.Next()
        wires.Next()
    return found


SIDES = ("bottom", "right", "top", "left")


def plate(
    width: float = 120.0,
    height: float = 50.0,
    depth: float = 10.0,
    *,
    hole: tuple[tuple[float, float], float] | None = None,
    start: int = 0,
    at: tuple[float, float, float] = (0.0, 0.0, 0.0),
    entity: str = "e1",
) -> Named:
    """Rectangle `entity` (corner at the origin), drawn from side `start` on, with an optional
    round hole `e9`, extruded `depth`, then moved to `at`. Faces named by history."""
    corners = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    order = [(start + k) % 4 for k in range(4)]
    sources = [f"{entity}.{SIDES[k]}" for k in order]
    made = BRepBuilderAPI.BRepBuilderAPI_MakeFace(
        _wire([_line(corners[k], corners[(k + 1) % 4]) for k in order]), True
    )
    if hole is not None:
        ring = _wire([_circle(*hole)])
        ring.Reverse()
        made.Add(TopoDS.TopoDS.Wire(ring))
        sources.append("e9")
    face = made.Face()
    prism = BRepPrimAPI.BRepPrimAPI_MakePrism(face, gp.gp_Vec(0.0, 0.0, depth))
    faces: dict[str, Any] = {"start": prism.FirstShape(), "end": prism.LastShape()}
    for source, edge in zip(sources, _wire_edges(face), strict=True):
        (side,) = list(prism.Generated(edge))
        faces[f"side {source}"] = side
    shape = prism.Shape()
    if at != (0.0, 0.0, 0.0):
        move = gp.gp_Trsf()
        move.SetTranslation(gp.gp_Vec(*at))
        moved = BRepBuilderAPI.BRepBuilderAPI_Transform(shape, move, False)
        return Named(moved.Shape(), {n: moved.ModifiedShape(f) for n, f in faces.items()})
    return Named(shape, faces)


def through(boolean: Any, before: Named) -> dict[str, list[Any] | str]:
    """Where each name went through a boolean: the faces it names now, or "deleted"."""
    result = list(_faces(boolean.Shape()))
    found: dict[str, list[Any] | str] = {}
    for name, face in before.faces.items():
        if boolean.IsDeleted(face):
            found[name] = "deleted"
            continue
        modified = list(boolean.Modified(face))
        found[name] = modified or [f for f in result if f.IsSame(face)]
    return found


# --- Looking at what's there ---------------------------------------------------------------


def _faces(shape: Any) -> Iterator[Any]:
    walk = TopExp.TopExp_Explorer(shape, TopAbs.TopAbs_FACE)
    while walk.More():
        yield walk.Current()
        walk.Next()


def face_signature(face: Any) -> Signature:
    props = GProp.GProp_GProps()
    BRepGProp.BRepGProp.SurfaceProperties_s(face, props)
    c = props.CentreOfMass()
    return (round(c.X(), 6), round(c.Y(), 6), round(c.Z(), 6), round(props.Mass(), 6))


def by_position(shape: Any) -> list[Signature]:
    return [face_signature(f) for f in _faces(shape)]


def by_name(named: Named) -> dict[str, Signature]:
    return {name: face_signature(face) for name, face in named.faces.items()}


def edge_names(named: Named) -> dict[str, Signature]:
    """Each edge, named by the faces it joins: "end|side e1.right" is the top of the right side.
    An edge a face meets itself along (a cylinder's seam) is "side e9|side e9"."""
    groups: list[tuple[Any, list[str]]] = []
    for name, face in named.faces.items():
        walk = TopExp.TopExp_Explorer(face, TopAbs.TopAbs_EDGE)
        while walk.More():
            edge = walk.Current()
            for known, names in groups:
                if known.IsSame(edge):
                    names.append(name)
                    break
            else:
                groups.append((edge, [name]))
            walk.Next()
    found = {}
    for edge, names in groups:
        props = GProp.GProp_GProps()
        BRepGProp.BRepGProp.LinearProperties_s(edge, props)
        c = props.CentreOfMass()
        key = "|".join(sorted(names))
        found[key] = (round(c.X(), 6), round(c.Y(), 6), round(c.Z(), 6), round(props.Mass(), 6))
    return found


def expected(width: float, height: float, depth: float) -> dict[str, Signature]:
    """What each named face of a plate is, by formula."""
    w, h, d = width, height, depth
    return {
        "start": (w / 2, h / 2, 0.0, w * h),
        "end": (w / 2, h / 2, d, w * h),
        "side e1.bottom": (w / 2, 0.0, d / 2, w * d),
        "side e1.right": (w, h / 2, d / 2, h * d),
        "side e1.top": (w / 2, h, d / 2, w * d),
        "side e1.left": (0.0, h / 2, d / 2, h * d),
    }


# --- By position: not stable --------------------------------------------------------------


def test_names_by_position_move_when_a_hole_is_added_or_the_outline_starts_elsewhere() -> None:
    base = by_position(plate().shape)
    holed = by_position(plate(hole=((60.0, 25.0), 10.0)).shape)
    elsewhere = by_position(plate(start=2).shape)
    assert base[4] == (60.0, 25.0, 0.0, 6000.0)  # "face 4" is the start cap ...
    assert holed[4][2] == 5.0  # ... until a hole comes first: now it's the hole's wall
    assert base[0][1] == 0.0  # "face 0" is the bottom side ...
    assert elsewhere[0][1] == 50.0  # ... drawn from the top, it's the top side
    assert by_position(plate(width=140.0).shape)[1][0] == 140.0  # a size alone keeps the order


# --- By history: stable through every rebuild tried -----------------------------------------


@pytest.mark.parametrize(
    ("width", "height", "depth"),
    [(120.0, 50.0, 10.0), (140.0, 50.0, 10.0), (120.0, 80.0, 10.0), (120.0, 50.0, 25.0)],
)
def test_names_by_history_follow_their_faces_through_dimension_and_depth_changes(
    width: float, height: float, depth: float
) -> None:
    assert by_name(plate(width, height, depth)) == pytest.approx(expected(width, height, depth))


def test_names_by_history_ignore_where_the_outline_starts() -> None:
    assert by_name(plate(start=2)) == pytest.approx(by_name(plate()))
    assert by_name(plate(start=3)) == pytest.approx(by_name(plate()))


def test_adding_geometry_adds_a_name_and_moves_none_and_removing_it_takes_only_its_name() -> None:
    holed = by_name(plate(hole=((60.0, 25.0), 10.0)))
    plain = by_name(plate())
    assert set(holed) - set(plain) == {"side e9"}  # the hole's wall: new, and new name
    for name in ("side e1.bottom", "side e1.right", "side e1.top", "side e1.left"):
        assert holed[name] == plain[name]
    assert holed["start"][:3] == plain["start"][:3]  # the same cap, less the hole's area
    # Removed again, a reference to the hole's wall finds nothing: lost, never re-pointed.
    assert "side e9" not in plain


def test_rebuilding_the_same_feature_gives_the_same_names_on_the_same_faces() -> None:
    first, second = plate(hole=((60.0, 25.0), 10.0)), plate(hole=((60.0, 25.0), 10.0))
    assert by_name(first) == by_name(second)
    assert edge_names(first) == edge_names(second)


def test_edges_named_by_the_faces_they_join_follow_a_width_change() -> None:
    narrow, wide = edge_names(plate()), edge_names(plate(width=140.0))
    assert set(narrow) == set(wide)
    assert len(narrow) == 12  # a box's edges, each joining two different faces
    assert narrow["end|side e1.right"] == (120.0, 25.0, 10.0, 50.0)
    assert wide["end|side e1.right"] == (140.0, 25.0, 10.0, 50.0)  # still the top right edge
    holed = edge_names(plate(hole=((60.0, 25.0), 10.0)))
    assert set(holed) - set(narrow) == {"end|side e9", "side e9|start", "side e9|side e9"}


# --- Through booleans ---------------------------------------------------------------------


def test_a_boss_apart_leaves_every_name_on_its_face() -> None:
    part = plate()
    boss = plate(20.0, 20.0, 10.0, at=(200.0, 0.0, 0.0), entity="e5")
    fused = BRepAlgoAPI.BRepAlgoAPI_Fuse(part.shape, boss.shape)
    assert fused.IsDone()
    after = through(fused, part)
    assert all(len(faces) == 1 for faces in after.values())
    assert {n: face_signature(f[0]) for n, f in after.items()} == by_name(part)  # type: ignore[index]


def test_a_through_hole_carries_every_name_one_to_one() -> None:
    """The caps come back as one face each, holed; the hole's walls are the tool's own sides,
    so a reference to "the hole's left wall" is the tool's name for it."""
    part = plate()
    tool = plate(10.0, 10.0, 10.0, at=(55.0, 20.0, 0.0), entity="e7")
    cut = BRepAlgoAPI.BRepAlgoAPI_Cut(part.shape, tool.shape)
    assert cut.IsDone()
    after = through(cut, part)
    assert all(isinstance(f, list) and len(f) == 1 for f in after.values())
    assert face_signature(after["end"][0])[3] == pytest.approx(5_900.0)  # type: ignore[index]
    walls = through(cut, tool)
    assert (walls["start"], walls["end"]) == ("deleted", "deleted")  # the tool's caps
    assert face_signature(walls["side e7.left"][0])[:2] == (55.0, 25.0)  # type: ignore[index]


def test_a_shared_side_is_deleted_and_the_rest_carried() -> None:
    part = plate()
    boss = plate(20.0, 50.0, 10.0, at=(120.0, 0.0, 0.0), entity="e5")
    after = through(BRepAlgoAPI.BRepAlgoAPI_Fuse(part.shape, boss.shape), part)
    assert after["side e1.right"] == "deleted"  # now inside the solid
    assert all(len(f) == 1 for n, f in after.items() if n != "side e1.right")


def test_a_cut_across_a_face_splits_one_name_into_two_faces() -> None:
    """The limit of naming by history: a slot cut right across the top leaves two top faces.
    History says both came from "end"; which one a reference meant, it can't say."""
    part = plate()
    slot = plate(10.0, 70.0, 4.0, at=(55.0, -10.0, 6.0), entity="e8")
    after = through(BRepAlgoAPI.BRepAlgoAPI_Cut(part.shape, slot.shape), part)
    ends = after["end"]
    assert isinstance(ends, list)
    assert len(ends) == 2
    assert sorted(face_signature(f)[0] for f in ends) == [27.5, 92.5]  # left and right of it
    assert len(after["start"]) == 1  # untouched below
