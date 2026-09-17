from __future__ import annotations

import unittest

from chronocat_ground.heater_test import HeaterTestSession
from chronocat_ground.protocol import (
    CHARACTERIZATION_BASELINE,
    CHARACTERIZATION_HEATING,
    COMMAND_CHARACTERIZATION_START,
    CommandResponse,
    TelemetryPacket,
)


def packet(timestamp: int, valid_mask: int = 0xFFF) -> TelemetryPacket:
    return TelemetryPacket(
        version=2,
        message_type=1,
        flags=0,
        payload_length=0,
        timestamp=timestamp,
        counter=timestamp,
        health_code=0,
        temperature_valid_mask=valid_mask,
        temperatures=tuple(2000 + index for index in range(12)),
        heater_duty_permille=0,
        os_adc_valid_mask=0,
        os_adc_readings=tuple(),
        geiger_readings=tuple(),
    )


class HeaterTestSessionTests(unittest.TestCase):
    def test_start_response_and_temperature_history(self) -> None:
        session = HeaterTestSession()
        session.prepare_start(4, 12)
        session.apply_command_response(
            CommandResponse(
                status=0,
                command=COMMAND_CHARACTERIZATION_START,
                arg1=CHARACTERIZATION_BASELINE,
                arg2=12,
            )
        )

        session.append_packet(packet(1000, valid_mask=0x001))
        session.apply_status_response(
            CommandResponse(0, COMMAND_CHARACTERIZATION_START, CHARACTERIZATION_HEATING, 12)
        )
        session.append_packet(packet(1500, valid_mask=0x001))

        self.assertEqual(session.selected_heater, 4)
        self.assertEqual(session.state, CHARACTERIZATION_HEATING)
        self.assertEqual(list(session.temperature_histories[0]), [(0.0, 20.0), (0.5, 20.0)])
        self.assertEqual(len(session.temperature_histories[1]), 0)
        self.assertEqual(list(session.duty_history), [(0.0, 0.0), (0.5, 12.0)])

    def test_start_resets_previous_history(self) -> None:
        session = HeaterTestSession()
        session.prepare_start(0, 1)
        session.apply_command_response(CommandResponse(0, COMMAND_CHARACTERIZATION_START, 1, 1))
        session.append_packet(packet(10))
        self.assertTrue(session.temperature_histories[0])

        session.prepare_start(1, 2)

        self.assertFalse(session.temperature_histories[0])
        self.assertEqual(session.selected_heater, 1)
        self.assertEqual(session.requested_duty, 2)

    def test_timestamp_wrap_keeps_elapsed_time_positive(self) -> None:
        session = HeaterTestSession()
        session.prepare_start(0, 1)
        session.apply_command_response(CommandResponse(0, COMMAND_CHARACTERIZATION_START, 1, 1))
        session.append_packet(packet(0xFFFFFF00))
        session.append_packet(packet(0x000000F4))

        self.assertAlmostEqual(session.elapsed_s or 0.0, 0.5)


if __name__ == "__main__":
    unittest.main()
