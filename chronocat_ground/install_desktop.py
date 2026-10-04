"""Register the ground station with a Linux desktop environment."""

from __future__ import annotations

import os
from pathlib import Path
import sys


DESKTOP_FILE_NAME = "chronocat_ground.desktop"


def _exec_argument(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'"{escaped}"'


def register_desktop_entry(data_home: Path | None = None) -> Path:
    """Install a per-user launcher for terminal-launched and menu-launched windows."""
    icon = Path(__file__).with_name("CHRONO-CAT_logo.png").resolve()
    if not icon.is_file():
        raise FileNotFoundError(f"Ground station icon is missing: {icon}")

    if data_home is None:
        data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    applications = data_home / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    desktop_file = applications / DESKTOP_FILE_NAME
    desktop_file.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=CHRONO-CAT Ground Station\n"
        f"Exec={_exec_argument(sys.executable)} -m chronocat_ground.main\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "StartupNotify=true\n"
        "StartupWMClass=chronocat_ground\n"
        "Categories=Science;Education;\n",
        encoding="utf-8",
    )
    return desktop_file


def main() -> int:
    if not sys.platform.startswith("linux"):
        print("Desktop registration is only needed on Linux.", file=sys.stderr)
        return 1
    print(f"Installed {register_desktop_entry()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
