# chronocat_ground

Minimal PySide6 desktop chronocat_ground app for Chronocat firmware.

## Features

- Receives UDP telemetry on port `5005`.
- Connects to the firmware TCP command server on `192.168.1.50:5006`.
- Shows packet counter, firmware timestamp, flags, health, sensor masks, AD7177 raw ADC telemetry, Geiger telemetry, source address, packet count, and packet age.
- Shows top-bar telemetry state alongside TCP connection and SD logger state.
- Sends binary command packets for ping, telemetry on, telemetry off, and telemetry status.
- Provides Geiger detector memory controls on the Radiation page.
- Provides a PID Heating page with mapped-heater telemetry and plots, individual controls,
  and a common-setpoint PID activation control with selectable profiles.

## Setup

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e '.[gui]'
```

Use Python 3.12 for now. Python 3.14 is too new for reliable PySide6 wheel support, and the local Homebrew Python install may fail while bootstrapping `pip`. PySide6 is pinned to 6.9.3 because newer wheels fail to initialize the macOS Cocoa platform plugin on the current ground-station environment.

## Run

```bash
chronocat_ground
```

Or:

```bash
python -m chronocat_ground.main
```

The GUI has a `Start CSV Log` / `Stop CSV Log` toggle in the top bar. When enabled, it
creates a timestamped CSV file in the current directory and writes each received telemetry
packet to it. The file is flushed after every packet.

## Raspberry Pi CLI Logging

For a Raspberry Pi or any headless machine, install the CLI without Qt/PySide6:

For full Raspberry Pi SD-card, static IP, and boot autostart setup, see
[`pi_rad_test/README.md`](pi_rad_test/README.md).

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Run the UDP recorder:

```bash
chronocat_telemetry --out flight.csv --quiet
chronocat_telemetry --out radiation.csv --quiet --geiger-only
```

Equivalent module form:

```bash
python -m chronocat_ground.telemetry_cli --out flight.csv --quiet
```

The recorder listens on UDP `0.0.0.0:5005` by default and flushes the CSV after every
completed telemetry row. Current combined packets are written directly as one row;
legacy standard and PID packets sharing a sequence are still joined into one row.
Incomplete legacy rows are retained rather than discarded. Each write is synchronized to
disk so completed rows survive an unexpected power loss. Only one process can bind UDP
port `5005` at a time.

## Record Telemetry to CSV

Run the headless telemetry recorder:

```bash
chronocat_telemetry
```

By default it listens on UDP `0.0.0.0:5005` and writes a timestamped CSV file such as
`telemetry_20260628_143012_a1b2c3d4_e5f6a7b8.csv` in the current directory. The final two
components are the Linux boot ID and a random logger session ID. They prevent repeated
boots with a stale Raspberry Pi clock from selecting the same filename. Full logging writes
current combined packets as one row. For legacy traffic it joins the standard and PID
packets using their shared timestamp and sequence counter.

New logs are created exclusively and never overwrite an existing file. An explicit
`--out` path also refuses to replace an existing file unless `--overwrite` is supplied.

Useful options:

```bash
chronocat_telemetry --out flight.csv
chronocat_telemetry --out flight.csv --overwrite
chronocat_telemetry --port 5005
chronocat_telemetry --bind 0.0.0.0
chronocat_telemetry --max-packets 100
chronocat_telemetry --quiet
chronocat_telemetry --strict
chronocat_telemetry --geiger-only
```

Use `--overwrite` only when intentionally discarding the existing output file. Automatic
filenames never overwrite, even when the Pi clock is wrong.

`--geiger-only` writes one row per valid detector response. It keeps only the receive
timestamp, detector ID, and fields supplied by detector command D; it omits all other
telemetry, interpreted error names, aliases, and placeholder columns.

The CSV includes receive time, packet timestamp in milliseconds, counter, flags, health,
12 temperature sensor values with validity flags, 12 decoded AD7177 readings, both Geiger telemetry slots,
and TCP status. Legacy `geiger_*` columns remain aliases for Geiger 1; explicit `geiger_0_*` and
`geiger_1_*` columns identify both counters.

The Dashboard view also plots a rolling average of the error-free AD7177
`raw24` channel values present in each packet. Channels carrying ADC/CRC/register
errors are excluded from that average sample.

The PID Heating view keeps the selected-heater temperature and duty plots and
also provides rolling all-heater averages. Average temperature uses sensor-valid
heater readings; average duty uses all twelve applied duty values, including
zero/off channels.

The GUI and recorder both bind UDP port `5005`, so normally run only one of them at a time
on the same machine.

## Code Layout

The GUI composition root is intentionally small. Reusable Qt widgets and page construction
live under `chronocat_ground/ui/`; command transport is handled by `command_dispatcher.py`;
telemetry history and durable database projection are handled by `telemetry_history.py`.
The public `protocol.py` and `telemetry_csv.py` modules remain compatibility facades over
the focused codec, model, schema, and logger modules.

## Firmware Protocol

Telemetry UDP app payload, carried inside normal UDP/IP packets. Multi-byte fields are big endian.

```text
Common field                  Bytes
magic = 'CCTM'                4
version                       1
message_type                  1
flags                         2
payload_length                2
packet_timestamp_ms           4
counter                       4
health_code                   1
temperature_valid_mask        2
temperature_sensors_1_13     26  int16 centi-degrees C
os_adc_valid_mask             2
os_adc_readings_1_12         48  uint32 AD7177 word: [31:8] raw24, [7:0] status
```

The 97-byte prefix applies to legacy versions 1/2 and to the standard portion of
the current combined packet. Legacy version 3 type 1 additionally inserts a
2-byte H0 duty field after the temperatures, making its standard prefix 99 bytes.
The prefix is followed by one or more 34-byte Geiger records:

```text
Geiger record field           Bytes
valid                         1
counter_id                    1  reserved and treated as ID 0 in version 1
error_flags                   2
event_id                      4
dose_cps                      8  float64
dose_rate_cps                 4  float32
total_dose_sv                 4  float32
dose_time_sec                 4
stats_time_sec                2
hv_voltage                    2
stat_error_percent            1
stat_cell_count               1
```

Supported telemetry packets:

```text
version 1, type 1: 129 bytes, one Geiger record (legacy)
version 2, type 1: 163 bytes, two Geiger records (legacy)
version 3, type 1: 165 bytes, standard telemetry (legacy)
version 3, type 2: 442 bytes, PID-only telemetry (development format)
version 3, type 3: 587 bytes, combined telemetry (current firmware)
```

The current firmware sends one version 3, type 3 datagram per second. It combines
the 163-byte standard body with two PID masks and twelve 35-byte heater records:

```text
offset  size  field
0       18    common header
18      1     health_code
19      2     temperature_valid_mask
21      24    12 signed int16 temperatures in centi-degrees C
45      2     os_adc_valid_mask
47      48    12 uint32 AD7177 words
95      68    two 34-byte Geiger records
163     4     PID-enabled and manual-mode uint16 masks
167     420   twelve 35-byte heater records
587           total application payload length
```

Each combined heater record is encoded as target (uint32 milli-degrees C), duty
(uint16 permille), result (1 byte), and seven float32 values: proportional,
integral, derivative, output, Kp, Ki, and Kd. The record order identifies heaters
0 through 11. Heater feedback and validity come from the shared temperature fields;
the heater-to-sensor mapping is fixed in firmware and the ground-station decoder.

IPv4 and UDP headers are generated by LwIP and are not included in these app payload sizes.

Flags:

```text
bit 0 telemetry enabled
bit 1 TCP server listening
```

Masks:

```text
temperature_valid_mask bit 0 = temperature sensor 1 valid
temperature_valid_mask bit 12 = temperature sensor 13 valid
os_adc_valid_mask is retained in the packet layout but AD7177 slots are decoded from the 12 uint32 words.
```

AD7177 slot order is fixed:

```text
slot 0  = ADC0 CH0
slot 1  = ADC0 CH1
slot 2  = ADC0 CH2
slot 3  = ADC1 CH0
slot 4  = ADC1 CH1
slot 5  = ADC1 CH2
slot 6  = ADC2 CH0
slot 7  = ADC2 CH1
slot 8  = ADC2 CH2
slot 9  = ADC3 CH0
slot 10 = ADC3 CH1
slot 11 = ADC3 CH2
```

AD7177 status byte bits:

```text
bit 7    RDY
bit 6    ADC_ERROR
bit 5    CRC_ERROR
bit 4    REG_ERROR
bits 1:0 status channel number
```

Firmware fills all four AD7177 devices and their three configured channels.

TCP command request app payload, carried inside normal TCP/IP packets:

```text
uint8  command
uint16 arg1    big endian
uint16 arg2    big endian
```

TCP command response app payload:

```text
uint8  status
uint8  command
uint16 arg1    big endian
uint16 arg2    big endian
```

Commands:

```text
0x01 ping
0x02 telemetry set
0x03 telemetry status
0x47 geiger reset total dose
0x4F geiger clear history
0x58 geiger reset statistics
```

Values:
Telemetry set uses `arg1`:

```text
0x00 off
0x01 on
```

Geiger memory commands map directly to detector UART commands:

```text
0x47 -> detector Command G, reset accumulated total dose
0x4F -> detector Command O, clear history only
0x58 -> detector Command X, reset accumulated statistics
```

Geiger command `arg1` selects the detector and `arg2` remains zero:

```text
arg1 = 0  Geiger 1
arg1 = 1  Geiger 2
arg2 = 0
```

The current GUI controls intentionally target Geiger 1 only.

The legacy manual global heater controls remain protocol-compatible:

```text
0x1B all on:  arg1 = 0, arg2 = duty permille (1..250)
0x1C all off: arg1 = 0, arg2 = 0
```

All On requires all 12 temperature sensors to be valid and below 65 C. All Off stops
every heater immediately.

Geiger memory commands can take a few seconds while the detector writes flash.
The TCP response uses `arg1` for detector error flags and
`arg2` as an effective action mask:

```text
arg2 bit 0 total dose reset
arg2 bit 1 history cleared
arg2 bit 2 statistics reset
```
