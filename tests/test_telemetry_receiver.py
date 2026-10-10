import os
import socket
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication

from chronocat_ground import telemetry_receiver
from chronocat_ground.telemetry_receiver import TelemetryReceiver
from tests.test_protocol import combined_telemetry_packet_v5


def free_udp_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class TelemetryReceiverTest(unittest.TestCase):
    def test_a_socket_error_reopens_the_port_instead_of_stopping(self) -> None:
        app = QCoreApplication.instance() or QCoreApplication([])
        telemetry_receiver.REOPEN_DELAY_S = 0.1
        port = free_udp_port()
        receiver = TelemetryReceiver(port)
        packets, errors = [], []
        receiver.packet_received.connect(lambda packet, *_: packets.append(packet))
        receiver.receive_error.connect(errors.append)
        receiver.start()
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            def wait_for(condition, timeout=3.0):
                deadline = time.monotonic() + timeout
                while not condition() and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.02)
                return condition()

            self.assertTrue(wait_for(lambda: receiver._socket is not None))
            # Break the socket under the running receiver, as a network fault would.
            receiver._socket.close()
            self.assertTrue(wait_for(lambda: errors))
            self.assertIn("reopening", errors[0])

            def send_until_received():
                sender.sendto(combined_telemetry_packet_v5(), ("127.0.0.1", port))
                return bool(packets)
            self.assertTrue(wait_for(send_until_received))
        finally:
            receiver.stop()
            receiver.wait(2000)
            sender.close()
            telemetry_receiver.REOPEN_DELAY_S = 1.0
        self.assertFalse(receiver.isRunning())


if __name__ == "__main__":
    unittest.main()
