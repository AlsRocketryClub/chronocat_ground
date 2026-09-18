from __future__ import annotations

from dataclasses import dataclass
import threading

from PySide6.QtCore import QObject, Signal

from .command_client import CommandClient
from .protocol import CommandResponse


@dataclass(frozen=True)
class CommandRequest:
    """A transport-level command request, independent of any page widget."""

    command: int
    arg1: int = 0
    arg2: int = 0
    timeout: float | None = None


class CommandDispatcher(QObject):
    """Run one TCP command at a time and marshal its result back to Qt."""

    completed = Signal(object, object)  # CommandRequest, CommandResponse
    failed = Signal(object, str)  # CommandRequest, error text
    connection_succeeded = Signal(str, int)
    connection_failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, client: CommandClient, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._lock = threading.Lock()
        self._busy = False
        self._shutting_down = False
        self._thread: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def submit(self, request: CommandRequest) -> bool:
        """Submit a request, returning false when another request is active."""
        with self._lock:
            if self._busy or self._shutting_down:
                return False
            self._busy = True
        self.busy_changed.emit(True)

        self._thread = threading.Thread(
            target=self._run,
            args=(request,),
            daemon=True,
            name=f"command-{request.command:02x}",
        )
        self._thread.start()
        return True

    def _run(self, request: CommandRequest) -> None:
        try:
            response = self._client.send_command(
                request.command,
                request.arg1,
                request.arg2,
                timeout=request.timeout,
            )
        except (OSError, ValueError, ConnectionError) as exc:
            self._finish()
            self.failed.emit(request, str(exc))
        else:
            self._finish()
            self.completed.emit(request, response)

    def connect(self, host: str, port: int, timeout: float = 2.0) -> bool:
        with self._lock:
            if self._busy or self._shutting_down:
                return False
            self._busy = True
        self.busy_changed.emit(True)
        self._thread = threading.Thread(
            target=self._run_connect,
            args=(host, port, timeout),
            daemon=True,
            name="command-connect",
        )
        self._thread.start()
        return True

    def _run_connect(self, host: str, port: int, timeout: float) -> None:
        try:
            self._client.connect(host, port, timeout=timeout)
        except OSError as exc:
            self._finish()
            self.connection_failed.emit(str(exc))
        else:
            self._finish()
            self.connection_succeeded.emit(host, port)

    def _finish(self) -> None:
        with self._lock:
            self._busy = False
        self.busy_changed.emit(False)

    def shutdown(self) -> None:
        with self._lock:
            self._shutting_down = True
            thread = self._thread
        if thread is not None and thread.is_alive():
            self._client.disconnect()
            thread.join(timeout=5.0)
