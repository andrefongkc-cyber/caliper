"""OpenCascade Kernel (ADR 0001). The only module allowed to import OCP.

Needs the `occt` extra (cadquery-ocp). It builds real B-rep faces for what the provisional
Kernel protocol asks of V1: a planar face bounded by one Rectangle or Circle. It is held to
the same conformance suite as FakeKernel (tests/engine/geometry/test_kernel_conformance.py).

OCP ships without type information, so its names are Any here and every value leaving this
module is converted to a plain float first.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, assert_never

# OCP has no type information, and the Linux core CI job doesn't install it.
from OCP import (  # type: ignore[import-not-found, import-untyped, unused-ignore]
    Bnd,
    BRepBndLib,
    BRepBuilderAPI,
    BRepCheck,
    BRepGProp,
    GProp,
    gp,
)

from caliper.contracts.document import Circle, Geometry, Point2, Rectangle
from caliper.contracts.errors import ErrorCode
from caliper.contracts.kernel import KernelError, Shape
from caliper.contracts.queries import AreaProperties, BoundingBox

if TYPE_CHECKING:
    from caliper.contracts.kernel import Kernel


@dataclass(frozen=True, slots=True, eq=False)
class OCCTFace:
    """An OCCT TopoDS_Face, wrapped so other kernels' shapes are refused."""

    face: Any


class OCCTKernel:
    def make_face(self, boundary: Sequence[Geometry]) -> Shape:
        if len(boundary) != 1 or not isinstance(boundary[0], Rectangle | Circle):
            raise KernelError(
                ErrorCode.PROFILE_NOT_CLOSED,
                "a face needs exactly one Rectangle or Circle as its boundary",
            )
        profile = boundary[0]
        match profile:
            case Rectangle(corner=corner, width=width, height=height):
                _require_valid(profile, (corner.x, corner.y, width, height), (width, height))
                x0, y0, x1, y1 = corner.x, corner.y, corner.x + width, corner.y + height
                polygon = BRepBuilderAPI.BRepBuilderAPI_MakePolygon(
                    gp.gp_Pnt(x0, y0, 0.0),
                    gp.gp_Pnt(x1, y0, 0.0),
                    gp.gp_Pnt(x1, y1, 0.0),
                    gp.gp_Pnt(x0, y1, 0.0),
                    True,
                )
                wire = polygon.Wire() if polygon.IsDone() else None
            case Circle(center=center, radius=radius):
                _require_valid(profile, (center.x, center.y, radius), (radius,))
                axis = gp.gp_Ax2(gp.gp_Pnt(center.x, center.y, 0.0), gp.gp_Dir(0.0, 0.0, 1.0))
                edge = BRepBuilderAPI.BRepBuilderAPI_MakeEdge(gp.gp_Circ(axis, radius))
                wire = (
                    BRepBuilderAPI.BRepBuilderAPI_MakeWire(edge.Edge()).Wire()
                    if edge.IsDone()
                    else None
                )
            case _:
                assert_never(profile)
        face = BRepBuilderAPI.BRepBuilderAPI_MakeFace(wire, True) if wire is not None else None
        if face is None or not face.IsDone():
            raise KernelError(
                ErrorCode.GEOMETRY_DEGENERATE, f"OCCT could not build a face: {profile}"
            )
        return OCCTFace(face=face.Face())

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
        return isinstance(shape, OCCTFace) and bool(
            BRepCheck.BRepCheck_Analyzer(shape.face).IsValid()
        )


def _require_valid(profile: Geometry, numbers: tuple[float, ...], sizes: tuple[float, ...]) -> None:
    if not all(math.isfinite(n) for n in numbers) or not all(s > 0 for s in sizes):
        raise KernelError(ErrorCode.GEOMETRY_DEGENERATE, f"degenerate profile: {profile}")


def _own(shape: Shape) -> OCCTFace:
    if not isinstance(shape, OCCTFace):
        raise TypeError(f"shape {shape!r} was not made by OCCTKernel")
    return shape


if TYPE_CHECKING:
    _conforms: Kernel = OCCTKernel()
