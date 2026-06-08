#!/usr/bin/env python3
"""Generate partner integration credentials for Sim Coaches Receiver."""

import argparse
import json
import secrets
from pathlib import Path


def build_config(args, api_key, signing_secret):
    webhook_url = (args.webhook_url or "").strip()
    dry_run = bool(args.dry_run or not webhook_url)
    enabled = bool(webhook_url)

    return {
        "enabled": enabled,
        "dry_run": dry_run,
        "webhook_url": webhook_url,
        "api_key": api_key,
        "signing_secret": signing_secret,
        "timeout_seconds": args.timeout_seconds,
        "demo_url": (args.demo_url or "").strip(),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate API key and signing secret for partner webhooks."
    )
    parser.add_argument(
        "--bytes",
        type=int,
        default=32,
        help="Random bytes per secret before URL-safe encoding. Default: 32.",
    )
    parser.add_argument(
        "--webhook-url",
        default="",
        help="Partner staging or production webhook URL to write into config.",
    )
    parser.add_argument(
        "--demo-url",
        default="",
        help="Optional demo URL included in race.completed payloads.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=5,
        help="Outbound webhook timeout for generated config. Default: 5.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write generated config with dry_run enabled.",
    )
    parser.add_argument(
        "--write-config",
        nargs="?",
        const="integration_config.json",
        help="Write integration_config.json. Optional path may be provided.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing config when used with --write-config.",
    )
    args = parser.parse_args()

    if args.bytes < 16:
        parser.error("--bytes must be at least 16")
    if args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be at least 1")

    api_key = secrets.token_urlsafe(args.bytes)
    signing_secret = secrets.token_urlsafe(args.bytes)
    config = build_config(args, api_key, signing_secret)

    print("Generated partner integration credentials")
    print()
    print(f"API key:        {api_key}")
    print(f"Signing secret: {signing_secret}")
    print()
    print("Share the API key and signing secret with the partner over a secure channel.")
    print("Do not commit integration_config.json or paste these values into public tickets.")

    if not args.write_config:
        print()
        print("To write a local config, rerun with --write-config and --webhook-url.")
        return

    config_path = Path(args.write_config)
    if config_path.exists() and not args.force:
        raise SystemExit(
            f"{config_path} already exists. Use --force to overwrite it intentionally."
        )

    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print()
    print(f"Wrote local config: {config_path}")
    if not config["webhook_url"]:
        print("No webhook URL was provided, so enabled=false and dry_run=true.")
    elif config["dry_run"]:
        print("Config has dry_run=true. Set dry_run=false for live delivery.")
    else:
        print("Config is ready for live delivery to the configured webhook URL.")


if __name__ == "__main__":
    main()
