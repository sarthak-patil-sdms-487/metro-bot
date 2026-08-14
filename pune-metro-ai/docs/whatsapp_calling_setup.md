# WhatsApp chat and calling setup

The application runs text chat and WhatsApp Calling in one FastAPI process and
uses the same `POST /webhook` callback for both. The payload determines which
adapter runs:

- a `messages` event uses the existing text/chat adapter;
- a `calls` event uses the in-process Pipecat/WebRTC voice adapter;
- both store their history in PostgreSQL and use the shared brain and tools.

Calling is optional. When `WHATSAPP_CALLING_ENABLED=false`, chat continues to
work and `/health` reports `calling_ready: false`.

## 1. Meta prerequisites

Use a WhatsApp Business Platform Cloud API number that is eligible for the
WhatsApp Business Calling API. Calling is not enabled by default. Production
numbers may be subject to Meta business, quality, and messaging-tier eligibility;
Meta public test numbers/sandbox accounts can have relaxed restrictions.

In Meta WhatsApp Manager, select the business phone number and enable voice
calling/call-icon visibility if that control is available. The equivalent Graph
API operation is a phone-number settings update similar to:

```bash
curl -X POST "https://graph.facebook.com/v23.0/${PHONE_NUMBER_ID}/settings" \
  -H "Authorization: Bearer ${SYSTEM_USER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"calling":{"status":"ENABLED","call_icon_visibility":"DEFAULT"}}'
```

Confirm the exact Graph API version and settings shape shown for your Meta app
before running this command; Meta rolls API versions and account capabilities
independently.

## 2. Webhook

Expose the FastAPI port through a stable HTTPS URL. For local testing:

```bash
ngrok http 8000
```

Configure the Meta callback as:

```text
https://YOUR_PUBLIC_HOST/webhook
```

Use `WHATSAPP_VERIFY_TOKEN` as the verification token. Subscribe the WhatsApp
Business Account to both the `messages` and `calls` webhook fields. Keep the
same callback for both fields.

## 3. Environment

Add these values to the existing `.env`; do not create a voice-specific env file:

```dotenv
WHATSAPP_CALLING_ENABLED=true

# Optional overrides. Leave blank to reuse WHATSAPP_ACCESS_TOKEN and
# WHATSAPP_PHONE_NUMBER_ID from chat.
WHATSAPP_CALLING_ACCESS_TOKEN=
WHATSAPP_CALLING_PHONE_NUMBER_ID=
WHATSAPP_CALLING_APP_SECRET=
WHATSAPP_CALLING_API_VERSION=v23.0

SARVAM_API_KEY=replace-me
SARVAM_STT_MODEL=saaras:v3
SARVAM_STT_MODE=transcribe
SARVAM_TTS_MODEL=bulbul:v3
SARVAM_TTS_SPEAKER_ENGLISH=shreya
SARVAM_TTS_SPEAKER_HINDI=shreya
SARVAM_TTS_SPEAKER_MARATHI=shreya
SARVAM_TTS_PACE=1.0
SARVAM_TTS_TEMPERATURE=0.6
```

Saaras v3 defaults to automatic language detection when no fixed language is
provided. The pipeline therefore accepts English, Hindi, Marathi, and code-mixed
speech without a manual language switch. Bulbul v3 does not support pitch or
loudness, so those settings are deliberately absent.

## 4. Start and migrate

```bash
POSTGRES_PORT=5434 docker compose -f docker/docker-compose.yml up --build -d
POSTGRES_PORT=5434 docker compose -f docker/docker-compose.yml exec app python scripts/init_db.py
```

Check readiness:

```bash
curl http://localhost:8000/health
```

Expected when calling is ready:

```json
{"status":"ok","calling_enabled":true,"calling_ready":true}
```

Follow both chat and call events:

```bash
POSTGRES_PORT=5434 docker compose -f docker/docker-compose.yml logs -f app
```

## 5. Troubleshooting

- `calling_enabled=false`: set the flag in `.env` and recreate the app container.
- `calling_enabled=true`, `calling_ready=false`: check Meta token/phone ID,
  `SARVAM_API_KEY`, Pipecat installation, and startup logs.
- Messages work but calls never appear in logs: subscribe the `calls` webhook
  field and confirm the call icon is enabled for the number.
- Neither messages nor calls arrive: update Meta with the current public HTTPS
  callback; free ngrok URLs change after restart.
- A call connects but has no audio: inspect WebRTC/ICE logs and make sure the
  deployment allows outbound UDP and HTTPS/WebSocket traffic.
