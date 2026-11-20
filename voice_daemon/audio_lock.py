"""Eine Sperre für alle PortAudio-Streams des Sprach-Daemons.

Öffnet ein Thread einen Stream (Sprachausgabe, Piepton), während ein anderer gerade den
Mikrofonstream stoppt (Stummschalten), können sich PortAudio und CoreAudio gegenseitig
blockieren: Pa_OpenStream wartet in CreateIOProcID, Pa_StopStream in StopIOProc — für immer.
Darum laufen Öffnen/Starten und Stoppen/Schließen nur nacheinander.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Generator, Protocol, TypeVar

PA_LOCK = threading.RLock()

class _Stream(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...


S = TypeVar("S", bound=_Stream)


def start_stream(factory: Callable[[], S]) -> S:
    """Erzeugt und startet einen Stream unter der Sperre."""
    with PA_LOCK:
        stream = factory()
        try:
            stream.start()
        except BaseException:
            stream.close()
            raise
    return stream


def close_stream(stream: _Stream) -> None:
    """Stoppt und schließt einen Stream unter der Sperre."""
    with PA_LOCK:
        try:
            stream.stop()
        finally:
            stream.close()


@contextmanager
def opened_stream(factory: Callable[[], S]) -> Generator[S, None, None]:
    stream = start_stream(factory)
    try:
        yield stream
    finally:
        close_stream(stream)
