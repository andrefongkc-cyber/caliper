"""The one place the shell tolerates engine pieces that aren't built yet.

The shell calls the full contract API. Stream A is still building parts of it (move,
delete, transactions, merge keys, most queries), and those raise NotImplementedError. Every
such call goes through `attempt`, which turns that into an `Unavailable` value the UI can
show. There's no fallback geometry here: when the engine lands a piece, the feature starts
working with no shell change. Delete this module once the engine is complete.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Unavailable:
    feature: str

    @property
    def message(self) -> str:
        return f"{self.feature} isn't in the engine yet"


def attempt[T](feature: str, call: Callable[[], T]) -> T | Unavailable:
    try:
        return call()
    except NotImplementedError:
        return Unavailable(feature)
