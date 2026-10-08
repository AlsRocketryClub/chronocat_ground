import struct
import unittest
from datetime import datetime

from chronocat_ground.health_model import (
    ERROR,
    OK,
    UNKNOWN,
    WARNING,
    HealthEventLog,
    PacketLossTracker,
    active_issues,
    evaluate_health,
    format_rate,
    link_status,
)
from chronocat_ground.protocol import parse_telemetry_packet
from tests.test_protocol import combined_telemetry_packet_v5

LIVE = link_status(uplink_connected=True, packet_age_s=0.5)


def packet(
    temps=None, temp_mask=0xFFFF, adc_mask=0x0FFF, geiger_2_flags=0, flags=0x0007, uptime_ms=1234,
    health_code=0,
):
    data = bytearray(combined_telemetry_packet_v5())
    struct.pack_into(">H", data, 6, flags)
    struct.pack_into(">I", data, 10, uptime_ms)
    data[18] = health_code
    struct.pack_into(">H", data, 19, temp_mask)
    struct.pack_into(">16h", data, 21, *(temps or [2500] * 16))
    struct.pack_into(">H", data, 53, adc_mask)
    struct.pack_into(">H", data, 103 + 2, 0)
    struct.pack_into(">H", data, 103 + 34 + 2, geiger_2_flags)
    return parse_telemetry_packet(bytes(data))


def items_for(decoded, link=LIVE):
    return {item.key: item for item in evaluate_health(decoded.standard, decoded.pid, link)}


class HealthModelTest(unittest.TestCase):
    def test_over_limit_temperature_is_an_error_naming_sensor_and_heater(self) -> None:
        temps = [2500] * 16
        temps[4] = 6650  # F1_U1
        items = items_for(packet(temps=temps))
        self.assertEqual(items["temp:4"].state, ERROR)
        self.assertEqual(active_issues(tuple(items.values()))[0].text,
                         "F1_U1 (H1): 66.5 °C, over the 65 °C limit")

    def test_dead_adc_chips_are_one_issue_each(self) -> None:
        issues = [issue.text for issue in active_issues(tuple(items_for(packet(adc_mask=0x0FC0)).values()))]
        self.assertIn("ADC0 not responding (all 3 channels)", issues)
        self.assertIn("ADC1 not responding (all 3 channels)", issues)
        self.assertFalse(any("CH0 (" in text for text in issues))

    def test_geiger_flags_follow_the_corrected_table(self) -> None:
        # A statistics reset is normal, and the high byte is a counter, not flags.
        self.assertEqual(items_for(packet(geiger_2_flags=0x0310))["geiger:1"].state, OK)
        for notice in (0x08, 0x20, 0x40, 0x80):
            self.assertEqual(items_for(packet(geiger_2_flags=notice))["geiger:1"].state, WARNING)
        hv = items_for(packet(geiger_2_flags=0x01))["geiger:1"]
        self.assertEqual((hv.state, hv.reason), (ERROR, "HV undervoltage after pumping"))

    def test_geiger_tile_value_stays_short_with_details_in_tooltip(self) -> None:
        geiger = items_for(packet(geiger_2_flags=0x10))["geiger:1"]
        self.assertTrue(geiger.value.endswith(" cps"))
        self.assertLessEqual(len(geiger.value), 10)
        self.assertIn("HV", geiger.detail)
        self.assertIn("statistics reset", geiger.detail)
        self.assertEqual(
            [format_rate(rate) for rate in (12345.678, 123.45, 9.876, 0.0123)],
            ["12346", "123.5", "9.88", "0.0123"],
        )

    def test_forced_heater_is_a_warning_even_with_a_good_sensor(self) -> None:
        data = bytearray(combined_telemetry_packet_v5())
        heater_records = 18 + 1 + 2 + 32 + 2 + 48 + 68 + 4
        struct.pack_into(">H", data, heater_records + 2 * 35 + 4, 120)
        data[heater_records + 2 * 35 + 6] = 3  # result FORCED
        heater = items_for(parse_telemetry_packet(bytes(data)))["heater:2"]
        self.assertEqual(heater.state, WARNING)
        self.assertEqual(heater.reason, "forced override: sensor protection off")

    def test_one_working_ambient_sensor_is_enough(self) -> None:
        # Only F2_U6 (sensor 15) fitted; the board still reports a sensor error.
        items = items_for(packet(temp_mask=0x0FFF | (1 << 15), health_code=2))
        self.assertEqual(items["temp:12"].state, UNKNOWN)
        self.assertEqual(items["temp:15"].state, OK)
        self.assertEqual(items["firmware"].state, OK)
        self.assertEqual(active_issues(tuple(items.values())), [])

    def test_no_working_ambient_sensor_is_one_warning(self) -> None:
        items = items_for(packet(temp_mask=0x0FFF, health_code=2))
        texts = [issue.text for issue in active_issues(tuple(items.values()))]
        self.assertEqual(texts, ["No ambient sensor responding"])
        self.assertEqual(items["temp:15"].state, WARNING)

    def test_missing_heater_sensor_still_warns_with_ambient_working(self) -> None:
        items = items_for(packet(temp_mask=0xFFFF & ~(1 << 4), health_code=2))
        self.assertEqual(items["temp:4"].state, WARNING)
        self.assertEqual(items["firmware"].state, WARNING)

    def test_watchdog_reset_flag_is_reported(self) -> None:
        reset = items_for(packet(flags=0x0007 | (1 << 4)))["reset"]
        self.assertEqual((reset.state, reset.value), (WARNING, "watchdog"))

    def test_stale_downlink_is_one_issue_and_packet_items_become_unknown(self) -> None:
        stale = link_status(uplink_connected=True, packet_age_s=12.0)
        items = items_for(packet(adc_mask=0), stale)
        self.assertEqual(items["temp:0"].state, UNKNOWN)
        self.assertEqual([issue.text for issue in active_issues(tuple(items.values()))],
                         ["Downlink: no packet for 12.0 s"])

    def test_event_log_records_loss_recovery_and_restart_once(self) -> None:
        log = HealthEventLog()
        now = datetime(2026, 10, 7, 14, 0, 0)
        log.update(tuple(items_for(packet()).values()), now, 5000)
        log.update(tuple(items_for(packet(temp_mask=0xFFFF & ~(1 << 7))).values()), now, 6000)
        log.update(tuple(items_for(packet(temp_mask=0xFFFF & ~(1 << 7))).values()), now, 7000)
        # Stale data neither recovers nor loses anything.
        stale = link_status(uplink_connected=True, packet_age_s=9.0)
        log.update(tuple(items_for(packet(), stale).values()), now)
        log.update(tuple(items_for(packet()).values()), now, 1000)
        texts = [event.text for event in log.events]
        self.assertEqual(texts, [
            "F2_U4 (H10): no reading",
            "H10 (F2_U4): no sensor reading; held off",
            "Downlink: no packet for 9.0 s",
            "Board restarted (uptime 1s)",
            "Downlink ok (receiving)",
            "F2_U4 (H10) ok (25.0 °C)",
            "H10 (F2_U4) ok (4%)",
        ])


class PacketLossTrackerTest(unittest.TestCase):
    def test_counts_gaps_but_not_board_restarts(self) -> None:
        tracker = PacketLossTracker()
        for counter in (10, 11, 14, 15, 2, 3):  # 12, 13 lost; then a restart
            tracker.record(counter)
        self.assertEqual((tracker.received, tracker.lost), (6, 2))
        self.assertAlmostEqual(tracker.loss_percent, 25.0)
