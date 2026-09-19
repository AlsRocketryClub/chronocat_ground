"""Application stylesheet kept outside the window composition code."""

APPLICATION_STYLE = """
QWidget {
    color: #111111;
    font-family: Arial, Helvetica, sans-serif;
    font-size: 13px;
}
QLabel {
    background: transparent;
}
#appShell, #pages, #pageScroll, QScrollArea > QWidget > QWidget {
    background: #dcdcdc;
}
#topbar {
    background: #f4f4f4;
    border: 1px solid #8f8f8f;
}
#sidebar, #panel, #telemetryState, #sampleCard {
    background: #f8f8f8;
    border: 1px solid #7a7a7a;
    border-radius: 0px;
}
#eyebrow {
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 1px;
}
#title {
    font-size: 24px;
    font-weight: 700;
    letter-spacing: 1px;
}
#panelTitle, #kpiLabel, #formLabel {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}
#kpiValue {
    font-size: 18px;
    font-weight: 700;
}
#subtitle, #kpiSub, #smallNote, #sampleMetric {
    color: #444444;
    font-size: 12px;
}
QLineEdit, QPlainTextEdit, QDoubleSpinBox, QSpinBox {
    background: #ffffff;
    border: 1px solid #777777;
    border-radius: 0px;
    padding: 6px 8px;
    selection-background-color: #d9d9d9;
    selection-color: #111111;
}
QComboBox {
    background: #ffffff;
    border: 1px solid #777777;
    border-radius: 0px;
    padding: 6px 8px;
    min-width: 80px;
}
QComboBox:hover {
    border-color: #555555;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #777777;
    selection-background-color: #d9d9d9;
    selection-color: #111111;
    outline: none;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QPushButton {
    background: #ececec;
    border: 1px solid #666666;
    border-radius: 0px;
    padding: 8px 10px;
    font-weight: 700;
    min-width: 80px;
}
QPushButton:hover {
    background: #dfdfdf;
}
QPushButton:pressed {
    background: #d0d0d0;
}
QPushButton:disabled {
    color: #777777;
    background: #eeeeee;
    border-color: #aaaaaa;
}
#navButton, #navButtonActive {
    text-align: left;
    background: #ffffff;
    border: 1px solid #7f7f7f;
    font-weight: 400;
    min-width: 90px;
}
#navButtonActive {
    background: #d9d9d9;
    font-weight: 700;
}
#sampleToggle {
    text-align: left;
    background: #ffffff;
    min-width: 100px;
}
#statusIndicator {
    border: 1px solid #777777;
    border-radius: 0px;
    background: #edd0d0;
    font-weight: 700;
}
#statusIndicatorTitle {
    color: #444444;
    font-size: 10px;
    letter-spacing: 1px;
}
#statusIndicatorValue {
    font-size: 12px;
    font-weight: 700;
}
#statusIndicator[status="connected"] {
    background: #d8ead8;
}
#statusIndicator[status="connecting"],
#statusIndicator[status="waiting"],
#statusIndicator[status="unknown"],
#statusIndicator[status="off"] {
    border: 1px solid #999999;
    background: #eeeeee;
}
#statusIndicator[status="receiving"],
#statusIndicator[status="active"] {
    background: #d8ead8;
    border-color: #6e9f6e;
    color: #245824;
}
#statusIndicator[status="stale"],
#statusIndicator[status="connecting"] {
    background: #fff0c7;
    border-color: #c49a37;
    color: #674900;
}
#statusIndicator[status="error"],
#statusIndicator[status="disconnected"] {
    background: #f5d8d8;
    border-color: #b36a6a;
    color: #702525;
}
#statusIndicator[status="off"] {
    color: #555555;
}
#healthSummary {
    background: #f3f3f3;
    border: 2px solid #999999;
}
#healthSummary[state="healthy"] {
    background: #eef6ee;
    border-color: #6e9f6e;
}
#healthSummary[state="warning"] {
    background: #fff8df;
    border-color: #c49a37;
}
#healthSummary[state="error"] {
    background: #fbeaea;
    border-color: #b36a6a;
}
#healthSummary[selected="true"] {
    border: 3px solid #3f6f9f;
}
#healthSectionTitle {
    color: #444444;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}
#healthSectionStatus {
    color: #111111;
    font-size: 16px;
    font-weight: 700;
    min-height: 24px;
}
#healthDetailsButton {
    background: transparent;
    padding: 2px 6px;
    min-width: 82px;
    font-size: 11px;
}
#healthDetailLabel {
    color: #666666;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}
#healthDetailTitle {
    color: #111111;
    font-size: 16px;
    font-weight: 700;
}
#telemetryState {
    padding: 10px;
    font-weight: 700;
}
#telemetryState[active="true"] {
    background: #eef6ee;
}
#logView {
    font-family: Menlo, Consolas, monospace;
    font-size: 12px;
}
#dataTable {
    background: #ffffff;
    border: 1px solid #b5b5b5;
    gridline-color: #b5b5b5;
}
#pidHeader, #pidGlobalControls, #pidOverview, #pidDetail, #pidControls, #pidMetrics {
    background: #f8f8f8;
    border: 1px solid #9a9a9a;
}
#pidHeader {
    border-left: 5px solid #3f6f9f;
}
#primaryButton {
    background: #d8ead8;
    border-color: #6e9f6e;
}
#pidTitle, #pidDetailTitle {
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 1px;
}
#pidSummary {
    color: #444444;
    font-size: 12px;
}
#pidOverviewScroll {
    background: transparent;
    border: none;
}
#pidColumnLabel {
    color: #666666;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
#pidRow {
    background: #ffffff;
    border: 1px solid #c4c4c4;
}
#pidRow[selected="true"] {
    background: #e5eff8;
    border: 2px solid #3f6f9f;
}
#pidRow[state="active"] #pidBadge {
    background: #d8ead8;
    border-color: #6e9f6e;
    color: #245824;
}
#pidRow[state="manual"] #pidBadge {
    background: #dce8f4;
    border-color: #7898b7;
    color: #234769;
}
#pidRow[state="blocked"] #pidBadge {
    background: #fff0c7;
    border-color: #c49a37;
    color: #674900;
}
#pidRow[state="fault"] #pidBadge {
    background: #f5d8d8;
    border-color: #b36a6a;
    color: #702525;
}
#pidRow[state="off"] #pidBadge,
#pidRow[state="waiting"] #pidBadge {
    background: #ededed;
    border-color: #aaaaaa;
    color: #555555;
}
#pidBadge {
    border: 1px solid #aaaaaa;
    padding: 3px 7px;
    font-size: 10px;
    font-weight: 700;
}
#pidRowHeater, #pidRowTemperature, #pidRowDuty {
    font-weight: 700;
}
#pidRowSensor {
    color: #555555;
    font-size: 11px;
}
#pidDetailMapping {
    color: #555555;
    font-size: 11px;
    letter-spacing: 1px;
}
#pidAlert {
    background: #eef3f7;
    border: 1px solid #b7c8d8;
    padding: 7px 9px;
    color: #314b60;
}
#pidMetrics {
    border-color: #c5c5c5;
}
#pidMetricLabel {
    color: #666666;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
#pidMetricValue {
    font-size: 16px;
    font-weight: 700;
}
#dangerButton {
    background: #f0d6d6;
    border-color: #aa6262;
    color: #6f2020;
}
#dangerButton:hover {
    background: #e9c3c3;
}
QTableWidget::item {
    padding: 4px 6px;
}
QHeaderView::section {
    background: #e7e7e7;
    border: 1px solid #b5b5b5;
    padding: 5px 6px;
    font-weight: 700;
}
#linePlot, #sparkline, #sampleChart, #chartBox {
    border: 1px solid #b0b0b0;
}
#linePlot {
    min-height: 180px;
}
#sparkline {
    min-height: 130px;
    font-family: Menlo, Consolas, monospace;
    font-size: 16px;
}
#sampleChart {
    min-height: 90px;
    font-family: Menlo, Consolas, monospace;
}
#selectPlaceholder, #noteBox {
    background: #ffffff;
    border: 1px solid #777777;
    padding: 8px;
}
#noteBox {
    background: #f1f1f1;
    font-size: 12px;
}
"""
