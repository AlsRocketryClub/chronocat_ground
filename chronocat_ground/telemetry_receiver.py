from __future__ import annotations

import socket
import time

from PySide6.QtCore import QThread, Signal

from .protocol import (
    CombinedTelemetryPacket,
    DEFAULT_TELEMETRY_PORT,
    PidTelemetryPacket,
    TelemetryPacket,
    parse_telemetry_packets,
)

# How long to wait before reopening the UDP port after a socket error.
REOPEN_DELAY_S = 1.0


class TelemetryReceiver(QThread):
    packet_received = Signal(object, str, float, float)
    receive_error = Signal(str)

    def __init__(self, port: int = DEFAULT_TELEMETRY_PORT) -> None:
        super().__init__()
        self.port = port
        self._running = False
        self._socket: socket.socket | None = None

    def stop(self) -> None:
        self._running = False
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass

    def start(self, priority: QThread.Priority = QThread.InheritPriority) -> None:
        """Mark the receiver active before Qt schedules ``run``."""
        self._running = True
        super().start(priority)

    def _wait_before_reopening(self) -> None:
        deadline = time.monotonic() + REOPEN_DELAY_S
        while self._running and time.monotonic() < deadline:
            self.msleep(50)

    def run(self) -> None:
        # A socket error must not end telemetry for the session: report it,
        # wait, and open the port again until stop() is called.
        bind_error_reported = False
        while self._running:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(0.25)
                sock.bind(("0.0.0.0", self.port))
            except OSError as exc:
                if not bind_error_reported:
                    self.receive_error.emit(
                        f"Could not bind UDP telemetry port {self.port}: {exc}; retrying"
                    )
                    bind_error_reported = True
                self._wait_before_reopening()
                continue
            bind_error_reported = False
            self._socket = sock
            self._receive(sock)
            if self._running:
                self._wait_before_reopening()
        self._running = False

    def _receive(self, sock: socket.socket) -> None:
        try:
            while self._running:
                try:
                    data, address = sock.recvfrom(2048)
                    received_monotonic = time.monotonic()
                    received_wall = time.time()
                except TimeoutError:
                    continue
                except OSError as exc:
                    if self._running:
                        self.receive_error.emit(f"UDP socket error ({exc}); reopening the port")
                    break

                try:
                    packets: list[
                        TelemetryPacket | PidTelemetryPacket | CombinedTelemetryPacket
                    ] = parse_telemetry_packets(data)
                except ValueError as exc:
                    preview = data[:16].hex(" ")
                    self.receive_error.emit(
                        f"{address[0]}:{address[1]} sent {len(data)} bytes: {exc}; first bytes: {preview}"
                    )
                    continue

                for packet in packets:
                    self.packet_received.emit(
                        packet,
                        f"{address[0]}:{address[1]} ({len(data)} bytes)",
                        received_monotonic,
                        received_wall,
                    )
        finally:
            self._socket = None
            try:
                sock.close()
            except OSError:
                pass
