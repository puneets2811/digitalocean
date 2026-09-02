# API Contract

Base URL (local): `http://localhost:8000`  
Base URL (live Droplet example): `http://<DROPLET_IP>:8000`  
Content-Type: `application/json`  
Interactive docs: `GET /docs` (OpenAPI / Swagger UI)

## Authentication

Protected routes require:

```http
Authorization: Bearer <API_KEY>
```

`API_KEY` comes from the service environment (see `.env.example`).  
Unauthenticated or invalid key → `401 Unauthorized`.

| Route | Auth |
|-------|------|
| `GET /health` | No |
| `GET /ready` | No |
| All other routes below | Yes |

---

## Endpoints

### `GET /health`

Liveness probe.

**Response `200`**
```json
{ "status": "ok" }
```

---

### `GET /ready`

Readiness: SQLite and RabbitMQ reachable.

**Response `200`**
```json
{
  "status": "ready",
  "database": { "ok": true, "error": null },
  "rabbitmq": { "ok": true }
}
```

**Response `503`** — same shape with `"status": "not_ready"` and error details in `detail`.

---

### `POST /events`

Durably accept an event (persist to SQLite, publish to RabbitMQ with confirm).

**Request body**
```json
{
  "type": "order.created",
  "source": "checkout",
  "payload": { "order_id": "123", "status": "paid" }
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `type` | string | yes | 1–256 chars |
| `source` | string | yes | 1–256 chars |
| `payload` | object | no | Arbitrary JSON object; default `{}` |

**Response `202 Accepted`**
```json
{
  "id": "c265ef0c-0186-4b99-b3da-050b8b818c80",
  "status": "accepted"
}
```

**Errors**
| Status | When |
|--------|------|
| `401` | Missing/invalid bearer |
| `422` | Validation error |
| `503` | RabbitMQ publish failed (event may be stored as `publish_failed`) |

---

### `GET /events/{event_id}`

Fetch a stored event.

**Response `200`**
```json
{
  "id": "c265ef0c-0186-4b99-b3da-050b8b818c80",
  "type": "order.created",
  "source": "checkout",
  "payload": { "order_id": "123", "status": "paid" },
  "status": "accepted",
  "created_at": "2026-09-02T08:29:56.000000+00:00"
}
```

`status` values include: `accepted`, `publish_failed`.

**Errors:** `401`, `404`

---

### `POST /subscriptions`

Register a webhook subscription. Filters are evaluated without restart.

**Request body**
```json
{
  "url": "https://webhook.site/your-uuid",
  "type_filter": "order.created",
  "source_filter": "checkout",
  "payload_conditions": [
    { "path": "status", "eq": "paid" }
  ],
  "secret": "optional-hmac-secret"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `url` | string (URL) | yes | Webhook endpoint |
| `type_filter` | string \| null | no | Default `"*"`. Exact match, or `"*"` / omit = any |
| `source_filter` | string \| null | no | Default `"*"`. Exact match, or `"*"` / omit = any |
| `payload_conditions` | array | no | AND of equality checks; default `[]` |
| `payload_conditions[].path` | string | yes | Dotted path into event `payload` |
| `payload_conditions[].eq` | any | yes | Expected value |
| `secret` | string \| null | no | HMAC secret; default = `WEBHOOK_DEFAULT_SECRET` |

**Match rule:** subscription matches if type filter matches **and** source filter matches **and** all payload conditions match.

**Response `201 Created`**
```json
{
  "id": "f5ab473f-bd13-4abd-97be-a37694b6f004",
  "url": "https://webhook.site/your-uuid",
  "type_filter": "order.created",
  "source_filter": "checkout",
  "payload_conditions": [{ "path": "status", "eq": "paid" }],
  "active": true,
  "created_at": "2026-09-02T08:29:32.944925+00:00"
}
```

Note: `secret` is not returned.

**Errors:** `401`, `422`

---

### `GET /subscriptions`

List all subscriptions (newest first).

**Response `200`** — array of subscription objects (same shape as create response).

**Errors:** `401`

---

### `DELETE /subscriptions/{subscription_id}`

Remove a subscription.

**Response `204 No Content`** — empty body.

**Errors:** `401`, `404`

---

### `GET /events/{event_id}/deliveries`

Delivery audit for one event (each matching subscription).

**Response `200`**
```json
[
  {
    "id": "147c8620-e600-4b03-8b19-a08a012a99ed",
    "event_id": "c265ef0c-0186-4b99-b3da-050b8b818c80",
    "subscription_id": "f5ab473f-bd13-4abd-97be-a37694b6f004",
    "status": "delivered",
    "created_at": "2026-09-02T08:29:56.063107+00:00",
    "updated_at": "2026-09-02T08:29:56.554882+00:00",
    "attempts": [
      {
        "id": 1,
        "attempt_no": 1,
        "at": "2026-09-02T08:29:56.549401+00:00",
        "http_status": 200,
        "error": null,
        "duration_ms": 470
      }
    ]
  }
]
```

`status`: `pending` | `delivered` | `failed`

Empty array if the event exists but no subscriptions matched.

**Errors:** `401`, `404` (unknown event)

---

### `GET /subscriptions/{subscription_id}/deliveries`

Delivery audit for one subscription (newest first). Same delivery object shape as above.

**Errors:** `401`, `404` (unknown subscription)

---

### `GET /deliveries/{delivery_id}`

Single delivery with attempts.

**Errors:** `401`, `404`

---

## Webhook delivery contract (outbound)

When a subscription matches, the consumer `POST`s to the subscription `url`.

**Headers**
| Header | Value |
|--------|--------|
| `Content-Type` | `application/json` |
| `X-Event-Id` | Event UUID |
| `X-Webhook-Signature` | `sha256=<hex>` HMAC-SHA256 of raw body using subscription secret |

**Body**
```json
{
  "id": "<event_id>",
  "type": "order.created",
  "source": "checkout",
  "payload": { },
  "created_at": "2026-09-02T08:29:56.000000+00:00"
}
```

**Success:** HTTP `2xx`  
**Retries:** up to 5 attempts with exponential backoff (1s, 2s, 4s, 8s, 16s)  
**Timeout:** 5 seconds per attempt (configurable)

---

## Quick curl examples

```bash
export API=http://localhost:8000
export AUTH="Authorization: Bearer $API_KEY"

curl -sS "$API/health"

curl -sS -X POST "$API/subscriptions" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"url":"https://webhook.site/UUID","type_filter":"order.created","source_filter":"*"}'

curl -sS -X POST "$API/events" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"type":"order.created","source":"checkout","payload":{"status":"paid"}}'

curl -sS "$API/subscriptions" -H "$AUTH"

curl -sS "$API/events/<EVENT_ID>/deliveries" -H "$AUTH"
```
