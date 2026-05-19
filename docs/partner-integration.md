# Partner Integration

This integration is intentionally separate from the working leaderboard display path:

`Sender -> Receiver -> lap_times.csv -> PyQt leaderboard`

The partner webhook runs after a lap result has already been accepted and written. If webhook delivery fails, local racing and the monitor display continue.

For normal sessions, Receiver tracks the best lap during the active session and emits `race.completed` when the session ends. Legacy direct lap submissions with no session lifecycle still emit one event per accepted lap.

## Config

Receiver creates `integration_config.json` on first run:

```json
{
  "enabled": false,
  "dry_run": true,
  "webhook_url": "",
  "api_key": "",
  "signing_secret": "",
  "timeout_seconds": 5,
  "demo_url": ""
}
```

- `enabled`: must be `true` before real delivery happens.
- `dry_run`: logs generated events without sending them.
- `webhook_url`: partner endpoint for race-completed events.
- `api_key`: sent as `Authorization: Bearer ...` when present.
- `signing_secret`: used for `X-SimCoaches-Signature` HMAC-SHA256.
- `demo_url`: optional URL included in payloads.

## Endpoints

- `GET /api/integration/config`: returns non-secret integration status.
- `POST /api/integration/test-event`: generates a sample `race.completed` event and runs it through the same delivery path.
- `POST /api/integration/retry-pending`: retries queued webhook deliveries. Optional body: `{ "limit": 25 }`.
- `GET /api/leaderboard`: read-only JSON for the current top 10 leaderboard.
- `GET /leaderboard`: browser display that refreshes from `lap_times.csv` every 2 seconds.

## Payload

Schema: `schemas/race.completed.schema.json`

```json
{
  "event_type": "race.completed",
  "event_id": "0f9eb4bd-18f6-497c-b94d-ad449a21b982",
  "session_id": "sim-1-20260519-123000",
  "racer": {
    "first_name": "Alex",
    "last_name": "Racer",
    "full_name": "Alex Racer",
    "email": "alex@example.com",
    "phone": "+17025550123"
  },
  "race_time_seconds": 83.456,
  "formatted_race_time": "01:23.456",
  "simulator_id": "1",
  "completed_at": "2026-05-19T12:30:00.000000",
  "demo_url": "https://example.com/demo"
}
```

## Delivery Log

Receiver appends delivery records to `integration_events.jsonl`. This is for troubleshooting and dry-run review; it is not used by the leaderboard display.

Failed live deliveries are queued in `integration_pending.jsonl` and can be retried with `POST /api/integration/retry-pending`. Disabled and dry-run events are logged but not queued.

## Safety Notes

- The existing CSV format is unchanged.
- Integration is disabled by default.
- Partner delivery happens in a background thread.
- Webhook failures are logged and do not block lap submissions.
- Live webhook failures are queued for retry on disk.
