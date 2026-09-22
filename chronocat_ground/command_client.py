from __future__ import annotations

import select
import socket
import threading

from .protocol import (
    DEFAULT_COMMAND_PORT,
    DEFAULT_DEVICE_HOST,
    RESPONSE_PACKET_SIZE,
    CommandResponse,
    build_command,
    parse_command_response,
)


def _enable_aggressive_keepalive(sock: socket.socket) -> None:
    """Make a dead peer (e.g. an unplugged cable) surface as a socket error
    within seconds instead of TCP's default ~2 hour keepalive idle time."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    idle_seconds = 3
    interval_seconds = 2
    probe_count = 3
    for option_name, value in (
        ("TCP_KEEPIDLE", idle_seconds),  # Linux
        ("TCP_KEEPALIVE", idle_seconds),  # macOS
        ("TCP_KEEPINTVL", interval_seconds),
        ("TCP_KEEPCNT", probe_count),
    ):
        option = getattr(socket, option_name, None)
        if option is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, option, value)
            except OSError:
                pass


class CommandClient:
    def __init__(self) -> None:
        self._socket: socket.socket | None = None
        self._lock = threading.RLock()
        self.host = DEFAULT_DEVICE_HOST
        self.port = DEFAULT_COMMAND_PORT

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._socket is not None

    def connect(self, host: str, port: int, timeout: float = 2.0) -> None:
        with self._lock:
            self.disconnect()

            sock = socket.create_connection((host, port), timeout=timeout)
            sock.settimeout(timeout)
            _enable_aggressive_keepalive(sock)
            self._socket = sock
            self.host = host
            self.port = port

    def disconnect(self) -> None:
        with self._lock:
            if self._socket is None:
                return

            try:
                self._socket.close()
            finally:
                self._socket = None

    def check_connection(self) -> bool:
        """Actively probe the TCP session rather than trusting a cached flag.

        getpeername() alone only reflects local socket state and stays
        "connected" even after the physical link (e.g. Ethernet) is cut,
        until the OS notices via keepalive or an actual I/O attempt. Combine
        keepalive (see _enable_aggressive_keepalive) with a non-blocking
        peek so a dead peer is caught within a few seconds instead of never.
        """
        with self._lock:
            if self._socket is None:
                return False
            try:
                self._socket.getpeername()
            except OSError:
                self.disconnect()
                return False

            try:
                readable, _, errored = select.select(
                    [self._socket], [], [self._socket], 0
                )
            except OSError:
                self.disconnect()
                return False
            if errored:
                self.disconnect()
                return False
            if readable:
                try:
                    peeked = self._socket.recv(1, socket.MSG_PEEK)
                except BlockingIOError:
                    pass
                except OSError:
                    self.disconnect()
                    return False
                else:
                    if peeked == b"":
                        # Peer closed the connection (FIN) or a keepalive
                        # probe timed out and the kernel gave up on it.
                        self.disconnect()
                        return False
            return True

    def send_command(
        self,
        command: int,
        arg1: int = 0,
        arg2: int = 0,
        timeout: float | None = None,
    ) -> CommandResponse:
        with self._lock:
            sock = self._socket
        return self._send_command(sock, command, arg1, arg2, timeout)

    def _send_command(
        self,
        sock: socket.socket | None,
        command: int,
        arg1: int,
        arg2: int,
        timeout: float | None,
    ) -> CommandResponse:
        if sock is None:
            raise ConnectionError("not connected")

        old_timeout = sock.gettimeout()
        if timeout is not None:
            sock.settimeout(timeout)

        try:
            sock.sendall(build_command(command, arg1, arg2))
            response = self._recv_exact(sock, RESPONSE_PACKET_SIZE)
            parsed = parse_command_response(response)
            if parsed.command != command:
                raise ValueError(
                    f"response command 0x{parsed.command:02x} "
                    f"does not match request 0x{command:02x}"
                )
            return parsed
        finally:
            if timeout is not None:
                try:
                    sock.settimeout(old_timeout)
                except OSError:
                    pass

    def _recv_exact(self, sock: socket.socket, length: int) -> bytes:
        chunks: list[bytes] = []
        remaining = length

        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                with self._lock:
                    if self._socket is sock:
                        self._socket = None
                raise ConnectionError("connection closed")
            chunks.append(chunk)
            remaining -= len(chunk)

        return b"".join(chunks)
