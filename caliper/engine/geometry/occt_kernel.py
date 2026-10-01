"""OpenCascade Kernel (ADR 0001). The only module allowed to import OCP.

Needs the `occt` extra (cadquery-ocp). It builds real B-rep faces for what the provisional
Kernel protocol asks for: a planar face inside one loop of lines and arcs (or one Rectangle
or Circle), less any holes. Consecutive edges share one vertex, toleranced to the engine's
joining distance, so a loop closes even where its ends differ in the last bits. It is held to
the same conformance suite as FakeKernel (tests/engine/geometry/test_kernel_conformance.py).

Solids (V2's F2): a face is moved onto its sketch's frame and swept along the normal
(BRepPrimAPI_MakePrism), solids combine with OCCT's booleans, and a mesh comes from its
incremental mesher, each triangle turned to face out.

OCP ships without type information, so its names are Any here and every value leaving this
module is converted to a plain float first.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

# OCP has no type information, and the Linux core CI job doesn't install it.
from OCP import (  # type: ignore[import-not-found, import-untyped, unused-ignore]
    Bnd,
    BRep,
    BRepAlgoAPI,
    BRepBndLib,
    BRepBuilderAPI,
    BRepCheck,
    BRepGProp,
    BRepMesh,
    BRepPrimAPI,
    GProp,
    ShapeFix,
    TopAbs,
    TopExp,
    TopLoc,
    TopoDS,
    gp,
)

from caliper.contracts.document import Point2
from caliper.contracts.errors import ErrorCode
from caliper.contracts.kernel import Frame, KernelError, Loop, Shape
from caliper.contracts.queries import AreaProperties, BoundingBox, BoundingBox3, Mesh, Point3
from caliper.engine import profiles

if TYPE_CHECKING:
    from caliper.contracts.kernel import Kernel

PRECISION = 1e-7
"""mm: OCCT's own confusion distance (Precision::Confusion), the least vertex tolerance."""


@dataclass(frozen=True, slots=True, eq=False)
class OCCTFace:
    """An OCCT TopoDS_Face, wrapped so other kernels' shapes are refused."""

    face: Any


@dataclass(frozen=True, slots=True, eq=False)
class OCCTSolid:
    """An OCCT shape holding solids: one, several, or none (a cut that left nothing)."""

    shape: Any


class OCCTKernel:
    def make_face(self, outer: Loop, holes: Sequence[Loop] = ()) -> Shape:
        loops = (outer, *holes)
        for loop in loops:
            if (problem := profiles.loop_problem(loop)) is not None:
                raise KernelError(problem.code, problem.message)
        join = max(PRECISION, profiles.joining([e for loop in loops for e in loop.edges]))
        wires = [_wire(loop, join) for loop in loops]
        maker = BRepBuilderAPI.BRepBuilderAPI_MakeFace(wires[0], True)
        if not maker.IsDone():
            raise KernelError(ErrorCode.GEOMETRY_DEGENERATE, "OCCT could not build a face")
        for wire in wires[1:]:
            maker.Add(wire)
        # Each hole must run the other way round from the outline; this sets them so.
        fix = ShapeFix.ShapeFix_Face(maker.Face())
        fix.FixOrientation()
        face = fix.Face()
        if not BRepCheck.BRepCheck_Analyzer(face).IsValid():
            raise KernelError(ErrorCode.GEOMETRY_DEGENERATE, "OCCT built a face that isn't valid")
        return OCCTFace(face=face)

    def area_properties(self, face: Shape) -> AreaProperties:
        props = GProp.GProp_GProps()
        BRepGProp.BRepGProp.SurfaceProperties_s(_own(face).face, props)
        centroid = props.CentreOfMass()
        # OCCT's matrix is about the centroid, with products of inertia stored negated.
        inertia = props.MatrixOfInertia()
        return AreaProperties(
            area=float(props.Mass()),
            centroid=Point2(x=float(centroid.X()), y=float(centroid.Y())),
            ixx=float(inertia.Value(1, 1)),
            iyy=float(inertia.Value(2, 2)),
            ixy=-float(inertia.Value(1, 2)),
        )

    def bounding_box(self, shape: Shape) -> BoundingBox:
        box = Bnd.Bnd_Box()
        BRepBndLib.BRepBndLib.AddOptimal_s(_own(shape).face, box, False, False)
        low, high = box.CornerMin(), box.CornerMax()
        return BoundingBox(
            x_min=float(low.X()), y_min=float(low.Y()), x_max=float(high.X()), y_max=float(high.Y())
        )

    def is_valid(self, shape: Shape) -> bool:
        if isinstance(shape, OCCTFace):
            return bool(BRepCheck.BRepCheck_Analyzer(shape.face).IsValid())
        return isinstance(shape, OCCTSolid) and bool(
            BRepCheck.BRepCheck_Analyzer(shape.shape).IsValid()
        )

    # --- Solids -------------------------------------------------------------------------

    def extrude(self, face: Shape, frame: Frame, depth: float) -> Shape:
        if not depth > 0:
            raise KernelError(ErrorCode.VALUE_NOT_POSITIVE, "depth must be greater than 0")
        placed = BRepBuilderAPI.BRepBuilderAPI_Transform(_own(face).face, _placing(frame), True)
        n = _normal(frame)
        prism = BRepPrimAPI.BRepPrimAPI_MakePrism(
            placed.Shape(), gp.gp_Vec(n.x * depth, n.y * depth, n.z * depth)
        )
        if not prism.IsDone():
            raise KernelError(ErrorCode.GEOMETRY_DEGENERATE, "OCCT could not extrude the face")
        return OCCTSolid(shape=prism.Shape())

    def union(self, a: Shape, b: Shape) -> Shape:
        return _boolean(BRepAlgoAPI.BRepAlgoAPI_Fuse(_solid(a).shape, _solid(b).shape), "join")

    def cut(self, a: Shape, b: Shape) -> Shape:
        return _boolean(BRepAlgoAPI.BRepAlgoAPI_Cut(_solid(a).shape, _solid(b).shape), "cut")

    def volume(self, solid: Shape) -> float:
        props = GProp.GProp_GProps()
        BRepGProp.BRepGProp.VolumeProperties_s(_solid(solid).shape, props)
        return float(props.Mass())

    def bounding_box_3d(self, solid: Shape) -> BoundingBox3:
        box = Bnd.Bnd_Box()
        BRepBndLib.BRepBndLib.AddOptimal_s(_solid(solid).shape, box, False, False)
        if box.IsVoid():
            raise KernelError(ErrorCode.SELECTION_EMPTY, "the solid is nothing: it has no bounds")
        low, high = box.CornerMin(), box.CornerMax()
        return BoundingBox3(
            x_min=float(low.X()),
            y_min=float(low.Y()),
            z_min=float(low.Z()),
            x_max=float(high.X()),
            y_max=float(high.Y()),
            z_max=float(high.Z()),
        )

    def mesh(self, solid: Shape, tolerance: float) -> Mesh:
        shape = _solid(solid).shape
        BRepMesh.BRepMesh_IncrementalMesh(shape, tolerance, False, 0.5, True)
        vertices: list[Point3] = []
        triangles: list[tuple[int, int, int]] = []
        faces = TopExp.TopExp_Explorer(shape, TopAbs.TopAbs_FACE)
        while faces.More():
            face = TopoDS.TopoDS.Face(faces.Current())
            location = TopLoc.TopLoc_Location()
            made = BRep.BRep_Tool.Triangulation_s(face, location)
            if made is not None:
                placing = location.Transformation()
                start = len(vertices)
                for i in range(1, made.NbNodes() + 1):
                    p = made.Node(i).Transformed(placing)
                    vertices.append(Point3(x=float(p.X()), y=float(p.Y()), z=float(p.Z())))
                # A face used the other way round has its triangles the other way round too.
                flip = face.Orientation() == TopAbs.TopAbs_REVERSED
                for i in range(1, made.NbTriangles() + 1):
                    a, b, c = made.Triangle(i).Get()
                    if flip:
                        b, c = c, b
                    triangles.append((start + a - 1, start + b - 1, start + c - 1))
            faces.Next()
        return Mesh(vertices=tuple(vertices), triangles=tuple(triangles))


def _boolean(made: Any, what: str) -> OCCTSolid:
    if not made.IsDone():
        raise KernelError(ErrorCode.GEOMETRY_DEGENERATE, f"OCCT could not {what} the solids")
    return OCCTSolid(shape=made.Shape())


def _normal(frame: Frame) -> Point3:
    x, y = frame.x, frame.y
    return Point3(x=x.y * y.z - x.z * y.y, y=x.z * y.x - x.x * y.z, z=x.x * y.y - x.y * y.x)


def _placing(frame: Frame) -> Any:
    """The move from the XY plane, where faces are built, onto `frame`."""
    o, x, n = frame.origin, frame.x, _normal(frame)
    target = gp.gp_Ax3(gp.gp_Pnt(o.x, o.y, o.z), gp.gp_Dir(n.x, n.y, n.z), gp.gp_Dir(x.x, x.y, x.z))
    move = gp.gp_Trsf()
    move.SetTransformation(target, gp.gp_Ax3())
    return move


def _wire(loop: Loop, join: float) -> Any:
    """`loop` as an OCCT wire, each joint one vertex shared by the edges meeting there."""
    walked = profiles.traversed(loop)
    maker = BRepBuilderAPI.BRepBuilderAPI_MakeWire()
    if len(walked) == 1:  # a whole circle
        maker.Add(_edge(BRepBuilderAPI.BRepBuilderAPI_MakeEdge(_circle(walked[0]))))
    else:
        joints = [_vertex(e.a, join) for e in walked]  # each edge starts where the last ends
        for n, e in enumerate(walked):
            first, last = joints[n], joints[(n + 1) % len(walked)]
            if e.center is None:
                edge = _edge(BRepBuilderAPI.BRepBuilderAPI_MakeEdge(first, last))
            elif e.sweep > 0:
                edge = _edge(BRepBuilderAPI.BRepBuilderAPI_MakeEdge(_circle(e), first, last))
            else:
                # OCCT's circle runs counter-clockwise, so the edge is made from where this
                # one ends, then turned round to run the way the wire does.
                made = BRepBuilderAPI.BRepBuilderAPI_MakeEdge(_circle(e), last, first)
                edge = TopoDS.TopoDS.Edge(_edge(made).Reversed())
            maker.Add(edge)
    if not maker.IsDone():
        raise KernelError(ErrorCode.PROFILE_NOT_CLOSED, "OCCT could not join the loop's edges")
    return maker.Wire()


def _edge(made: Any) -> Any:
    if not made.IsDone():
        raise KernelError(ErrorCode.GEOMETRY_DEGENERATE, "OCCT could not build an edge")
    return made.Edge()


def _circle(edge: profiles.Edge) -> Any:
    assert edge.center is not None
    axis = gp.gp_Ax2(gp.gp_Pnt(edge.center.x, edge.center.y, 0.0), gp.gp_Dir(0.0, 0.0, 1.0))
    return gp.gp_Circ(axis, edge.radius)


def _vertex(at: Point2, tolerance: float) -> Any:
    vertex = TopoDS.TopoDS_Vertex()
    BRep.BRep_Builder().MakeVertex(vertex, gp.gp_Pnt(at.x, at.y, 0.0), tolerance)
    return vertex


def _own(shape: Shape) -> OCCTFace:
    if not isinstance(shape, OCCTFace):
        raise TypeError(f"shape {shape!r} isn't a face made by OCCTKernel")
    return shape


def _solid(shape: Shape) -> OCCTSolid:
    if not isinstance(shape, OCCTSolid):
        raise TypeError(f"shape {shape!r} isn't a solid made by OCCTKernel")
    return shape


if TYPE_CHECKING:
    _conforms: Kernel = OCCTKernel()
