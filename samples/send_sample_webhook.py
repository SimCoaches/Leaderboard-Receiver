#!/usr/bin/env python3
"""Send a signed sample race.completed webhook to a partner endpoint.

This is a standalone partner self-test utility. It does not require the
Sim Coaches Receiver app to be running.
"""

import argparse
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_PAYLOAD = Path(__file__).with_name("race.completed.sample.json")


def format_lap_time(seconds):
    seconds = float(seconds)
    minutes = int(seconds // 60)
    remaining = seconds % 60
    return f"{minutes:02d}:{remaining:06.3f}"


def split_name(full_name):
    parts = str(full_name or "").strip().split()
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", parts[0]
    return parts[0], " ".join(parts[1:]), " ".join(parts)


def load_payload(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def update_payload(payload, args):
    if not args.keep_event_id:
        payload["event_id"] = str(uuid.uuid4())
        payload["session_id"] = args.session_id or f"self-test-{uuid.uuid4()}"
        payload["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    elif args.session_id:
        payload["session_id"] = args.session_id

    if args.driver_name:
        first, last, full = split_name(args.driver_name)
        payload["racer"]["first_name"] = first
        payload["racer"]["last_name"] = last
        payload["racer"]["full_name"] = full

    if args.email:
        payload["racer"]["email"] = args.email
    if args.phone:
        payload["racer"]["phone"] = args.phone
    if args.lap_time is not None:
        payload["race_time_seconds"] = float(args.lap_time)
        payload["formatted_race_time"] = format_lap_time(args.lap_time)
    if args.simulator_id:
        payload["simulator_id"] = str(args.simulator_id)
    if args.demo_url is not None:
        payload["demo_url"] = args.demo_url

    return payload


def build_headers(body, api_key, signing_secret):
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "SimCoaches-Leaderboard-Receiver",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if signing_secret:
        headers["X-SimCoaches-Signature"] = hmac.new(
            signing_secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    return headers


def main():
    parser = argparse.ArgumentParser(
        description="Send a signed Sim Coaches race.completed sample webhook."
    )
    parser.add_argument("--url", required=True, help="Partner webhook URL.")
    parser.add_argument("--api-key", required=True, help="Shared API key.")
    parser.add_argument("--signing-secret", required=True, help="Shared signing secret.")
    parser.add_argument("--payload", default=str(DEFAULT_PAYLOAD), help="Payload JSON file.")
    parser.add_argument("--driver-name", default="Alex Racer", help="Sample racer full name.")
    parser.add_argument("--email", default="alex@example.com", help="Sample racer email.")
    parser.add_argument("--phone", default="+17025550123", help="Sample racer phone.")
    parser.add_argument("--lap-time", type=float, default=83.456, help="Best lap in seconds.")
    parser.add_argument("--simulator-id", default="1", help="Sample simulator ID.")
    parser.add_argument("--session-id", default="", help="Optional session ID override.")
    parser.add_argument("--demo-url", default=None, help="Optional demo URL override.")
    parser.add_argument(
        "--keep-event-id",
        action="store_true",
        help="Use event_id/completed_at/session_id from payload file.",
    )
    parser.add_argument("--timeout", type=int, default=10, help="HTTP timeout in seconds.")
    args = parser.parse_args()

    payload = update_payload(load_payload(args.payload), args)
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    headers = build_headers(body, args.api_key, args.signing_secret)

    request = Request(args.url, data=body.encode("utf-8"), headers=headers, method="POST")

    print(f"Sending race.completed sample to {args.url}")
    print(f"event_id: {payload['event_id']}")
    print(f"session_id: {payload['session_id']}")

    try:
        with urlopen(request, timeout=args.timeout) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            print(f"Response status: {response.status}")
            if response_body:
                print("Response body:")
                print(response_body)
    except HTTPError as e:
        response_body = e.read().decode("utf-8", errors="replace")
        print(f"Response status: {e.code}")
        if response_body:
            print("Response body:")
            print(response_body)
        raise SystemExit(1)
    except URLError as e:
        print(f"Request failed: {e.reason}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
