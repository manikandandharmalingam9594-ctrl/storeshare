"""
CLI Entry point for running the Store-Share Provider Agent on Windows / Linux / macOS.

Usage:
  python -m provider_agent --storage-dir "C:\\storeshare-storage" --quota-gb 50 --email provider@example.com --password secret
  python -m provider_agent --storage-dir "C:\\storeshare-storage" --quota-gb 50 --token "<JWT_TOKEN>"
"""

import argparse
import logging
import os
import sys

from provider_agent.agent import ProviderAgent
from provider_agent.config import AgentConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Store-Share Local Provider Agent: allocates host storage and sends authenticated telemetry.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--coordinator-url",
        default=os.getenv("STORESHARE_COORDINATOR_URL", "http://127.0.0.1:8001"),
        help="Base URL of the Store-Share coordinator service",
    )
    parser.add_argument(
        "--storage-dir",
        default=os.getenv("STORESHARE_STORAGE_DIR"),
        required=not bool(os.getenv("STORESHARE_STORAGE_DIR")),
        help="Path to local storage directory allocated for Store-Share chunks",
    )
    parser.add_argument(
        "--quota-gb",
        type=float,
        default=float(os.getenv("STORESHARE_QUOTA_GB", "10.0")),
        help="Allocated storage quota in Gigabytes (limits chunk usage; does not format disk)",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("STORESHARE_AUTH_TOKEN"),
        help="JWT Bearer token for pre-authenticated provider identity",
    )
    parser.add_argument(
        "--email",
        default=os.getenv("STORESHARE_EMAIL"),
        help="Provider account email address for authentication",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("STORESHARE_PASSWORD"),
        help="Provider account password for authentication",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=int,
        default=int(os.getenv("STORESHARE_HEARTBEAT_INTERVAL", "15")),
        help="Seconds between outbound telemetry heartbeats",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification (development only)",
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Authenticate, sync storage setup, send one heartbeat, and exit",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable detailed debug logging",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not args.token and not (args.email and args.password):
        print(
            "Error: Authentication credentials required. Provide either --token or both --email and --password.",
            file=sys.stderr,
        )
        sys.exit(1)

    config = AgentConfig(
        coordinator_url=args.coordinator_url,
        storage_dir=args.storage_dir,
        quota_gb=args.quota_gb,
        token=args.token,
        email=args.email,
        password=args.password,
        heartbeat_interval=args.heartbeat_interval,
        verify_tls=not args.insecure,
    )

    try:
        with ProviderAgent(config=config) as agent:
            if args.run_once:
                res = agent.heartbeat_once()
                print(f"Single heartbeat successful: {res}")
                sys.exit(0)
            else:
                agent.run()
    except KeyboardInterrupt:
        print("\nAgent stopped by user.")
        sys.exit(0)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
