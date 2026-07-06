from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import socket
import sys

from .protocol import DEFAULT_TELEMETRY_PORT, parse_telemetry_packets, telemetry_health_name
from .telemetry_csv import TelemetryCsvLogger, default_output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Receive Chronocat UDP telemetry and write packets to CSV."
    )
    parser.add_argument("--bind", default="0.0.0.0", help="local address to bind, default: 0.0.0.0")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_TELEMETRY_PORT,
        help=f"UDP telemetry port, default: {DEFAULT_TELEMETRY_PORT}",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="CSV output path, default: telemetry_YYYYMMDD_HHMMSS.csv",
    )
    parser.add_argument("--max-packets", type=int, default=None, help="stop after this many valid packets")
    parser.add_argument("--quiet", action="store_true", help="do not print packet summaries")
    parser.add_argument("--strict", action="store_true", help="stop on the first invalid packet")
    return parser


def record(args: argparse.Namespace) -> int:
    if not 1 <= args.port <= 65535:
        print("error: --port must be from 1 to 65535", file=sys.stderr)
        return 2

    output_path = args.out or default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    packets_written = 0

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((args.bind, args.port))
    except OSError as exc:
        print(f"error: could not bind UDP {args.bind}:{args.port}: {exc}", file=sys.stderr)
        return 1

    print(f"Listening for UDP telemetry on {args.bind}:{args.port}")
    print(f"Writing CSV to {output_path}")

    logger = TelemetryCsvLogger(output_path)
    try:
        logger.start()
        while True:
            data, address = sock.recvfrom(2048)
            received_at = datetime.now()

            try:
                packets = parse_telemetry_packets(data)
            except ValueError as exc:
                preview = data[:16].hex(" ")
                message = (
                    f"warning: {address[0]}:{address[1]} sent {len(data)} bytes: "
                    f"{exc}; first bytes: {preview}"
                )
                print(message, file=sys.stderr)
                if args.strict:
                    return 1
                continue

            for packet in packets:
                logger.write_packet(packet, address, received_at)
                packets_written += 1

                if not args.quiet:
                    health = telemetry_health_name(packet.health_code)
                    print(
                        f"packet {packets_written}: counter={packet.counter} "
                        f"timestamp_ms={packet.timestamp} health={health} source={address[0]}:{address[1]}"
                    )

                if args.max_packets is not None and packets_written >= args.max_packets:
                    return 0
    except KeyboardInterrupt:
        print(f"\nStopped after writing {packets_written} packet(s).")
        return 0
    finally:
        logger.stop()
        sock.close()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return record(args)


if __name__ == "__main__":
    raise SystemExit(main())
