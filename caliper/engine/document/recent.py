"""What was worked out for the last few documents, kept by identity and shared between threads.

Documents are immutable (ADR 0002), so a value worked out for one never goes stale, and holding
the document keeps its id from being reused. The next document's value is usually worked out
from the latest one's: a command replaces a few entities and shares the rest.
"""

import threading
from collections import OrderedDict

from caliper.contracts.document import Document


class Recent[V]:
    """A value for each of the last `size` documents asked about.

    Safe to share between threads: the in-app assistant's tool calls run on a worker thread
    while the window asks about its own document.
    """

    def __init__(self, size: int) -> None:
        self._size = size
        self._entries: OrderedDict[int, tuple[Document, V]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, document: Document) -> V | None:
        with self._lock:
            entry = self._entries.get(id(document))
            if entry is None or entry[0] is not document:
                return None
            self._entries.move_to_end(id(document))
            return entry[1]

    def latest(self) -> tuple[Document, V] | None:
        """The document asked about last, and its value: where to work the next one out from."""
        with self._lock:
            return next(reversed(self._entries.values()), None)

    def put(self, document: Document, value: V) -> None:
        with self._lock:
            self._entries[id(document)] = (document, value)
            self._entries.move_to_end(id(document))
            while len(self._entries) > self._size:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
