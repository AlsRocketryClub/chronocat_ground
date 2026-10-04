from pathlib import Path
import sys
import tempfile
import unittest

from chronocat_ground.install_desktop import register_desktop_entry


class DesktopLauncherTests(unittest.TestCase):
    def test_installed_launcher_matches_the_terminal_app_and_icon(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            launcher = register_desktop_entry(Path(directory))
            contents = launcher.read_text(encoding="utf-8")

            self.assertEqual(launcher.name, "chronocat_ground.desktop")
            self.assertIn(f'Exec="{sys.executable}" -m chronocat_ground.main', contents)
            self.assertIn("StartupWMClass=chronocat_ground\n", contents)
            icon = next(line.removeprefix("Icon=") for line in contents.splitlines() if line.startswith("Icon="))
            self.assertTrue(Path(icon).is_file())


if __name__ == "__main__":
    unittest.main()
