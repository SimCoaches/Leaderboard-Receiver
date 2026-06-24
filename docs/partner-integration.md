# Partner Integration

## Purpose

Sim Coaches Receiver can send a partner webhook after a racer completes a session.

The normal event flow is:

```text
Sender sign-in -> Receiver session tracking -> best lap saved -> race.completed webhook -> partner system
```

The webhook integration is intentionally separate from the working leaderboard display path:

```text
Sender -> Receiver -> lap_times.csv -> PyQt leaderboard
```

If webhook delivery fails, local racing and the monitor display continue.

## Who Provides What

Sim Coaches provides:

- `race.completed` JSON schema and sample payload.
- API key and webhook signing secret, unless the partner prefers to generate them.
- Test-event endpoint on a staging/local Receiver.
- Live leaderboard display URL once the Receiver machine is on the event network.

Partner provides:

- Staging webhook URL where Sim Coaches should POST test race events.
- Production webhook URL for the live event.
- Any field naming or validation requirements that differ from this schema.

Important: `webhook_url` is the partner's receiving endpoint. Sim Coaches posts race events to that URL.

## Event Timing

For normal sessions, Receiver tracks the best lap during the active session and emits one `race.completed` event when the session ends.

Legacy direct lap submissions with no explicit session lifecycle still emit one event per accepted lap. The event integration should use normal session start/end flow for production.

When Lead Gen is enabled in Sender, racer email and phone are required before a driver can start. Those fields are carried through registration, queue assignment, session start, lap submission, and the final `race.completed` webhook.

## Configuration

Receiver creates `integration_config.json` on first run in the Receiver working directory. The real file is gitignored because it contains secrets.

Use `integration_config.example.json` as a template:

```json
{
  "enabled": true,
  "dry_run": false,
  "webhook_url": "https://partner.example.com/webhooks/simcoaches/race-completed",
  "api_key": "replace-with-shared-api-key",
  "signing_secret": "replace-with-shared-signing-secret",
  "timeout_seconds": 5,
  "demo_url": "https://partner.example.com/demo"
}
```

Fields:

- `enabled`: must be `true` before live delivery happens.
- `dry_run`: when `true`, Receiver logs generated events without sending them.
- `webhook_url`: partner endpoint for `race.completed` events.
- `api_key`: sent as `Authorization: Bearer <api_key>` when present.
- `signing_secret`: used for `X-SimCoaches-Signature` HMAC-SHA256.
- `timeout_seconds`: outbound request timeout.
- `demo_url`: optional URL included in payloads.

Generate credentials with:

```powershell
python scripts\generate_integration_credentials.py
```

This prints a fresh API key and signing secret. Share those values with the partner over a secure channel.

To generate credentials and write a local `integration_config.json` after the partner gives us a staging URL:

```powershell
python scripts\generate_integration_credentials.py `
  --webhook-url "https://partner.example.com/webhooks/simcoaches/race-completed" `
  --demo-url "https://partner.example.com/demo" `
  --write-config
```

`integration_config.json` is ignored by git and should stay local to the Receiver machine.

For event installs, the Receiver installer can include the local `integration_config.json` so a new PC starts with the same previously shared API key and signing secret. The installer should not overwrite an existing installed `integration_config.json`.

## In-Person Setup

The Receiver app also has a `Partner` tab for event-day setup without editing files by hand.

1. Open Receiver.
2. Go to `Partner`.
3. If the partner already built against previously shared credentials, paste those exact values into `API Key` and `Signing Secret`.
4. Only click `Generate New Credentials` when intentionally rotating credentials and the partner is ready to update their software.
5. Copy `API Key` and `Signing Secret` for the partner if they need to confirm what Receiver is using.
6. Paste the partner's staging or production `Webhook URL`.
7. Set `Enabled`.
8. Set `Live Send` when the partner is ready to receive real HTTP test requests. Leave `Dry Run` on if we only want to confirm local config.
9. Click `Save Partner Config`.
10. Click `Send Test Webhook`.

Expected results:

- `Dry run confirmed`: Receiver built the signed event but did not send HTTP.
- `Test delivered: ... HTTP 2xx`: the partner endpoint received and accepted the signed event.
- `Test failed: ...`: check the webhook URL, network, API key, signing secret, or partner logs.

## Outbound Webhook Request

Receiver sends an HTTP `POST` to `webhook_url`.

Headers:

```http
Content-Type: application/json
User-Agent: SimCoaches-Leaderboard-Receiver
Authorization: Bearer <api_key>
X-SimCoaches-Signature: <hex hmac sha256>
```

`Authorization` is omitted when `api_key` is blank. `X-SimCoaches-Signature` is omitted when `signing_secret` is blank.

The signature is:

```text
hex(HMAC_SHA256(raw_request_body, signing_secret))
```

Partner should verify the signature against the exact raw body bytes received. Python example:

```python
import hashlib
import hmac

def valid_signature(raw_body: bytes, header_signature: str, signing_secret: str) -> bool:
    expected = hmac.new(
        signing_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, header_signature or "")
```

Partners should de-duplicate on `event_id`. Failed deliveries can be retried and will keep the same `event_id`.

## Payload

Schema: `schemas/race.completed.schema.json`

Sample: `samples/race.completed.sample.json`

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
  "demo_url": "https://partner.example.com/demo"
}
```

Notes:

- `race_time_seconds` is the racer's best lap for the completed session.
- `formatted_race_time` is the display-ready version of the same value.
- `session_id` is stable for the driver's active session.
- `event_id` is unique for the webhook event and should be used for idempotency.
- `racer.email` and `racer.phone` are guaranteed when Lead Gen is enabled.

## Receiver Test Endpoints

These endpoints are served by Receiver on its configured host/port, usually:

```text
http://<receiver-ip>:5000
```

Endpoints:

- `GET /api/integration/config`: returns non-secret integration status.
- `POST /api/integration/test-event`: generates a sample `race.completed` event and runs it through the delivery path.
- `POST /api/integration/retry-pending`: retries queued webhook deliveries. Optional body: `{ "limit": 25 }`.
- `GET /api/leaderboard`: read-only JSON for the current top 10 leaderboard.
- `GET /leaderboard`: browser display that refreshes from `lap_times.csv`.

Trigger a local test event:

```powershell
Invoke-RestMethod `
  -Uri "http://localhost:5000/api/integration/test-event" `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"driver_name":"Alex Racer","email":"alex@example.com","phone":"+17025550123","lap_time":83.456,"simulator_id":"1","session_id":"test-session-001"}'
```

Check integration status:

```powershell
Invoke-RestMethod -Uri "http://localhost:5000/api/integration/config" -Method GET
```

## Remote Testing

Preferred remote test flow:

1. Partner sends Sim Coaches a public staging `webhook_url`.
2. Sim Coaches generates and shares an API key and signing secret.
3. Sim Coaches updates `integration_config.json` with staging values.
4. Sim Coaches starts Receiver and triggers `POST /api/integration/test-event`.
5. Partner confirms receipt, signature verification, and payload mapping.
6. Sim Coaches runs a real sign-in -> lap -> session-end test.

If the partner needs to trigger test events from afar, expose Receiver temporarily through a tunnel such as ngrok or cloudflared, then share only the temporary staging URL. Do not leave a public tunnel open after testing.

Example exposed test URL:

```text
https://temporary-test-url.example.com/api/integration/test-event
```

## Partner Self-Test Without Sim Coaches Source

The partner does not need the Sim Coaches application source code to test their receiving endpoint.

Sim Coaches can send the partner these files only:

- `samples/race.completed.sample.json`
- `samples/send_sample_webhook.py`
- `samples/simcoaches-webhook-self-test.postman_collection.json`
- `schemas/race.completed.schema.json`

Python self-test:

```powershell
python samples\send_sample_webhook.py `
  --url "https://partner.example.com/webhooks/simcoaches/race-completed" `
  --api-key "shared-api-key" `
  --signing-secret "shared-signing-secret"
```

That command sends a signed `race.completed` sample directly to the partner's staging webhook URL. It uses the same header names and HMAC signing behavior as Receiver.

Postman self-test:

1. Import `samples/simcoaches-webhook-self-test.postman_collection.json`.
2. Set collection variables:
   - `webhook_url`
   - `api_key`
   - `signing_secret`
3. Send `Send signed race.completed sample`.
4. Confirm the partner endpoint receives the event and verifies `X-SimCoaches-Signature`.

Successful self-test criteria:

- Partner receives an HTTP `POST`.
- `Authorization: Bearer <api_key>` matches the shared API key.
- `X-SimCoaches-Signature` verifies against the raw request body.
- Payload validates against `schemas/race.completed.schema.json`.
- Partner can safely de-duplicate by `event_id`.

## Delivery Logs And Retry

Receiver appends delivery records to `integration_events.jsonl`. This is for troubleshooting and dry-run review; it is not used by the leaderboard display.

Failed live deliveries are queued in `integration_pending.jsonl` and can be retried with:

```powershell
Invoke-RestMethod `
  -Uri "http://localhost:5000/api/integration/retry-pending" `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"limit":25}'
```

Disabled and dry-run events are logged but not queued.

## Safety Notes

- The existing CSV format is unchanged.
- Integration is disabled and dry-run by default.
- Partner delivery happens in a background thread.
- Webhook failures are logged and do not block lap submissions.
- Live webhook failures are queued for retry on disk.
- Public test tunnels should be used only for staging and closed immediately after testing.
