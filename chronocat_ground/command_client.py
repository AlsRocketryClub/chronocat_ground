from __future__ import annotations

import socket

from .protocol import (
    DEFAULT_COMMAND_PORT,
    DEFAULT_DEVICE_HOST,
    RESPONSE_PACKET_SIZE,
    CommandResponse,
    build_command,
    parse_command_response,
)


class CommandClient:
    def __init__(self) -> None:
        self._socket: socket.socket | None = None
        self.host = DEFAULT_DEVICE_HOST
        self.port = DEFAULT_COMMAND_PORT

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def connect(self, host: str, port: int, timeout: float = 2.0) -> None:
        self.disconnect()

        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)
        self._socket = sock
        self.host = host
        self.port = port

    def disconnect(self) -> None:
        if self._socket is None:
            return

        try:
            self._socket.close()
        finally:
            self._socket = None

    def send_command(self, command: int, arg1: int = 0, arg2: int = 0) -> CommandResponse:
        if self._socket is None:
            raise ConnectionError("not connected")

        self._socket.sendall(build_command(command, arg1, arg2))
        response = self._recv_exact(RESPONSE_PACKET_SIZE)
        return parse_command_response(response)

    def _recv_exact(self, length: int) -> bytes:
        if self._socket is None:
            raise ConnectionError("not connected")

        chunks: list[bytes] = []
        remaining = length

        while remaining > 0:
            chunk = self._socket.recv(remaining)
            if not chunk:
                self.disconnect()
                raise ConnectionError("connection closed")
            chunks.append(chunk)
            remaining -= len(chunk)

        return b"".join(chunks)
