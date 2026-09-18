import unittest

from chronocat_ground.pid_profiles import PID_PROFILES, profile_by_name


class PidProfileTests(unittest.TestCase):
    def test_default_profile_matches_firmware_defaults_for_all_heaters(self) -> None:
        profile = profile_by_name("Default")

        self.assertEqual(len(profile.gains_by_heater), 12)
        for heater_id in range(12):
            self.assertEqual(profile.gains_for(heater_id).kp, 2.25)
            self.assertEqual(profile.gains_for(heater_id).ki, 0.051)
            self.assertEqual(profile.gains_for(heater_id).kd, 4.0)

    def test_profiles_are_named_data(self) -> None:
        self.assertEqual([profile.name for profile in PID_PROFILES], ["Default"])


if __name__ == "__main__":
    unittest.main()
