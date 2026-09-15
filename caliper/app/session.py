"""One open document: the bus, its file, and the UI state that goes with it.

The bus owns the document. The session owns what the document is not allowed to hold:
selection, hover, the file path, and whether there are unsaved changes.
"""

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from caliper.app.engine_gaps import Unavailable, attempt
from caliper.contracts.commands import (
    Applied,
    Change,
    Command,
    CommandBus,
    CommandResult,
    DeleteEntities,
    MoveEntities,
    Rejected,
)
from caliper.contracts.document import Document, EntityId
from caliper.contracts.queries import Queries
from caliper.engine.commands.bus import Bus
from caliper.engine.io import snapshot

_GAP_NAMES: dict[type, str] = {MoveEntities: "Move", DeleteEntities: "Delete"}


class DocumentSession(QObject):
    document_changed = Signal()
    """The document was replaced or changed. Read `session.document`."""
    selection_changed = Signal()
    hover_changed = Signal()
    file_changed = Signal()
    """The path or the unsaved-changes state changed."""
    message = Signal(str)
    """Something worth a line in the status bar."""

    def __init__(self, bus: CommandBus | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus: CommandBus = bus if bus is not None else Bus()
        self._unsubscribe = self._bus.subscribe(self._on_change)
        self._saved: Document = self._bus.document
        self._path: Path | None = None
        self._selection: frozenset[EntityId] = frozenset()
        self._hover: EntityId | None = None

    # --- Engine ---------------------------------------------------------------------------

    @property
    def bus(self) -> CommandBus:
        return self._bus

    @property
    def document(self) -> Document:
        return self._bus.document

    @property
    def queries(self) -> Queries:
        return self._bus.queries

    def execute(self, command: Command) -> CommandResult | Unavailable:
        """Send a command. Rejections and missing engine pieces are reported as messages."""
        name = _GAP_NAMES.get(type(command), type(command).__name__)
        result = attempt(name, lambda: self._bus.execute(command))
        match result:
            case Unavailable():
                self.message.emit(result.message)
            case Rejected(errors=errors):
                self.message.emit("; ".join(e.message for e in errors))
            case Applied():
                pass
        return result

    def undo(self) -> None:
        if (change := self._bus.undo()) is not None:
            self.message.emit(f"Undid {change.label}")

    def redo(self) -> None:
        if (change := self._bus.redo()) is not None:
            self.message.emit(f"Redid {change.label}")

    def _on_change(self, change: Change) -> None:
        live = self._bus.document.entities
        if any(id not in live for id in self._selection):
            self._selection = frozenset(id for id in self._selection if id in live)
            self.selection_changed.emit()
        if self._hover is not None and self._hover not in live:
            self._hover = None
            self.hover_changed.emit()
        self.document_changed.emit()
        self.file_changed.emit()

    # --- Files ----------------------------------------------------------------------------

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def is_dirty(self) -> bool:
        return self._bus.document != self._saved

    def replace(self, bus: CommandBus, path: Path | None) -> None:
        """Switch to another document. Undo history never crosses documents, so it's a new bus."""
        self._unsubscribe()
        self._bus = bus
        self._unsubscribe = bus.subscribe(self._on_change)
        self._saved = bus.document
        self._path = path
        self._selection = frozenset()
        self._hover = None
        self.selection_changed.emit()
        self.hover_changed.emit()
        self.document_changed.emit()
        self.file_changed.emit()

    def new(self) -> None:
        self.replace(Bus(), None)

    def open(self, path: Path) -> None:
        """Raises `LoadError` (from caliper.engine.io.canonical) for a bad file; nothing changes."""
        self.replace(Bus(snapshot.load(path)), path)

    def save(self, path: Path | None = None) -> None:
        target = path if path is not None else self._path
        if target is None:
            raise ValueError("an untitled document needs a path")
        document = self._bus.document
        snapshot.save(document, target)
        self._saved = document
        self._path = target
        self.file_changed.emit()

    # --- UI state -------------------------------------------------------------------------

    @property
    def selection(self) -> frozenset[EntityId]:
        return self._selection

    def set_selection(self, ids: frozenset[EntityId]) -> None:
        if ids != self._selection:
            self._selection = ids
            self.selection_changed.emit()

    @property
    def hover(self) -> EntityId | None:
        return self._hover

    def set_hover(self, id: EntityId | None) -> None:
        if id != self._hover:
            self._hover = id
            self.hover_changed.emit()
