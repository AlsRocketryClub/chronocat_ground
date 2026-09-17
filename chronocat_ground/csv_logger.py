from __future__ import annotations

import csv
import errno
import os
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import TextIO

from .csv_schema import (
    CSV_MODE_FULL,
    CSV_MODE_GEIGER_ONLY,
    GEIGER_ONLY_CSV_FIELDS,
    CombinedTelemetryPacket,
    PidTelemetryPacket,
    TelemetryPacket,
    combined_packet_to_row,
    csv_fieldnames,
    packet_to_geiger_rows,
    packet_to_row,
    pid_packet_to_row,
)


def system_boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip()
    except OSError:
        return "unknown"
    return value.replace("-", "")[:8] or "unknown"


def _now() -> datetime:
    # Resolve through the facade so existing callers can patch its clock.
    from . import telemetry_csv

    return telemetry_csv.datetime.now()


def _uuid4():
    # Resolve through the facade so existing callers can patch session IDs.
    from . import telemetry_csv

    return telemetry_csv.uuid4()


def default_output_path(
    directory: Path | None = None,
    *,
    boot_id: str | None = None,
    session_id: str | None = None,
) -> Path:
    timestamp = _now().strftime("%Y%m%d_%H%M%S")
    boot = boot_id or system_boot_id()
    session = session_id or _uuid4().hex[:8]
    return (directory or Path()).joinpath(
        f"telemetry_{timestamp}_{boot}_{session}.csv"
    )


class _FsyncWorker:
    """Perform durable file synchronization without blocking the Qt thread."""

    def __init__(self, queue_size: int = 32) -> None:
        self._queue: queue.Queue[int | None] = queue.Queue(maxsize=queue_size)
        self.error: Exception | None = None
        self._thread = threading.Thread(target=self._run, name="csv-fsync", daemon=True)
        self._thread.start()

    def submit(self, file_descriptor: int) -> None:
        if self.error is not None:
            raise OSError("CSV durability worker failed") from self.error
        try:
            self._queue.put_nowait(file_descriptor)
        except queue.Full:
            try:
                os.fsync(file_descriptor)
            finally:
                os.close(file_descriptor)

    def _run(self) -> None:
        while True:
            file_descriptor = self._queue.get()
            try:
                if file_descriptor is None:
                    return
                os.fsync(file_descriptor)
            except Exception as exc:
                self.error = exc
                while True:
                    try:
                        pending_fd = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    else:
                        if pending_fd is not None:
                            os.close(pending_fd)
                        self._queue.task_done()
                return
            finally:
                if file_descriptor is not None:
                    os.close(file_descriptor)
                self._queue.task_done()

    def close(self) -> None:
        if self._thread.is_alive():
            self._queue.put(None)
            self._queue.join()
            self._thread.join(timeout=5.0)
        else:
            while True:
                try:
                    pending_fd = self._queue.get_nowait()
                except queue.Empty:
                    break
                else:
                    if pending_fd is not None:
                        os.close(pending_fd)
                    self._queue.task_done()
        if self.error is not None:
            raise OSError("CSV durability worker failed") from self.error


class TelemetryCsvLogger:
    def __init__(
        self,
        path: Path | None = None,
        mode: str = CSV_MODE_FULL,
        *,
        overwrite: bool = False,
        durable: bool = True,
        background_sync: bool = False,
    ) -> None:
        if mode not in (CSV_MODE_FULL, CSV_MODE_GEIGER_ONLY):
            raise ValueError(f"unsupported CSV mode {mode!r}")
        if overwrite and path is None:
            raise ValueError("overwrite requires an explicit output path")
        self.automatic_path = path is None
        self.boot_id = system_boot_id()
        self.session_id = _uuid4().hex[:8]
        self.path = path or default_output_path(
            boot_id=self.boot_id, session_id=self.session_id
        )
        self.mode = mode
        self.overwrite = overwrite
        self.durable = durable
        self.background_sync = background_sync and durable
        self._sync_worker: _FsyncWorker | None = None
        self.file: TextIO | None = None
        self.writer: csv.DictWriter | None = None
        self.packet_count = 0
        self.last_geiger_samples: dict[int, tuple[object, ...]] = {}
        self.pending_packets: dict[
            tuple[int, int], dict[str, object]
        ] = {}

    @property
    def active(self) -> bool:
        return self.file is not None

    def start(self) -> None:
        if self.file is not None:
            return
        self.last_geiger_samples.clear()
        self.pending_packets.clear()
        self._create_parent_directory()
        while True:
            try:
                self.file = self.path.open(
                    "w" if self.overwrite else "x",
                    newline="",
                    encoding="utf-8",
                )
            except FileExistsError:
                if not self.automatic_path:
                    raise
                self.session_id = _uuid4().hex[:8]
                self.path = default_output_path(
                    self.path.parent,
                    boot_id=self.boot_id,
                    session_id=self.session_id,
                )
                continue
            break
        fieldnames = (
            GEIGER_ONLY_CSV_FIELDS
            if self.mode == CSV_MODE_GEIGER_ONLY
            else csv_fieldnames()
        )
        try:
            if self.background_sync:
                self._sync_worker = _FsyncWorker()
            self.writer = csv.DictWriter(self.file, fieldnames=fieldnames)
            self.writer.writeheader()
            self._sync_file(force=True)
            self._sync_directory(self.path.parent)
        except Exception:
            if self._sync_worker is not None:
                self._sync_worker.close()
                self._sync_worker = None
            self.file.close()
            self.file = None
            self.writer = None
            raise

    def write_packet(
        self,
        packet: TelemetryPacket | PidTelemetryPacket | CombinedTelemetryPacket,
        source: tuple[str, int] | str,
        received_at: datetime | None = None,
    ) -> None:
        if isinstance(packet, CombinedTelemetryPacket):
            if self.file is None or self.writer is None:
                raise RuntimeError("CSV logger is not active")
            timestamp = received_at or _now()
            if self.mode == CSV_MODE_GEIGER_ONLY:
                self._write_geiger_rows(packet.standard, timestamp)
                return
            else:
                self.writer.writerow(combined_packet_to_row(packet, timestamp, source))
            self._sync_file()
            self.packet_count += 1
            return
        if isinstance(packet, PidTelemetryPacket):
            self.write_pid_packet(packet, source, received_at)
            return
        if self.file is None or self.writer is None:
            raise RuntimeError("CSV logger is not active")
        timestamp = received_at or _now()
        if self.mode == CSV_MODE_GEIGER_ONLY:
            self._write_geiger_rows(packet, timestamp)
            return

        self._queue_packet(
            (packet.counter, packet.timestamp),
            "standard",
            packet_to_row(packet, timestamp, source),
        )

    def _write_geiger_rows(self, packet: TelemetryPacket, timestamp: datetime) -> None:
        if self.file is None or self.writer is None:
            raise RuntimeError("CSV logger is not active")
        rows = packet_to_geiger_rows(packet, timestamp)
        rows = [
            row
            for row in rows
            if self.last_geiger_samples.get(int(row["counter_id"]))
            != tuple(row[field] for field in GEIGER_ONLY_CSV_FIELDS[2:])
        ]
        if not rows:
            return
        self.writer.writerows(rows)
        for row in rows:
            self.last_geiger_samples[int(row["counter_id"])] = tuple(
                row[field] for field in GEIGER_ONLY_CSV_FIELDS[2:]
            )
        self._sync_file()
        self.packet_count += 1

    def write_pid_packet(
        self,
        packet: PidTelemetryPacket,
        source: tuple[str, int] | str,
        received_at: datetime | None = None,
    ) -> None:
        if self.mode == CSV_MODE_GEIGER_ONLY:
            return
        timestamp = received_at or _now()
        self._queue_packet(
            (packet.counter, packet.timestamp),
            "pid",
            pid_packet_to_row(packet, timestamp, source),
        )

    def _queue_packet(
        self,
        key: tuple[int, int],
        packet_kind: str,
        row: dict[str, object],
    ) -> None:
        if self.file is None or self.writer is None:
            raise RuntimeError("CSV logger is not active")
        entry = self.pending_packets.setdefault(key, {})
        entry[packet_kind] = row
        if len(entry) == 2:
            self._write_pending_packet(key)
        while len(self.pending_packets) > 2:
            oldest_key = min(self.pending_packets)
            self._write_pending_packet(oldest_key)

    def _write_pending_packet(self, key: tuple[int, int]) -> None:
        entry = self.pending_packets.pop(key, {})
        row = {field: "" for field in csv_fieldnames()}
        for packet_row in entry.values():
            row.update(packet_row)  # type: ignore[arg-type]
        if self.writer is None:
            raise RuntimeError("CSV logger is not active")
        self.writer.writerow(row)
        self._sync_file()
        self.packet_count += 1

    def _sync_file(self, *, force: bool = False) -> None:
        if self.file is None:
            return
        self.file.flush()
        if self.durable:
            if self._sync_worker is not None and not force:
                self._sync_worker.submit(os.dup(self.file.fileno()))
            else:
                os.fsync(self.file.fileno())

    def _create_parent_directory(self) -> None:
        missing: list[Path] = []
        directory = self.path.parent
        while not directory.exists():
            missing.append(directory)
            directory = directory.parent
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for created in reversed(missing):
            self._sync_directory(created)
            self._sync_directory(created.parent)

    def _sync_directory(self, directory: Path) -> None:
        if not self.durable:
            return
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            try:
                os.fsync(directory_fd)
            except OSError as exc:
                unsupported = {errno.EINVAL, errno.ENOTSUP}
                if hasattr(errno, "EOPNOTSUPP"):
                    unsupported.add(errno.EOPNOTSUPP)
                if exc.errno not in unsupported:
                    raise
        finally:
            os.close(directory_fd)

    def stop(self) -> None:
        if self.file is None:
            return
        for key in sorted(self.pending_packets):
            self._write_pending_packet(key)
        self._sync_file()
        if self._sync_worker is not None:
            self._sync_worker.close()
            self._sync_worker = None
        self.file.close()
        self.file = None
        self.writer = None
