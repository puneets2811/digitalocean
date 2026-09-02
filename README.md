# Event ingestion and notification fanout

Production-shaped FastAPI service that accepts structured events, matches subscription rules, and delivers signed webhooks with retries and queryable delivery history.

**API contract:** see [API.md](API.md) (auth, endpoints, webhook payload, status codes).

## Stack

- FastAPI (ingest + subscription + delivery query APIs)
- RabbitMQ (durable work handoff after accept)
- SQLite (events, subscriptions, deliveries, attempts) — single-node
- Docker Compose (api + consumer + RabbitMQ)

## Quick start (local)

```bash
cp .env.example .env
mkdir -p data
docker compose up --build
```

API: http://localhost:8000  
RabbitMQ UI: http://127.0.0.1:15672 (guest/guest; bound to localhost only)  
Auth: `Authorization: Bearer dev-api-key-change-me` (from `.env`)

AMQP (`5672`) is not published to the host — only the Compose network reaches RabbitMQ.

## Deploy on a DigitalOcean Droplet

Single-Droplet Compose deploy (api + consumer + RabbitMQ + SQLite volume).

1. Create an Ubuntu Droplet and SSH in.
2. Clone this repo and enter the project directory.
3. Install Docker Engine + Compose plugin:

```bash
bash docker-install.sh
```

   The script falls back to the `noble` apt suite if your Ubuntu codename is not in Docker’s repo yet.

4. Configure secrets (change defaults on the Droplet):

```bash
cp .env.example .env
# Edit .env — set strong API_KEY and WEBHOOK_DEFAULT_SECRET
mkdir -p data
```

5. Start the stack:

```bash
docker compose up --build -d
```

   Services use `restart: unless-stopped` so they come back after reboot.

6. Firewall: allow **22** and **8000/tcp** only. Do **not** open `5672` or `15672` publicly (AMQP is internal; management UI is localhost-only).

7. Verify:

```bash
curl http://DROPLET_IP:8000/health
```

8. Optional — RabbitMQ management UI via SSH tunnel:

```bash
ssh -L 15672:127.0.0.1:15672 user@DROPLET_IP
# then open http://127.0.0.1:15672 on your laptop (guest/guest)
```

## API

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/events` | yes | Durable ingest → `202 {id}` |
| GET | `/events/{id}` | yes | Fetch event |
| POST | `/subscriptions` | yes | Register webhook + filters |
| GET | `/subscriptions` | yes | List subscriptions |
| DELETE | `/subscriptions/{id}` | yes | Delete subscription |
| GET | `/events/{id}/deliveries` | yes | Deliveries + attempts for event |
| GET | `/subscriptions/{id}/deliveries` | yes | Deliveries + attempts for subscription |
| GET | `/deliveries/{id}` | yes | Single delivery + attempts |
| GET | `/health` | no | Liveness |
| GET | `/ready` | no | DB + RabbitMQ readiness |

### Event

```json
{
  "type": "order.created",
  "source": "checkout",
  "payload": { "order_id": "123", "status": "paid" }
}
```

### Subscription filters

- `type_filter` / `source_filter`: omit or `"*"` matches any; otherwise exact match
- `payload_conditions`: list of `{ "path": "order.status", "eq": "paid" }` (dotted path, AND)

```json
{
  "url": "https://webhook.site/your-id",
  "type_filter": "order.created",
  "source_filter": "checkout",
  "payload_conditions": [{ "path": "status", "eq": "paid" }],
  "secret": "optional-hmac-secret"
}
```

Webhook POST body: `{id, type, source, payload, created_at}`  
Headers: `X-Event-Id`, `X-Webhook-Signature: sha256=<hmac>`

## Curl demo

```bash
export API=http://localhost:8000
export AUTH="Authorization: Bearer dev-api-key-change-me"

# 1) Register a subscription (use webhook.site or any echo URL)
curl -sS -X POST "$API/subscriptions" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{
    "url": "https://webhook.site/YOUR-UUID",
    "type_filter": "order.created",
    "source_filter": "checkout",
    "payload_conditions": [{"path": "status", "eq": "paid"}]
  }'

# 2) Ingest a matching event
EVENT=$(curl -sS -X POST "$API/events" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"type":"order.created","source":"checkout","payload":{"order_id":"123","status":"paid"}}')
echo "$EVENT"
EVENT_ID=$(python -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$EVENT")

# 3) Non-matching event (wrong payload)
curl -sS -X POST "$API/events" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"type":"order.created","source":"checkout","payload":{"order_id":"124","status":"pending"}}'

# 4) Query delivery history
sleep 2
curl -sS "$API/events/$EVENT_ID/deliveries" -H "$AUTH" | python -m json.tool
```

Delivery statuses: `pending` → `delivered` or `failed`. Each attempt stores timestamp, HTTP status, and error.

## Delivery guarantees

- Event row written to SQLite before RabbitMQ publish
- Publisher confirms on publish; failure → event `publish_failed` and HTTP 503
- Persistent messages, durable exchange/queue, DLQ for poison worker failures
- Webhook retries: 5 attempts, exponential backoff (1s, 2s, 4s, 8s, 16s)
- At-least-once: unique `(event_id, subscription_id)` skips completed deliveries on redelivery

## Observability

- Structured logs with `event_id`, `delivery_id`, attempt number, HTTP status
- Queryable delivery + attempt history via API
- `/health` and `/ready`

## Trade-offs

### SQLite vs Postgres

**Chose SQLite** for zero ops on a single Droplet. API and consumer share one file via a bind mount (`./data`).

- **Pro:** Simple deploy, durable local audit, enough for assessment traffic  
- **Con:** Not multi-replica safe; write contention under load; **single point of failure** — if the DB is down, ingest and fanout both stop  

Scale-out path: Managed Postgres (or similar) with the same schema; app code stays mostly the same.

### RabbitMQ as work buffer, not event store

Messages carry `{event_id}` only. Full payload and subscriptions live in SQLite.

- **Pro:** Small queue messages; one source of truth for what we deliver; clear delivery audit in DB  
- **Con:** Consumer **must** read SQLite to fan out; MQ alone cannot reconstruct the event; DB outage leaves ids stranded in the queue  

Alternative: put the full event on the bus so ingest can succeed when the DB is briefly down, and use SQLite mainly for subscriptions + delivery audit.

### Dual-write on ingest (DB then MQ)

We commit the event row (`status=accepted`), then publish with confirms. Publish failure → `publish_failed` + HTTP `503`.

- **Pro:** Never enqueue work without a durable event row; client knows publish failed  
- **Con:** Classic dual-write: no cross-system transaction; client retries can create **new UUIDs** (no idempotency key yet); possible `accepted` row with no message if we crash after publish but before response  

Production path: transactional **outbox** and/or `Idempotency-Key`.

### At-least-once webhook delivery

Consumer acks after processing; HTTP may succeed before we mark `delivered`; retries and redelivery can duplicate POSTs.

- **Pro:** Prefer “subscriber may see it twice” over silent loss  
- **Con:** Not exactly-once; subscribers should dedupe on `X-Event-Id` / event `id`  

`UNIQUE(event_id, subscription_id)` limits duplicate delivery **rows**, not duplicate HTTP calls in every crash window.

### Soft-delete subscriptions

`DELETE /subscriptions/{id}` sets `active=0`. Delivery history stays; fanout ignores inactive rows.

- **Pro:** Audits survive unregistering a webhook  
- **Con:** Rows accumulate; list API shows active only (inactive still queryable via delivery endpoints)

### Auth and ops scope

Shared bearer `API_KEY` and Compose-on-one-Droplet are intentional for the assessment — not multi-tenant JWT, rate limits, or HA.

Also by design here: **best-effort ordering**, and payload filters limited to **dotted-path equality** (AND), not full JSONLogic.

## Local run without rebuilding images

Requires RabbitMQ reachable at `RABBITMQ_URL` (Compose RabbitMQ or managed):

```bash
cp .env.example .env
# For host-local API/consumer talking to Compose RabbitMQ:
# RABBITMQ_URL=amqp://guest:guest@localhost:5672/
# DATABASE_PATH=./data/webhooks.db

python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
mkdir -p data
uvicorn app.main:app --host 0.0.0.0 --port 8000
# other terminal:
python -m consumer.worker
```

Filter / unit / integration tests (no RabbitMQ; broker is mocked):

```bash
source .venv/bin/activate
pytest tests/ -q
```

## Pointing at DigitalOcean Managed RabbitMQ

Set `RABBITMQ_URL` in `.env` to your DO AMQP URL and run only `api` + `consumer` (or keep Compose RabbitMQ for local demos).
