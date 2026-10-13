"""Drawn map of both instrument boards: sensors, heaters, sample pairs and ADC channels.

Everything shown comes from the same tables the rest of the ground station
uses, so the map cannot drift from the decoding. Spot positions follow the
temperature-board PCB (U5/U2 top, U0/U1 middle, U4/U3 bottom, J1 at the
bottom right); the ambient sensors are nudged just enough not to overlap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..heater_safety import MAX_SAFE_TEMPERATURE_C
from ..protocol import (
    AMBIENT_SENSOR_IDS,
    HEATER_SENSOR_IDS,
    TEMP_SENSOR_I2C_ADDRESSES,
    TEMP_SENSOR_LABELS,
    TEMP_SENSOR_MUX_SIDES,
    TelemetryPacket,
)
from ..sample_layout import SAMPLE_COLUMNS, SAMPLES, SampleChannel
from ..units import VOLTS

# Spot centres as fractions of the drawn board, keyed by label without the
# F1_/F2_ board prefix.
_SPOT_POSITIONS = {
    "U5": (0.20, 0.11),
    "U2": (0.80, 0.11),
    "U7": (0.20, 0.29),
    "U0": (0.20, 0.47),
    "U1": (0.80, 0.47),
    "U6": (0.50, 0.65),
    "U4": (0.20, 0.84),
    "U3": (0.80, 0.84),
}
_BOARD_BUSES = ("I2C4", "I2C1")

# Drawn height / width. The real PCB is ~2.2; the map is squashed vertically
# because the space between sensor rows carries no information.
_BOARD_ASPECT = 1.6
_HEADER_HEIGHT = 46.0
_FOOTER_HEIGHT = 140.0
_SPOT_WIDTH = 0.34
_SPOT_HEIGHT = 74.0
# Sample spots carry two more lines: the sample and how it is connected.
_SAMPLE_SPOT_HEIGHT = 104.0
_LINK = QColor("#1f5f99")

_INK = QColor("#111111")
_MUTED = QColor("#666666")
_BOARD_FILL = QColor("#f4f6f2")
_BOARD_EDGE = QColor("#8a8f86")
_PAIR_LINE = QColor("#9aa4b1")
_STATE_COLORS = {
    "valid": (QColor("#e7f4ea"), QColor("#2e7d32")),
    "hot": (QColor("#fdecea"), QColor("#c62828")),
    "invalid": (QColor("#eeeeee"), QColor("#9e9e9e")),
}


@dataclass(frozen=True)
class _Pair:
    number: int
    left_sensor: int
    right_sensor: int
    device_a: SampleChannel
    device_b: SampleChannel


@dataclass(frozen=True)
class _Board:
    number: int
    material: str
    bus: str
    pairs: tuple[_Pair, ...]
    sensor_ids: tuple[int, ...]

    @property
    def adc_text(self) -> str:
        adcs = sorted({channel.adc_index for pair in self.pairs for channel in (pair.device_a, pair.device_b)})
        return " + ".join(f"ADC{adc}" for adc in adcs)

    @property
    def heater_text(self) -> str:
        heaters = sorted(HEATER_SENSOR_IDS.index(sensor) for sensor in self.sensor_ids if sensor in HEATER_SENSOR_IDS)
        return f"H{heaters[0]}–H{heaters[-1]}"


def _build_boards() -> tuple[_Board, ...]:
    boards = []
    for number, (material, channels) in enumerate(SAMPLE_COLUMNS, start=1):
        pairs = []
        for pair_number in sorted({channel.pair for channel in channels}):
            devices = {channel.device: channel for channel in channels if channel.pair == pair_number}
            left, right = devices["a"].temperature_sensor_ids
            pairs.append(_Pair(pair_number, left, right, devices["a"], devices["b"]))
        prefix = f"F{number}_"
        sensor_ids = tuple(
            TEMP_SENSOR_LABELS.index(prefix + label) for label in _SPOT_POSITIONS
        )
        boards.append(_Board(number, material, _BOARD_BUSES[number - 1], tuple(pairs), sensor_ids))
    return tuple(boards)


BOARDS = _build_boards()


def _base_label(sensor_id: int) -> str:
    return TEMP_SENSOR_LABELS[sensor_id].split("_", 1)[1]


class BoardMapWidget(QWidget):
    """Both boards side by side, coloured by the latest temperature readings.

    Signal channels show their latest reading; clicking it asks for that
    channel's plot (channel_requested carries the ADC slot).
    """

    channel_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(760, 640)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self._temperatures: dict[int, float | None] = {}
        self._readings: dict[int, float | None] = {}
        # Set while showing stored values from before a ground restart.
        self._stored_wall: float | None = None
        self._reading_rects: list[tuple[QRectF, int]] = []

    def show_stored(self, temperatures: dict[int, float], volts: dict[int, float | None], wall: float) -> None:
        """Last stored values after a ground restart, marked as such until live data arrives."""
        self._temperatures = {sensor: temperatures.get(sensor) for sensor in range(len(TEMP_SENSOR_LABELS))}
        self._readings = dict(volts)
        self._stored_wall = wall
        self.update()

    def set_readings(self, packet: TelemetryPacket) -> None:
        """Latest volts per ADC slot; None when the reading is invalid or flagged."""
        self._readings = {
            reading.slot: (
                reading.voltage
                if packet.os_adc_valid(reading.slot) and not reading.has_error and reading.word != 0
                else None
            )
            for reading in packet.ad7177_readings
        }
        self.update()

    def _slot_at(self, position) -> int | None:
        for rect, slot in self._reading_rects:
            if rect.contains(position):
                return slot
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        slot = self._slot_at(event.position()) if event.button() == Qt.LeftButton else None
        if slot is not None:
            self.channel_requested.emit(slot)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        over = self._slot_at(event.position()) is not None
        self.setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def set_temperatures(self, packet: TelemetryPacket) -> None:
        self._stored_wall = None
        self._temperatures = {
            sensor_id: packet.temperature_c(sensor_id) for sensor_id in range(len(TEMP_SENSOR_LABELS))
        }
        self.update()

    def clear_temperatures(self) -> None:
        self._temperatures = {}
        self.update()

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return int(self._board_width(width) * _BOARD_ASPECT + _HEADER_HEIGHT + _FOOTER_HEIGHT)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def sizeHint(self):  # noqa: N802
        size = super().sizeHint()
        size.setHeight(self.heightForWidth(max(self.width(), self.minimumWidth())))
        return size

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        height = self.heightForWidth(self.width())
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)

    @staticmethod
    def _board_width(width: int) -> float:
        return min((width - 40) / 2, 440.0)

    def paintEvent(self, event) -> None:  # noqa: N802
        self._reading_rects = []
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        board_width = self._board_width(self.width())
        board_height = board_width * _BOARD_ASPECT
        gap = self.width() - 2 * board_width
        for index, board in enumerate(BOARDS):
            left = gap / 3 + index * (board_width + gap / 3)
            self._paint_board(painter, board, QRectF(left, _HEADER_HEIGHT, board_width, board_height))
        self._paint_footer(painter, QRectF(gap / 3, _HEADER_HEIGHT + board_height + 10, self.width() - 2 * gap / 3, _FOOTER_HEIGHT - 10))
        painter.end()

    def _paint_board(self, painter: QPainter, board: _Board, rect: QRectF) -> None:
        painter.setPen(_INK)
        painter.setFont(_font(12, bold=True))
        painter.drawText(
            QRectF(rect.left(), 0, rect.width(), 22), Qt.AlignLeft | Qt.AlignVCenter,
            f"BOARD {board.number} · {board.material}",
        )
        painter.setPen(_MUTED)
        painter.setFont(_font(10))
        painter.drawText(
            QRectF(rect.left(), 21, rect.width(), 20), Qt.AlignLeft | Qt.AlignVCenter,
            f"{board.adc_text}  ·  sensors on {board.bus}  ·  heaters {board.heater_text}",
        )

        painter.setPen(QPen(_BOARD_EDGE, 2))
        painter.setBrush(_BOARD_FILL)
        painter.drawRoundedRect(rect, 10, 10)

        spots = {sensor: self._spot_rect(rect, sensor) for sensor in board.sensor_ids}
        for pair in board.pairs:
            self._paint_pair(painter, pair, spots[pair.left_sensor], spots[pair.right_sensor])
        for sensor, spot in spots.items():
            self._paint_spot(painter, sensor, spot)

        connector = QRectF(rect.right() - 0.30 * rect.width(), rect.bottom() - 20, 0.24 * rect.width(), 14)
        painter.setPen(QPen(_BOARD_EDGE, 1))
        painter.setBrush(QColor("#d9dcd5"))
        painter.drawRect(connector)
        painter.setPen(_MUTED)
        painter.setFont(_font(8))
        painter.drawText(connector, Qt.AlignCenter, "J1 FFC")

    @staticmethod
    def _spot_rect(board_rect: QRectF, sensor: int) -> QRectF:
        x, y = _SPOT_POSITIONS[_base_label(sensor)]
        width = _SPOT_WIDTH * board_rect.width()
        height = _SPOT_HEIGHT if sensor in AMBIENT_SENSOR_IDS else _SAMPLE_SPOT_HEIGHT
        return QRectF(
            board_rect.left() + x * board_rect.width() - width / 2,
            board_rect.top() + y * board_rect.height() - height / 2,
            width,
            height,
        )

    def _paint_pair(self, painter: QPainter, pair: _Pair, left: QRectF, right: QRectF) -> None:
        y = left.center().y()
        painter.setPen(QPen(_PAIR_LINE, 2, Qt.DashLine))
        painter.drawLine(left.right(), y, right.left(), y)

        strip = QRectF(left.right() + 2, left.top(), right.left() - left.right() - 4, left.height())
        # A plain backing keeps the pair line from running through the text.
        painter.fillRect(strip.adjusted(4, 0, -4, 0), _BOARD_FILL)
        painter.setPen(_INK)
        painter.setFont(_font(10, bold=True))
        painter.drawText(QRectF(strip.left(), strip.top(), strip.width(), 18), Qt.AlignCenter, f"PAIR {pair.number}")
        for row, channel in enumerate((pair.device_a, pair.device_b)):
            top = strip.top() + 19 + row * 43
            painter.setFont(_font(8))
            painter.setPen(_MUTED)
            painter.drawText(
                QRectF(strip.left(), top, strip.width(), 13), Qt.AlignCenter,
                f"{channel.device}  ADC{channel.adc_index} CH{channel.channel_index}",
            )
            # Every channel's reading opens its plot; measuring ones stand out.
            value_rect = QRectF(strip.left(), top + 13, strip.width(), 17)
            volts = self._readings.get(channel.slot)
            font = _font(10 if channel.is_signal else 9, bold=channel.is_signal)
            font.setUnderline(True)
            painter.setFont(font)
            painter.setPen(_LINK if channel.is_signal else _MUTED)
            painter.drawText(
                value_rect, Qt.AlignCenter,
                VOLTS.format(channel.slot, volts) if volts is not None else "—",
            )
            self._reading_rects.append((value_rect, channel.slot))
            if not channel.is_signal:
                painter.setFont(_font(8))
                painter.setPen(_MUTED)
                text = channel.role_short
                # Shorten well before the text would reach the sensor boxes.
                if painter.fontMetrics().horizontalAdvance(text) > strip.width() - 16:
                    text = {"noise reference": "noise ref.", "not connected": "not conn.",
                            "connected, noise": "conn., noise"}.get(text, text)
                painter.drawText(QRectF(strip.left(), top + 29, strip.width(), 13), Qt.AlignCenter, text)

    def _paint_spot(self, painter: QPainter, sensor: int, rect: QRectF) -> None:
        temperature = self._temperatures.get(sensor)
        if temperature is None:
            state = "invalid"
        elif temperature >= MAX_SAFE_TEMPERATURE_C:
            state = "hot"
        else:
            state = "valid"
        fill, edge = _STATE_COLORS[state]
        ambient = sensor in AMBIENT_SENSOR_IDS
        painter.setPen(QPen(edge, 2, Qt.DashLine if ambient else Qt.SolidLine))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 6, 6)

        inner = rect.adjusted(8, 4, -8, -4)
        title = TEMP_SENSOR_LABELS[sensor]
        painter.setPen(_INK)
        painter.setFont(_font(11, bold=True))
        role = "ambient" if ambient else f"H{HEATER_SENSOR_IDS.index(sensor)}"
        metrics = painter.fontMetrics()
        if metrics.horizontalAdvance(f"{title}  {role}") > inner.width():
            role = "amb." if ambient else role
        painter.drawText(QRectF(inner.left(), inner.top(), inner.width(), 20), Qt.AlignLeft | Qt.AlignVCenter, title)
        painter.drawText(QRectF(inner.left(), inner.top(), inner.width(), 20), Qt.AlignRight | Qt.AlignVCenter, role)
        painter.setPen(_MUTED)
        painter.setFont(_font(9))
        painter.drawText(
            QRectF(inner.left(), inner.top() + 20, inner.width(), 16), Qt.AlignLeft | Qt.AlignVCenter,
            f"0x{TEMP_SENSOR_I2C_ADDRESSES[sensor]:02X} · B{TEMP_SENSOR_MUX_SIDES[sensor]}",
        )
        painter.setPen(edge if state != "valid" else _INK)
        painter.setFont(_font(13, bold=True))
        painter.drawText(
            QRectF(inner.left(), inner.top() + 36, inner.width(), 26), Qt.AlignLeft | Qt.AlignVCenter,
            f"{temperature:.2f} °C" if temperature is not None else "no reading",
        )
        if ambient:
            return
        sample = SAMPLES.get(TEMP_SENSOR_LABELS[sensor])
        if sample is None:
            painter.setPen(_MUTED)
            painter.setFont(_font(10))
            painter.drawText(
                QRectF(inner.left(), inner.top() + 64, inner.width(), 18), Qt.AlignLeft | Qt.AlignVCenter,
                "no sample",
            )
            return
        id_row = QRectF(inner.left(), inner.top() + 64, inner.width(), 18)
        painter.setPen(_INK)
        painter.setFont(_font(10, bold=True))
        painter.drawText(id_row, Qt.AlignLeft | Qt.AlignVCenter, sample.sample_id)
        if sample.shielded:
            painter.setFont(_font(8, bold=True))
            tag = "SHIELDED"
            if painter.fontMetrics().horizontalAdvance(f"{sample.sample_id}  {tag}") * 1.15 > inner.width():
                tag = "SHLD"
            painter.drawText(id_row, Qt.AlignRight | Qt.AlignVCenter, tag)
        painter.setPen(_MUTED)
        painter.setFont(_font(9))
        painter.drawText(
            QRectF(inner.left(), inner.top() + 82, inner.width(), 16), Qt.AlignLeft | Qt.AlignVCenter,
            f"{sample.connection} · {sample.device}",
        )

    def _paint_footer(self, painter: QPainter, rect: QRectF) -> None:
        if self._stored_wall is not None:
            # Its own line at the bottom: these are not live readings.
            moment = datetime.fromtimestamp(self._stored_wall).strftime("%Y-%m-%d %H:%M:%S")
            painter.setFont(_font(9, bold=True))
            painter.setPen(QColor("#8a5a00"))
            painter.drawText(
                QRectF(rect.left(), rect.bottom() - 18, rect.width(), 18), Qt.AlignLeft | Qt.AlignVCenter,
                f"Showing the last stored values, from {moment}; no live telemetry yet.",
            )
        painter.setFont(_font(9))
        x = rect.left()
        for state, text in (("valid", "valid"), ("hot", f"≥ {MAX_SAFE_TEMPERATURE_C:.0f} °C"), ("invalid", "no reading")):
            fill, edge = _STATE_COLORS[state]
            painter.setPen(QPen(edge, 2))
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(x, rect.top() + 2, 14, 14), 3, 3)
            painter.setPen(_MUTED)
            painter.drawText(QRectF(x + 20, rect.top(), 120, 18), Qt.AlignLeft | Qt.AlignVCenter, text)
            x += 110
        painter.setPen(QPen(_MUTED, 2, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(x, rect.top() + 2, 14, 14), 3, 3)
        painter.setPen(_MUTED)
        painter.drawText(QRectF(x + 20, rect.top(), 200, 18), Qt.AlignLeft | Qt.AlignVCenter, "ambient, no heater")

        notes = (
            "Hn = heater · 0x.. · Bn = I2C address, mux side · a/b = the two devices of a pair, one ADC channel each",
            "Device a (PAIR1 in-amp, pin 5) reads right − left; device b (PAIR2, pin 6) left − right: opposite sign.",
            "Click any reading for its plot. Blue: measuring channels; grey: no sample signal expected (role below).",
        )
        painter.drawText(
            QRectF(rect.left(), rect.top() + 24, rect.width(), rect.height() - 24),
            Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
            "\n".join(notes),
        )


def _font(point_size: int, bold: bool = False) -> QFont:
    font = QFont()
    font.setPointSize(point_size)
    font.setBold(bold)
    return font
