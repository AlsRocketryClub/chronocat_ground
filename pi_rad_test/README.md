# Raspberry Pi Radiation Test Logger

This directory contains Raspberry Pi setup files for headless UDP telemetry logging.

The expected network is:

```text
Devboard:      192.168.1.50
Raspberry Pi:  192.168.1.10
UDP telemetry: 0.0.0.0:5005 on the Pi
```

The firmware sends telemetry to `192.168.1.10:5005`, so the Pi Ethernet interface should use `192.168.1.10/24` when directly connected to the devboard.

## Put An OS On The SD Card

Use **Raspberry Pi Imager**.

Recommended OS:

```text
Raspberry Pi OS Lite (64-bit)
```

Steps:

1. Install Raspberry Pi Imager on your laptop: https://www.raspberrypi.com/software/
2. Insert the empty SD card.
3. Choose device: your Raspberry Pi model.
4. Choose OS: `Raspberry Pi OS (other)` -> `Raspberry Pi OS Lite (64-bit)`.
5. Choose storage: the SD card.
6. Open OS customization before writing.
7. Set hostname, user/password, locale, and enable SSH.
8. Write the image.
9. Insert the SD card into the Pi and boot it.

For a headless Pi, SSH is enough. You do not need the desktop OS.

## Copy This Repo To The Pi

On the Pi, clone/copy the `chronocat_ground` repo. Example:

```bash
cd /home/pi
git clone <your-repo-url> chronocat_ground
cd chronocat_ground
```

If you copy files manually instead of git, keep this directory layout:

```text
/home/pi/chronocat_ground/pyproject.toml
/home/pi/chronocat_ground/chronocat_ground/
/home/pi/chronocat_ground/pi_rad_test/
```

## One-Command Logger Setup

From the repo root on the Pi:

```bash
cd /home/pi/chronocat_ground
sudo ./pi_rad_test/setup_pi_logger.sh --configure-eth0
```

This will:

- Create a Python venv at `.venv-pi`.
- Install the CLI logger from the current repo checkout.
- Create `/home/pi/chronocat_logs`.
- Install a `systemd` service named `chronocat-telemetry`.
- Start the service now.
- Enable it on boot.
- Optionally configure `eth0` to `192.168.1.10/24`.

If you do not want the script to touch network settings:

```bash
sudo ./pi_rad_test/setup_pi_logger.sh
```

## Check It

Service status:

```bash
sudo systemctl status chronocat-telemetry
```

Follow service logs:

```bash
sudo journalctl -u chronocat-telemetry -f
```

Check CSV files:

```bash
ls -lh /home/pi/chronocat_logs
```

The logger writes timestamped CSV files like:

```text
telemetry_YYYYMMDD_HHMMSS.csv
```

The file is flushed after every valid packet. The installed service uses `--geiger-only`,
so each CSV row contains one valid detector response without ADC, temperature, or
placeholder columns.

## Useful Commands

Restart logger:

```bash
sudo systemctl restart chronocat-telemetry
```

Stop logger:

```bash
sudo systemctl stop chronocat-telemetry
```

Disable boot autostart:

```bash
sudo systemctl disable chronocat-telemetry
```

Run manually for a quick test:

```bash
cd /home/pi/chronocat_logs
/home/pi/chronocat_ground/.venv-pi/bin/chronocat_telemetry --quiet --geiger-only
```

Check Pi IP address:

```bash
ip addr show eth0
```

Check whether anything is listening on UDP 5005:

```bash
sudo ss -lunp | grep 5005
```

## Notes

- Only one process can bind UDP port `5005` at a time.
- The logger does not send TCP commands.
- The logger only receives UDP telemetry and writes CSV.
- The telemetry parser accepts version 1 (`131` bytes, one Geiger) and version 2
  (`165` bytes, two Geigers) packets.
