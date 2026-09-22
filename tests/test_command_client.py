from __future__ import annotations

import socket
import threading
import unittest

from chronocat_ground.command_client import CommandClient


class CommandClientConnectionCheckTests(unittest.TestCase):
    def _start_loopback_server(self) -> tuple[socket.socket, int]:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        return server, server.getsockname()[1]

    def test_check_connection_true_while_peer_stays_up(self) -> None:
        server, port = self._start_loopback_server()
        accepted: list[socket.socket] = []
        thread = threading.Thread(target=lambda: accepted.append(server.accept()[0]))
        thread.start()

        client = CommandClient()
        try:
            client.connect("127.0.0.1", port, timeout=2.0)
            thread.join(timeout=2.0)

            self.assertTrue(client.connected)
            self.assertTrue(client.check_connection())
            self.assertTrue(client.connected)
        finally:
            client.disconnect()
            for peer in accepted:
                peer.close()
            server.close()

    def test_check_connection_false_after_peer_closes(self) -> None:
        server, port = self._start_loopback_server()
        accepted: list[socket.socket] = []
        thread = threading.Thread(target=lambda: accepted.append(server.accept()[0]))
        thread.start()

        client = CommandClient()
        try:
            client.connect("127.0.0.1", port, timeout=2.0)
            thread.join(timeout=2.0)
            self.assertTrue(client.connected)

            accepted[0].close()

            # Give the FIN a moment to arrive locally.
            import time

            deadline = time.monotonic() + 2.0
            still_connected = True
            while time.monotonic() < deadline:
                still_connected = client.check_connection()
                if not still_connected:
                    break
                time.sleep(0.05)

            self.assertFalse(still_connected)
            self.assertFalse(client.connected)
        finally:
            client.disconnect()
            server.close()


if __name__ == "__main__":
    unittest.main()
