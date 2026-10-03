"""The 3D view's camera (V2's F5, ADR 0012): where the part is seen from, as plain maths with
no Qt, so it is tested anywhere.

Orthographic, as CAD views are: sizes on screen don't shrink with distance, so a 10 mm edge
reads 10 mm wherever it is. Z is up. The camera looks at `target` from a direction given by
`yaw` (degrees about Z, from +X) and `pitch` (degrees above the XY plane); `scale` is pixels
per mm. It is UI state, never in the document.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

from caliper.contracts.queries import BoundingBox3, Point3

ISOMETRIC_PITCH = math.degrees(math.atan(1 / math.sqrt(2)))
"""35.26°: looking down a cube's diagonal."""
PITCH_LIMIT = 89.0
"""Never straight down or up, where yaw would stop meaning anything."""
FIT_MARGIN = 0.8
"""How much of the view a fitted part fills."""


@dataclass(frozen=True, slots=True)
class Projected:
    x: float
    y: float
    depth: float
    """Larger is nearer the viewer."""


ORIGIN = Point3(x=0.0, y=0.0, z=0.0)


@dataclass(frozen=True, slots=True)
class Camera:
    target: Point3 = ORIGIN
    yaw: float = -45.0
    """From +X towards the eye, about Z: -45 looks in from the front right."""
    pitch: float = ISOMETRIC_PITCH
    scale: float = 4.0
    """Pixels per mm."""

    def axes(self) -> tuple[Point3, Point3, Point3]:
        """The screen's right and up, and the way back towards the eye, in the part's space."""
        yaw, pitch = math.radians(self.yaw), math.radians(self.pitch)
        back = Point3(
            x=math.cos(pitch) * math.cos(yaw),
            y=math.cos(pitch) * math.sin(yaw),
            z=math.sin(pitch),
        )
        right = Point3(x=-math.sin(yaw), y=math.cos(yaw), z=0.0)
        up = _cross(back, right)
        return right, up, back

    def project(self, p: Point3, width: float, height: float) -> Projected:
        x, y, depth = self.projector(width, height)(p)
        return Projected(x=x, y=y, depth=depth)

    def projector(self, width: float, height: float) -> Callable[[Point3], tuple[float, ...]]:
        """`project` for a frame's many points, as (x, y, depth): the axes worked out once,
        not for every point (Performance V2.2, Perf-8), and each point's sums the same."""
        (rx, ry, rz), (ux, uy, uz), (bx, by, bz) = ((a.x, a.y, a.z) for a in self.axes())
        tx, ty, tz, scale = self.target.x, self.target.y, self.target.z, self.scale
        cx, cy = width / 2, height / 2

        def project(p: Point3) -> tuple[float, ...]:
            dx, dy, dz = p.x - tx, p.y - ty, p.z - tz
            return (
                cx + (dx * rx + dy * ry + dz * rz) * scale,
                cy - (dx * ux + dy * uy + dz * uz) * scale,
                dx * bx + dy * by + dz * bz,
            )

        return project

    def orbited(self, dx: float, dy: float) -> "Camera":
        """Turned by a drag of (dx, dy) pixels: across turns about Z, up and down tilts."""
        pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, self.pitch + dy * 0.4))
        return replace(self, yaw=(self.yaw - dx * 0.4) % 360.0, pitch=pitch)

    def panned(self, dx: float, dy: float) -> "Camera":
        """Slid by a drag of (dx, dy) pixels: what was under the pointer stays under it."""
        right, up, _ = self.axes()
        along, across = -dx / self.scale, dy / self.scale
        t = self.target
        return replace(
            self,
            target=Point3(
                x=t.x + along * right.x + across * up.x,
                y=t.y + along * right.y + across * up.y,
                z=t.z + along * right.z + across * up.z,
            ),
        )

    def zoomed(self, factor: float, at: tuple[float, float], size: tuple[float, float]) -> "Camera":
        """Scaled by `factor` about the pixel `at`, which stays where it is on screen."""
        scale = max(1e-6, min(1e6, self.scale * factor))
        width, height = size
        offset = (at[0] - width / 2, at[1] - height / 2)
        # The point under `at` was offset / scale from the target; keep it offset / new scale.
        shift = (
            offset[0] * (1 / self.scale - 1 / scale),
            -offset[1] * (1 / self.scale - 1 / scale),
        )
        right, up, _ = self.axes()
        t = self.target
        return replace(
            self,
            scale=scale,
            target=Point3(
                x=t.x + shift[0] * right.x + shift[1] * up.x,
                y=t.y + shift[0] * right.y + shift[1] * up.y,
                z=t.z + shift[0] * right.z + shift[1] * up.z,
            ),
        )

    def fitted(self, box: BoundingBox3, width: float, height: float) -> "Camera":
        """Looking at the middle of `box`, with all of it in view whichever way it turns."""
        center = Point3(
            x=(box.x_min + box.x_max) / 2,
            y=(box.y_min + box.y_max) / 2,
            z=(box.z_min + box.z_max) / 2,
        )
        diagonal = math.dist((box.x_min, box.y_min, box.z_min), (box.x_max, box.y_max, box.z_max))
        scale = FIT_MARGIN * min(width, height) / max(diagonal, 1e-6)
        return replace(self, target=center, scale=scale)


def _dot(a: Point3, b: Point3) -> float:
    return a.x * b.x + a.y * b.y + a.z * b.z


def _cross(a: Point3, b: Point3) -> Point3:
    return Point3(x=a.y * b.z - a.z * b.y, y=a.z * b.x - a.x * b.z, z=a.x * b.y - a.y * b.x)


def normal(a: Point3, b: Point3, c: Point3) -> Point3:
    """A triangle's unit normal, (b - a) x (c - a): outward for a mesh wound outward."""
    n = _cross(
        Point3(x=b.x - a.x, y=b.y - a.y, z=b.z - a.z), Point3(x=c.x - a.x, y=c.y - a.y, z=c.z - a.z)
    )
    length = math.sqrt(_dot(n, n))
    if length == 0:
        return Point3(x=0.0, y=0.0, z=0.0)
    return Point3(x=n.x / length, y=n.y / length, z=n.z / length)


def facing(n: Point3, camera: Camera) -> float:
    """How directly a face with normal `n` faces the viewer: 1 head on, 0 edge on, below 0
    facing away."""
    return _dot(n, camera.axes()[2])
