"""The 3D view (V2's F5, ADR 0012, ADR 0015): the part, from its planes up.

It shows the part's origin and its Top, Front, and Right planes, its sketches on their
planes, and its solid (`scene3d`). It reads the session's queries (`Queries.mesh`,
`Queries.solid_properties`) and holds no model of its own. The mesh is asked for only when
the view is showing and the document has changed since; the engine keeps solids and meshes
by identity (ADR 0013), so asking again costs little.

While an agent's proposal waits, the solid drawn is the one the proposal would leave, in the
agent's colour (C-16): an extrude changes no geometry, so this is where it's seen before Accept.
While the Extrude form is open, it's the solid the form's fields would make, in the preview's
colour. Either is brought into view when it outgrows the part and the view.

What's under the pointer is drawn in the hover colour: it's what a click would pick. A selected
extrude shows as what it made. A click picks a plane, a face, or a sketch, which is what Sketch
and Extrude act on; a double-click on one asks to sketch on it or edit it; a right click that
doesn't move asks for its menu. A left drag orbits, a right or middle drag (or Shift with
a left drag) pans, and F fits. A trackpad moves it as it moves the canvas (`scrolled`): two
fingers pan, a pinch or ⌘ with two fingers zooms about the pointer, and Shift with two fingers
orbits; a mouse wheel zooms. The camera is UI state.
"""

import math
import time
from collections import deque
from collections.abc import Callable

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QInputDevice,
    QKeyEvent,
    QMouseEvent,
    QNativeGestureEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from caliper.app import theme
from caliper.app.agent.proposal import Proposal
from caliper.app.session import DocumentSession, Space
from caliper.app.tokens import SPACE
from caliper.app.viewport.camera3d import Camera
from caliper.app.viewport.scene3d import (
    FACE_TOLERANCE,
    Picked,
    Scene,
    facing_camera,
    mesh_box,
    on_plane,
)
from caliper.contracts.document import (
    Arc,
    Circle,
    Document,
    EntityId,
    Extrude,
    FaceRef,
    Plane,
    Sketch,
)
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.queries import BoundingBox3, Frame, Mesh, Point3, Queries
from caliper.engine.commands.bus import Bus

DRAG_PX = 3.0
TRIAD_ROOM = 72.0
"""Pixels of the view's bottom left corner the triad takes, each way."""
CAPTION_FLAGS = (
    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom | Qt.TextFlag.TextWordWrap
)
"""A press that moves less than this before its release is a click, not an orbit."""


class View3D(QWidget):
    open_requested = Signal(object)
    """A double-click on a plane (a `Plane`) or a sketch (its id): sketch on it, or edit it."""
    menu_requested = Signal(object, QPoint)
    """A right click that didn't move: what was under it (as `pick`), and where on screen."""

    def __init__(self, session: DocumentSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.camera = Camera()
        self.scene = Scene()
        self.mesh: Mesh | None = None
        """The solid's mesh, or the last good one while the part fails."""
        self.problem: str | None = None
        """Why there's no solid, or why the one shown is out of date."""
        self.proposal: Callable[[], Proposal | None] = lambda: None
        """The agent proposal to preview, if any; set by the main window."""
        self.note: str | None = None
        """What a pending proposal would do to the solid, said under the view."""
        self._proposal_stale = False
        """The proposal changed since the scene was made; the document didn't."""
        self.frame_ms: deque[float] = deque(maxlen=120)
        """How long the last frames took to draw, in milliseconds."""
        self._stale = True
        self._fit = True
        self._space = session.space
        self._drag: tuple[Qt.MouseButton, QPointF, bool] | None = None
        self._pressed: QPointF | None = None
        """Where a left press was, until it moves far enough to be an orbit."""
        self._clicked: QPointF | None = None
        """Where a right press was, until it moves far enough to be a pan."""
        self._tint: tuple[tuple[object, FaceRef], frozenset[int]] | None = None
        """The picked face's triangles, for the scene they were found in."""
        self._seed: tuple[Scene, FaceRef, int] | None = None
        """The triangle a click picked a face on, in the scene it was picked in."""
        self._made: tuple[Scene, frozenset[EntityId], frozenset[int]] | None = None
        """What the selected extrudes made, as triangles of the scene they were found in."""
        self.preview: Document | None = None
        """The part as the Extrude form's fields would leave it, to draw its solid; set by
        `show_preview`."""
        self._brought: BoundingBox3 | None = None
        """The proposed or previewed solid's box when the view last made sure of showing it."""
        self.hover: Picked = None
        """What's under the pointer, or under it in the Part panel: UI state, never selected
        by being here."""
        self._lit: frozenset[int] = frozenset()
        """The triangles of the solid drawn as hovered: a face's, or what an extrude made."""
        self._hover_at: QPointF | None = None
        self._hover_timer = QTimer(self)
        """Coalesces pointer moves: one pick for however many arrived since the last."""
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(0)
        self._hover_timer.timeout.connect(self._hover_now)
        self.show_planes = True
        """View → Show Planes: off hides the origin planes, and they can't be picked."""
        self.offering = False
        """Sketch is waiting for a plane to be picked: the planes show in full until one is."""
        self.setMouseTracking(True)
        self.setObjectName("view-3d")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(200, 150)
        session.document_changed.connect(self._changed)
        session.document_replaced.connect(self._replaced)
        session.selection_changed.connect(self._selection_changed)

    # --- What's drawn -------------------------------------------------------------------

    def _changed(self) -> None:
        self._stale = True
        if self.isVisible():
            self.refresh()
            self.update()

    def _replaced(self) -> None:
        """Another document: fit it, unless it's only the 2D tab's coming and going, which
        leaves the part as it was."""
        space = self.session.space
        if space is Space.PART and self._space is Space.PART:
            self.mesh, self._fit = None, True
        self._space = space
        self._changed()

    def _selection_changed(self) -> None:
        self.offering = False  # something was picked: the planes go back to how they were
        self.update()

    def show_preview(self, document: Document | None) -> None:
        """Draw the solid `document` has in place of the part's, in the preview's colour: the
        part as the Extrude form's fields would leave it. None draws the part again. Worked
        out when the view is next painted, as a proposal's is."""
        if document is not self.preview:
            self.preview = document
            self._proposal_stale = True
            self.update()

    def show_proposal(self) -> None:
        """The proposal to preview changed: the solid drawn is the one it would leave. It's
        worked out when the view is next painted, not here: an agent's call that changes the
        proposal is answered first, and several changes between frames cost one solid."""
        self._proposal_stale = True
        self.update()

    def refresh(self) -> None:
        """Ask the engine for the part's solid again, if the document changed since, and for
        the solid a pending proposal would leave, if the proposal did."""
        if not self._stale and not self._proposal_stale:
            return
        if self.session.space is not Space.PART:
            # The 2D tab's sketch is never drawn here, so nothing is built for it (Performance
            # V2.2, Perf-8). It has no solid: the part's is asked for again on coming back.
            self.mesh = None
            return
        document = self.session.document
        curves = None if self._stale else self.scene.curves  # a proposal leaves them alone
        if self._stale:
            self.problem = None
            if not any(isinstance(f, Extrude) for f in document.features):
                self.mesh = None
                if not any(isinstance(f, Sketch) for f in document.features):
                    self.problem = "Pick a plane and press Sketch, or double-click a plane"
            else:
                self._solid()
        self._stale = self._proposal_stale = False
        other, self.note = self._previewed(), None
        previewed = other is not None
        if not previewed:  # the form's own solid is what's being worked on: it comes first
            other, self.note = self._proposed()
        self.scene = Scene.of(
            document,
            self.mesh if other is None else other,
            proposed=other is not None,
            preview=previewed,
            curves=curves,
        )
        self.hover, self._lit = None, frozenset()  # of the scene before: found again on a move
        if self._fit:
            self.camera = self.camera.fitted(self.scene.box(), self.width(), self.height())
            self._fit = False
        elif other is not None:
            self._bring_into_view()
        else:
            self._brought = None

    def _bring_into_view(self) -> None:
        """A proposed or previewed solid that reaches past the part and out of the view is
        fitted, with the part, from where the camera looks. Once: the view isn't moved again
        until the solid outgrows what was last looked at, so zooming in on it holds."""
        box, own = self.scene.box(), mesh_box(self.mesh)
        last, self._brought = self._brought, box
        if (own is not None and _within(box, own)) or (last is not None and _within(box, last)):
            return
        width, height = self.width(), self.height()
        corners = [
            self.camera.project(Point3(x=x, y=y, z=z), width, height)
            for x in (box.x_min, box.x_max)
            for y in (box.y_min, box.y_max)
            for z in (box.z_min, box.z_max)
        ]
        if all(0 <= c.x <= width and 0 <= c.y <= height for c in corners):
            return
        self.camera = self.camera.fitted(box if own is None else _around(box, own), width, height)

    def _previewed(self) -> Mesh | None:
        """The solid the Extrude form's fields would make, when it isn't the part's own. A
        solid that can't be worked out leaves the part's drawn: the form says why."""
        document = self.preview
        if document is None or document is self.session.document:
            return None
        if not any(isinstance(f, Extrude) for f in document.features):
            return None
        mesh = _mesh(Bus(document).queries)
        if isinstance(mesh, Error) or mesh is self.mesh or mesh == self.mesh:
            return None
        return mesh

    def _solid(self) -> None:
        mesh = _mesh(self.session.queries)
        if isinstance(mesh, Error):
            self._failed(mesh)
            return
        self.mesh = mesh
        if not mesh.triangles:
            self.problem = "The part's solid is empty: everything was cut away"

    def _proposed(self) -> tuple[Mesh | None, str | None]:
        """The solid a pending proposal would leave, when it isn't the part's own, and what
        to say about it (C-16). The proposal's result is a document like any other, so the
        engine meshes it as it does the part, and keeps what the two share."""
        proposal = self.proposal()
        document = self.session.document
        if proposal is None or proposal.base is not document or proposal.result is document:
            return None, None
        had = self.mesh is not None and bool(self.mesh.triangles)
        gone = "Accepting would leave the part with no solid" if had else None
        if not any(isinstance(f, Extrude) for f in proposal.result.features):
            return None, gone
        mesh = _mesh(Bus(proposal.result).queries)
        if isinstance(mesh, Error):
            if mesh.code is ErrorCode.KERNEL_UNAVAILABLE:
                return None, None  # `problem` says what to install
            if mesh.code is ErrorCode.SELECTION_EMPTY:  # nothing left to extrude
                return None, gone
            return None, f"Accepting would leave the part without its solid. {mesh.message}"
        if mesh is self.mesh or mesh == self.mesh:
            return None, None  # the proposal leaves the solid as it is
        if not mesh.triangles:
            return mesh, "Accepting would cut the whole part away"
        return mesh, "The part as proposed. Accept to keep it, Reject to leave the part as it is"

    def _failed(self, error: Error) -> None:
        if error.code is ErrorCode.SELECTION_EMPTY:
            self.mesh = None
        elif error.code is ErrorCode.KERNEL_UNAVAILABLE:
            self.mesh = None
            self.problem = "Solids need a geometry kernel: uv sync --extra occt"
        else:
            # The last good solid stays on screen, marked out of date (core.md, item 5).
            self.problem = f"Showing the last solid that worked out. {error.message}"

    def fit(self) -> None:
        self._fit = True
        self._stale = True
        self.refresh()
        self.update()

    def showEvent(self, event: object) -> None:  # noqa: N802
        self.refresh()
        super().showEvent(event)  # type: ignore[arg-type]

    def paint_scene(
        self, painter: QPainter, camera: Camera, width: float, height: float, **options: object
    ) -> None:
        """The scene as `camera` sees it: for the canvas behind a sketch edited in 3D."""
        self.refresh()
        painter.fillRect(QRectF(0, 0, width, height), theme.CANVAS)
        looks = self.plane_look()
        self.scene.paint(painter, camera, width, height, planes=looks, **options)  # type: ignore[arg-type]
        self.paint_triad(painter, camera, height)

    def look_at(self, frame: Frame) -> None:
        """Turn to look straight at the plane `frame` lies on, from its front, keeping what's
        in the middle of the view there and as close."""
        middle = on_plane(frame, self.camera.target)
        self.camera = facing_camera(frame, middle, self.camera.scale)
        self.update()

    def plane_look(self) -> str:
        """How the origin planes are drawn now (`scene3d.PLANE_LOOKS`)."""
        if self.offering:
            return "full"
        return "auto" if self.show_planes else "hidden"

    def set_show_planes(self, shown: bool) -> None:
        self.show_planes = shown
        self.update()

    def offer_planes(self) -> None:
        """Sketch was asked for with nothing to sketch on: show the planes in full, to pick."""
        self.offering = True
        self.update()

    def solid_shown(self) -> object:
        """The solid `paint_scene` draws and whether it's a proposal's, as a value that
        changes only when it does: the engine keeps a mesh by identity, so the same solid
        compares at once."""
        self.refresh()
        return (self.scene.mesh, self.scene.proposed)

    def shows(self, hidden: EntityId | None) -> object:
        """What `paint_scene` draws with the sketch `hidden` left out, as a value equal only
        when it draws the same: the solid (the engine keeps its mesh by identity, so an
        unchanged one compares at once), the other sketches' curves, and the planes' size."""
        self.refresh()
        scene = self.scene
        curves = tuple(c for c in scene.curves if c.sketch != hidden)
        return (scene.mesh, curves, scene.half, scene.proposed, scene.preview, self.plane_look())

    # --- Drawing ------------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        self.refresh()  # a proposal that changed since the last frame
        started = time.perf_counter()
        painter = QPainter(self)
        width, height = self.width(), self.height()
        painter.fillRect(self.rect(), theme.CANVAS)
        picked = self.session.picked_plane
        self.scene.paint(
            painter,
            self.camera,
            width,
            height,
            picked=picked,
            selected=self.session.selection,
            tinted=self._tinted(picked),
            hovered=self.hover,
            lit=self._lit,
            planes=self.plane_look(),
        )
        self.paint_triad(painter, self.camera, height)
        said = self.note or self.problem  # the proposal is what's on screen
        if said:
            painter.setPen(theme.AGENT if self.note else theme.TEXT_DIM)
            painter.setFont(theme.font())
            # One line sits where it always did, right of the triad's corner. In a view too
            # narrow for that it wraps across the view, upwards from above the triad.
            metrics = painter.fontMetrics()
            if metrics.horizontalAdvance(said) <= width - 2 * TRIAD_ROOM:
                box = QRectF(0, 0, width, height - 24 + metrics.height() / 2)
            else:
                box = QRectF(SPACE.l, 0, max(width - 2 * SPACE.l, 1.0), height - TRIAD_ROOM)
            painter.drawText(box, CAPTION_FLAGS, said)
        painter.end()
        self.frame_ms.append(1e3 * (time.perf_counter() - started))

    @staticmethod
    def paint_triad(painter: QPainter, camera: Camera, height: float) -> None:
        """A small triad in the corner: X, Y, and Z as the camera sees them."""
        right, up, _ = camera.axes()
        origin = QPointF(36, height - 36)
        for axis, color in (
            (Point3(x=1.0, y=0.0, z=0.0), theme.AXIS_X),
            (Point3(x=0.0, y=1.0, z=0.0), theme.AXIS_Y),
            (Point3(x=0.0, y=0.0, z=1.0), theme.AXIS_Z),
        ):
            x = axis.x * right.x + axis.y * right.y + axis.z * right.z
            y = axis.x * up.x + axis.y * up.y + axis.z * up.z
            painter.setPen(QPen(color, 2.0))
            painter.drawLine(origin, QPointF(origin.x() + 22 * x, origin.y() - 22 * y))

    # --- Picking ------------------------------------------------------------------------

    def pick(self, x: float, y: float) -> Picked:
        """What a click at (x, y) picks: a sketch, a flat face of the solid (ADR 0016), or a
        plane; the solid hides the planes behind it."""
        found, triangle = self._under(x, y)
        if isinstance(found, FaceRef):  # where it was hit: the tint floods out from there
            self._seed = (self.scene, found, triangle) if triangle is not None else None
        return found

    def _under(self, x: float, y: float) -> tuple[Picked, int | None]:
        """What's under (x, y), as `pick` returns it, and the triangle of the solid there."""
        self.refresh()
        scene, size = self.scene, (self.width(), self.height())
        # A proposed solid isn't the part yet: it has no faces to name, so it isn't picked.
        faces = None if scene.proposed else self._face_at
        hit = None if faces is None else scene.solid_hit(self.camera, *size, x, y)
        found = scene.pick_over(
            self.camera, *size, x, y, hit, faces=faces, planes=self.plane_look() != "hidden"
        )
        return found, None if hit is None else hit[3]

    def _face_at(self, point: Point3, normal: Point3) -> FaceRef | None:
        return self.session.queries.face_at(point, normal, FACE_TOLERANCE)

    def _tinted(self, picked: object) -> frozenset[int]:
        """The triangles drawn as picked: the picked face's, and what a selected extrude made."""
        if self.scene.proposed:
            return frozenset()
        face, made = self._face(picked), self._selected()
        return face | made if made else face  # the kept set itself, when it's all there is

    def _face(self, picked: object) -> frozenset[int]:
        """The triangles of the picked face, worked out once per scene."""
        if not isinstance(picked, FaceRef):
            return frozenset()
        key = (self.scene, picked)
        if self._tint is not None and self._tint[0] == key:
            return self._tint[1]
        seed = self._seed
        if seed is not None and seed[0] is self.scene and seed[1] == picked:
            found = self.scene.face_region(seed[2])
        else:  # picked another way, or the part changed since: by name, triangle by triangle
            frame = self.session.queries.plane_frame(picked)
            found = (
                frozenset()
                if isinstance(frame, Error)
                else self.scene.face_triangles(frame, picked, self._face_at)
            )
        self._tint = (key, found)
        return found

    def _selected(self) -> frozenset[int]:
        """What the selected extrudes made, worked out once per scene and selection."""
        selection = self.session.selection
        made = self._made
        if made is not None and made[0] is self.scene and made[1] == selection:
            return made[2]
        found: frozenset[int] = frozenset()
        for feature in self.session.document.features:
            if isinstance(feature, Extrude) and feature.id in selection:
                found |= self._swept(feature)
        self._made = (self.scene, selection, found)
        return found

    def _swept(self, extrude: Extrude) -> frozenset[int]:
        """The triangles of the part's solid that `extrude` made (`Scene.swept_triangles`)."""
        document = self.session.document
        plane = next(
            (
                f.plane
                for f in document.features
                if isinstance(f, Sketch) and f.id == extrude.sketch
            ),
            None,
        )
        frame = None if plane is None else self.session.queries.plane_frame(plane)
        if frame is None or isinstance(frame, Error):
            return frozenset()
        entities = document.entities
        profile = extrude.ids or tuple(
            id for id, e in entities.items() if getattr(e, "sketch", None) == extrude.sketch
        )
        rounds = [
            (e.center, e.radius)
            for e in (entities.get(id) for id in profile)
            if isinstance(e, Circle | Arc) and not e.construction
        ]
        return self.scene.swept_triangles(
            extrude.id, frame, extrude.depth, rounds, self._face_at, reversed=extrude.reversed
        )

    # --- Under the pointer --------------------------------------------------------------

    def set_hover(self, target: object) -> None:
        """Show `target` as hovered: a plane, a sketch or an extrude by its id, or None. For
        the Part panel, whose rows are the same things."""
        self.refresh()
        lit: frozenset[int] = frozenset()
        extrude = next(
            (
                f
                for f in self.session.document.features
                if isinstance(f, Extrude) and f.id == target
            ),
            None,
        )
        if extrude is not None and not self.scene.proposed:
            lit = self._swept(extrude)
        self._show_hover(target, lit)  # type: ignore[arg-type]

    def _hover_now(self) -> None:
        at = self._hover_at
        if at is None or self._drag is not None or not self.isVisible():
            return
        found, triangle = self._under(at.x(), at.y())
        on_face = isinstance(found, FaceRef) and triangle is not None
        self._show_hover(found, self.scene.face_region(triangle) if on_face else frozenset())  # type: ignore[arg-type]

    def _show_hover(self, hover: Picked, lit: frozenset[int]) -> None:
        if hover != self.hover or lit != self._lit:
            self.hover, self._lit = hover, lit
            self.update()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._hover_at = None
        self._show_hover(None, frozenset())
        super().leaveEvent(event)

    def _choose(self, found: Picked) -> None:
        if isinstance(found, Plane | FaceRef):
            self.session.set_picked_plane(found)
        elif found is None:
            self.session.set_picked_plane(None)
            self.session.set_selection(frozenset())
        else:
            self.session.set_selection(frozenset({found}))

    # --- Moving the camera --------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        panning = event.button() in (
            Qt.MouseButton.RightButton,
            Qt.MouseButton.MiddleButton,
        ) or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self._drag = (event.button(), event.position(), panning)
        self._pressed = event.position() if event.button() == Qt.MouseButton.LeftButton else None
        self._clicked = event.position() if event.button() == Qt.MouseButton.RightButton else None
        self._show_hover(None, frozenset())  # the view is about to move, or the pick to land
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag is None:
            self._hover_at = event.position()
            if not self._hover_timer.isActive():
                self._hover_timer.start()
            return
        button, last, panning = self._drag
        if self._pressed is not None:
            moved = event.position() - self._pressed
            if math.hypot(moved.x(), moved.y()) < DRAG_PX:
                return  # still a click
            self._pressed = None
        if self._clicked is not None:
            moved = event.position() - self._clicked
            if math.hypot(moved.x(), moved.y()) < DRAG_PX:
                return  # still a right click
            self._clicked = None
        delta = event.position() - last
        if panning:
            self.camera = self.camera.panned(delta.x(), delta.y())
        else:
            self.camera = self.camera.orbited(delta.x(), delta.y())
        self._drag = (button, event.position(), panning)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._pressed is not None and event.button() == Qt.MouseButton.LeftButton:
            at = event.position()
            self._choose(self.pick(at.x(), at.y()))
        if self._clicked is not None and event.button() == Qt.MouseButton.RightButton:
            at = event.position()  # a right click, not a pan: the menu for what's under it
            self.menu_requested.emit(self.pick(at.x(), at.y()), self.mapToGlobal(at.toPoint()))
        self._drag, self._pressed, self._clicked = None, None, None
        self._hover_at = event.position()
        self._hover_timer.start()
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            at = event.position()
            found = self.pick(at.x(), at.y())
            if found is not None:
                self._choose(found)
                self.open_requested.emit(found)
                return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        self.camera = scrolled(self.camera, event, self.width(), self.height())
        self.update()
        event.accept()

    def event(self, event: QEvent) -> bool:
        if isinstance(event, QNativeGestureEvent):
            camera = pinched(self.camera, event, self.width(), self.height())
            if camera is not None:
                self.camera = camera
                self.update()
                return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_F:
            self.fit()
            return
        super().keyPressEvent(event)


WHEEL_ZOOM = 1.15
"""How much one notch of a mouse wheel zooms."""


def scrolled(camera: Camera, event: QWheelEvent, width: float, height: float) -> Camera:
    """`camera` after a scroll, read as the canvas reads one, so a trackpad does in 3D what it
    does in a sketch: two fingers pan, with ⌘ they zoom, and with Shift they orbit (Fusion's
    map on a Mac). A mouse wheel, which has no pixel delta, zooms. A zoom is about the
    pointer."""
    pixels = event.pixelDelta()
    trackpad = not pixels.isNull() and (
        event.device().type() == QInputDevice.DeviceType.TouchPad
        or event.phase() != Qt.ScrollPhase.NoScrollPhase
    )
    modifiers = event.modifiers()
    if trackpad and not modifiers & Qt.KeyboardModifier.ControlModifier:
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            return camera.orbited(pixels.x(), pixels.y())
        return camera.panned(pixels.x(), pixels.y())
    steps = (event.angleDelta().y() or event.angleDelta().x()) / 120
    if not steps:
        return camera
    at = event.position()
    return camera.zoomed(WHEEL_ZOOM**steps, (at.x(), at.y()), (width, height))


def pinched(
    camera: Camera, event: QNativeGestureEvent, width: float, height: float
) -> Camera | None:
    """`camera` after a trackpad pinch, zoomed about the pointer; None for another gesture."""
    if event.gestureType() != Qt.NativeGestureType.ZoomNativeGesture:
        return None
    at = event.position()
    return camera.zoomed(1.0 + event.value(), (at.x(), at.y()), (width, height))


def _within(inner: BoundingBox3, outer: BoundingBox3) -> bool:
    slack = 1e-6
    return (
        inner.x_min >= outer.x_min - slack
        and inner.y_min >= outer.y_min - slack
        and inner.z_min >= outer.z_min - slack
        and inner.x_max <= outer.x_max + slack
        and inner.y_max <= outer.y_max + slack
        and inner.z_max <= outer.z_max + slack
    )


def _around(a: BoundingBox3, b: BoundingBox3) -> BoundingBox3:
    return BoundingBox3(
        x_min=min(a.x_min, b.x_min),
        y_min=min(a.y_min, b.y_min),
        z_min=min(a.z_min, b.z_min),
        x_max=max(a.x_max, b.x_max),
        y_max=max(a.y_max, b.y_max),
        z_max=max(a.z_max, b.z_max),
    )


def _mesh(queries: Queries) -> Mesh | Error:
    """The solid of the document `queries` reads, as a mesh fine enough for its size; an empty
    mesh when everything was cut away."""
    solid = queries.solid_properties()
    if isinstance(solid, Error):
        return solid
    box = solid.bounding_box
    if box is None:
        return Mesh(vertices=(), triangles=())
    size = math.dist((box.x_min, box.y_min, box.z_min), (box.x_max, box.y_max, box.z_max))
    return queries.mesh(tolerance=min(1.0, max(0.01, size / 1000)))
