"""Interface size per screen, applied through Qt's own scale factors.

Qt scales text, widgets, plots and images uniformly when QT_SCREEN_SCALE_FACTORS
is set before the application starts, so nothing in the layout code changes.
It cannot be changed while running: a new size applies after a restart.

Automatic sizing keeps text about as large as on the reference laptop screen:
a screen that packs more logical pixels into an inch draws everything smaller,
so it is scaled up by the ratio. It never shrinks below 100 %.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import subprocess
import sys

from PySide6.QtCore import QSettings

# Logical pixels per inch of the MacBook's built-in display (1470 px over 291 mm).
REFERENCE_PPI = 128.0
MIN_PERCENT = 75
MAX_PERCENT = 200
STEP_PERCENT = 5
_PLAUSIBLE_PPI = (40.0, 400.0)
_AUTO_SETTING = "display/auto"
_SCALE_GROUP = "display/scale"
_PROBE_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class ScreenInfo:
    name: str
    width_px: int  # logical pixels, before any Qt scale factor
    width_mm: float


def settings() -> QSettings:
    # The same file as the rest of the ground station's settings; usable
    # before the QApplication exists.
    return QSettings(QSettings.IniFormat, QSettings.UserScope, "Chronocat", "Ground Station")


def snap_percent(percent: float) -> int:
    snapped = int(round(percent / STEP_PERCENT) * STEP_PERCENT)
    return max(MIN_PERCENT, min(MAX_PERCENT, snapped))


def points_per_inch(screen: ScreenInfo) -> float | None:
    if screen.width_mm <= 0 or screen.width_px <= 0:
        return None
    ppi = screen.width_px / (screen.width_mm / 25.4)
    low, high = _PLAUSIBLE_PPI
    return ppi if low <= ppi <= high else None


def auto_percent(screen: ScreenInfo) -> int:
    ppi = points_per_inch(screen)
    if ppi is None:
        return 100  # unknown or nonsense size: leave the screen alone
    return max(100, snap_percent(100.0 * ppi / REFERENCE_PPI))


def _key(screen_name: str) -> str:
    # Slashes would become nested groups in the settings file.
    return f"{_SCALE_GROUP}/{screen_name.replace('/', '_').replace(chr(92), '_')}"


def auto_enabled(store: QSettings) -> bool:
    value = store.value(_AUTO_SETTING, True)
    return value if isinstance(value, bool) else str(value).lower() == "true"


def set_auto_enabled(store: QSettings, enabled: bool) -> None:
    store.setValue(_AUTO_SETTING, enabled)


def stored_percent(store: QSettings, screen_name: str) -> int | None:
    value = store.value(_key(screen_name))
    try:
        return snap_percent(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def set_stored_percent(store: QSettings, screen_name: str, percent: int | None) -> None:
    if percent is None:
        store.remove(_key(screen_name))
    else:
        store.setValue(_key(screen_name), snap_percent(percent))


def default_percent(store: QSettings, screen: ScreenInfo) -> int:
    """What a screen gets without a size of its own."""
    return auto_percent(screen) if auto_enabled(store) else 100


def effective_percent(store: QSettings, screen: ScreenInfo) -> int:
    stored = stored_percent(store, screen.name)
    return stored if stored is not None else default_percent(store, screen)


def scale_factors_env(store: QSettings, screens: list[ScreenInfo]) -> str | None:
    """QT_SCREEN_SCALE_FACTORS for these screens, or None when all are 100 %."""
    parts = []
    for screen in screens:
        if ";" in screen.name or "=" in screen.name or not screen.name:
            continue  # cannot be expressed in the variable; stays at 100 %
        percent = effective_percent(store, screen)
        if percent != 100:
            parts.append(f"{screen.name}={percent / 100:g}")
    return ";".join(parts) or None


def _has_stored_sizes(store: QSettings) -> bool:
    store.beginGroup(_SCALE_GROUP)
    try:
        return bool(store.childKeys())
    finally:
        store.endGroup()


def probe_screens() -> list[ScreenInfo]:
    """List the connected screens from a short-lived child process.

    The scale factors must be set before this process creates its
    QApplication, so the screens are read by a separate one.
    """
    environment = dict(os.environ)
    environment.pop("QT_SCALE_FACTOR", None)
    environment.pop("QT_SCREEN_SCALE_FACTORS", None)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "chronocat_ground.display_scale", "--probe"],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S, env=environment,
        )
        rows = json.loads(result.stdout.strip().splitlines()[-1])
        return [ScreenInfo(str(row["name"]), int(row["width_px"]), float(row["width_mm"])) for row in rows]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError, KeyError, TypeError):
        return []


def apply_display_scaling() -> str | None:
    """Set Qt's per-screen scale factors; call before creating the QApplication.

    Returns the value set, or None. A scale factor chosen by hand in the
    environment always wins.
    """
    if "QT_SCREEN_SCALE_FACTORS" in os.environ or "QT_SCALE_FACTOR" in os.environ:
        return None
    store = settings()
    if not auto_enabled(store) and not _has_stored_sizes(store):
        return None  # everything at 100 %: skip the probe entirely
    value = scale_factors_env(store, probe_screens())
    if value:
        os.environ["QT_SCREEN_SCALE_FACTORS"] = value
    return value


def _print_screens() -> int:
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication(sys.argv[:1])
    print(json.dumps([
        {
            "name": screen.name(),
            "width_px": screen.geometry().width(),
            "width_mm": screen.physicalSize().width(),
        }
        for screen in app.screens()
    ]))
    return 0


if __name__ == "__main__":
    raise SystemExit(_print_screens() if "--probe" in sys.argv else 0)
