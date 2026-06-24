from __future__ import annotations

import socket

from PySide6.QtCore import QThread, Signal

from .protocol import DEFAULT_TELEMETRY_PORT, TelemetryPacket, parse_telemetry_packets


class TelemetryReceiver(QThread):
    packet_received = Signal(object, str)
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

    def run(self) -> None:
        self._running = True

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.25)
            sock.bind(("0.0.0.0", self.port))
        except OSError as exc:
            self.receive_error.emit(f"Could not bind UDP telemetry port {self.port}: {exc}")
            return

        self._socket = sock

        try:
            while self._running:
                try:
                    data, address = sock.recvfrom(2048)
                except TimeoutError:
                    continue
                except OSError:
                    if self._running:
                        self.receive_error.emit("UDP socket closed unexpectedly")
                    break

                try:
                    packets: list[TelemetryPacket] = parse_telemetry_packets(data)
                except ValueError as exc:
                    preview = data[:16].hex(" ")
                    self.receive_error.emit(
                        f"{address[0]}:{address[1]} sent {len(data)} bytes: {exc}; first bytes: {preview}"
                    )
                    continue

                for packet in packets:
                    self.packet_received.emit(packet, f"{address[0]}:{address[1]} ({len(data)} bytes)")
        finally:
            self._socket = None
            try:
                sock.close()
            except OSError:
                pass
