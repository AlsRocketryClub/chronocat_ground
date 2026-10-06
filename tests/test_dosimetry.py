import tempfile
import unittest
from pathlib import Path

from chronocat_ground.dosimetry import SessionDose, dose_rate_usv_h, is_plausible_xder, user_dose_usv
from chronocat_ground.telemetry_db import TelemetryDb, archive_database

GMC1_XDER = 3.34113e-06  # decoded from the GMC1 calibration structure


class DosimetryTest(unittest.TestCase):
    def test_dose_rate_follows_manufacturer_formula(self) -> None:
        # 0.02 net cps is a typical ground reading: about 0.067 µSv/h background.
        self.assertAlmostEqual(dose_rate_usv_h(0.02, GMC1_XDER), 0.0668226, places=6)

    def test_session_dose_integrates_rate_and_skips_gaps(self) -> None:
        dose = SessionDose()
        for t in range(0, 3601):  # one hour at a steady 1 cps
            dose.record(0, float(t), 1.0, GMC1_XDER)
        self.assertAlmostEqual(dose.dose_usv[0], GMC1_XDER * 1e6, places=9)
        dose.record(0, 3700.0, 1.0, GMC1_XDER)  # 99 s outage: not integrated
        self.assertAlmostEqual(dose.covered_s[0], 3600.0)
        dose.record(1, 0.0, 1.0, None)  # unknown coefficient: nothing to add
        dose.record(1, 1.0, 1.0, None)
        self.assertEqual(dose.dose_usv[1], 0.0)

    def test_user_dose_follows_manufacturer_formula(self) -> None:
        # Accumulated dose (Sv) = counts × xDER / 3600.
        self.assertAlmostEqual(user_dose_usv(3600.0, GMC1_XDER), GMC1_XDER * 1e6)

    def test_rejects_unusable_coefficients(self) -> None:
        self.assertTrue(is_plausible_xder(GMC1_XDER))
        for bad in (0.0, -2.78498384e-21, float("nan"), float("inf")):
            self.assertFalse(is_plausible_xder(bad))

    def test_coefficients_persist_in_the_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.db"
            db = TelemetryDb(path)
            db.save_xder(0, GMC1_XDER, 1000.0)
            db.save_xder(0, GMC1_XDER, 2000.0)
            db.close()
            db = TelemetryDb(path)
            self.assertEqual(db.load_xder(), {0: (GMC1_XDER, 2000.0)})
            db.close()
            archive_database(path)
            self.assertEqual(TelemetryDb(path).load_xder(), {})
