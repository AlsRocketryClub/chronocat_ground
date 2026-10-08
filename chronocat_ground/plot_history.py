from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import sqlite3

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from .protocol import AD7177_BIPOLAR_MIDSCALE, AD7177_VREF_VOLTS


HISTORY_BATCH_SIZE = 8192

_SERIES = {
    "adc": ("adc", "slot", "raw24"),
    "geiger": ("geiger", "counter_id", "dose_rate_cps"),
    "temperature": ("temperature", "slot", "temperature_c"),
    "duty": ("heater", "heater_id", "duty_permille"),
}
# Averages over the heaters, matching the heating page: valid heater-sensor
# temperatures (sensors 0-11; missing readings are never stored) and all duties.
_AVERAGES = {
    "temperature_avg": ("temperature", "temperature_c", "slot < 12"),
    "duty_avg": ("heater", "duty_permille", "1"),
}


def read_history_batch(
    database_path: str | Path,
    kind: str,
    channel: int,
    after_rowid: int,
    raw_mode: bool = False,
) -> tuple[int, np.ndarray, bool]:
    """Read only unseen rows using a worker-owned, read-only SQLite connection.

    Row IDs, rather than device timestamps, survive board resets and distinguish
    samples received at the same wall-clock time. The slot/counter indexes also
    index rowid implicitly, so incremental reads do not scan the old history.
    """
    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=1.0)
    try:
        if kind in _AVERAGES:
            # One point per packet: every row of a packet shares its receive time.
            table, value, where = _AVERAGES[kind]
            rows = connection.execute(
                f"SELECT MAX(rowid), received_wall, AVG({value}) FROM {table} "
                f"WHERE {where} AND rowid > ? AND received_wall IS NOT NULL "
                "GROUP BY received_wall ORDER BY 1 LIMIT ?",
                (after_rowid, HISTORY_BATCH_SIZE),
            ).fetchall()
        else:
            table, key, value = _SERIES[kind]
            rows = connection.execute(
                f"SELECT rowid, received_wall, {value} FROM {table} "
                f"WHERE {key} = ? AND rowid > ? AND received_wall IS NOT NULL "
                "ORDER BY rowid LIMIT ?",
                (channel, after_rowid, HISTORY_BATCH_SIZE),
            ).fetchall()
    finally:
        connection.close()
    if not rows:
        return after_rowid, np.empty((0, 2)), False
    points = np.asarray([(wall, value) for _rowid, wall, value in rows], dtype=float)
    if kind in ("duty", "duty_avg"):
        points[:, 1] /= 10.0  # per mille to percent
    if kind == "adc" and not raw_mode:
        points[:, 1] = (
            (points[:, 1] - AD7177_BIPOLAR_MIDSCALE)
            / AD7177_BIPOLAR_MIDSCALE * AD7177_VREF_VOLTS
        )
    return rows[-1][0], points, len(rows) == HISTORY_BATCH_SIZE


class PlotHistoryLoader(QObject):
    """Bounded background reads with GUI-thread delivery and one request in flight."""

    batch_ready = Signal(object)
    caught_up = Signal()
    failed = Signal(str)

    def __init__(self, database_path, kind: str, channel: int,
                 raw_mode: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._arguments = (database_path, kind, channel)
        self._raw_mode = raw_mode
        self._rowid = 0
        self._closed = False
        self._future: Future | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plot-history")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        if not self._closed:
            self._timer.start(0)

    def _poll(self) -> None:
        if self._closed:
            return
        if self._future is None:
            self._future = self._executor.submit(
                read_history_batch, *self._arguments, self._rowid, self._raw_mode
            )
        if not self._future.done():
            self._timer.start(50)
            return
        future, self._future = self._future, None
        try:
            self._rowid, points, more = future.result()
        except Exception as exc:
            self.failed.emit(str(exc))
            self._timer.start(2000)
            return
        if len(points):
            self.batch_ready.emit(points)
        if not more:
            self.caught_up.emit()
        self._timer.start(0 if more else 1000)

    def close(self) -> None:
        self._closed = True
        self._timer.stop()
        # A running bounded read closes its own connection; no UI-thread wait.
        self._executor.shutdown(wait=False, cancel_futures=True)
