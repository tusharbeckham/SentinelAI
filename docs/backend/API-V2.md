# API v2

Expands Phase 6 of [`BACKEND-PLAN.md`](./BACKEND-PLAN.md).

## 1. Versioning rule

`/v1` is frozen. Every route, status code and body shape documented in README
section 2.7 keeps working, and the existing tests keep passing unmodified. New
capability lands on `/v2`.

This costs a little duplication in the router. It is worth it: the console and
the README both document `/v1`, and quietly changing a response shape to add a
field is how integrations break at 3am.

## 2. Routes

| Method | Path | Role | Notes |
| --- | --- | --- | --- |
| GET | `/healthz` | public | liveness, no dependency checks |
| GET | `/readyz` | public | readiness: model loaded and DB writable |
| GET | `/metrics` | public | Prometheus exposition |
| GET | `/openapi.json` | public | hand-maintained 3.1 spec |
| GET | `/v2/alerts` | viewer | cursor pagination, filters, ETag |
| GET | `/v2/alerts/{id}` | viewer | includes attribution |
| POST | `/v2/score` | analyst | idempotent |
| GET | `/v2/cases` | viewer | filter by status, assignee, priority |
| POST | `/v2/cases` | analyst | opens a case from an alert; idempotent |
| GET | `/v2/cases/{id}` | viewer | includes the event history |
| PATCH | `/v2/cases/{id}` | analyst | transition; requires `If-Match` |
| POST | `/v2/cases/{id}/notes` | analyst | append-only |
| POST | `/v2/feedback` | analyst | idempotent |
| POST | `/v2/respond` | responder | idempotent, still dry-run by default |
| GET | `/v2/audit` | admin | paginated |
| GET | `/v2/audit/verify` | admin | walks and recomputes the chain |
| GET | `/v2/models` | viewer | registry listing |
| POST | `/v2/models/{v}/promote` | admin | promotion gate applies |
| GET | `/v2/drift` | viewer | latest PSI snapshot |

## 3. Pagination

Cursor, not offset. `?limit=50&cursor=<opaque>`, response carries
`{"items": [...], "next_cursor": "..."}`.

The cursor is base64 of `{"k": <last sort key>, "id": <last id>}`. Offset
pagination over a table that is being written to skips and duplicates rows as
the offsets shift underneath the reader, which for an alert queue means an
analyst silently never sees some alerts. The `id` tiebreaker is required because
`created_at` is not unique.

`limit` is clamped to 200.

## 4. Idempotency

Every mutating route accepts `Idempotency-Key`. Semantics follow Stripe:

1. First request with a key: execute, store status code and body, return.
2. Replay with same key and **same** body hash: return the stored response
   verbatim, including a 4xx or 5xx. Replaying an error is correct -- the client
   asked what happened to that request, and the answer is what happened.
3. Replay with same key and **different** body: `422`, body
   `{"error": "idempotency_key_reuse"}`. Never the cached response.
4. Keys expire after `SENTINELAI_IDEMPOTENCY_TTL` (default 24h).

A request in flight holds the key row; a concurrent replay gets `409` with
`Retry-After: 1` rather than executing twice.

The key is required on `POST /v2/respond`. Containment actions must never fire
twice because a client retried.

## 5. Concurrency on cases

`GET /v2/cases/{id}` returns `ETag: "<version>"`. `PATCH` requires `If-Match`.
Mismatch is `412 Precondition Failed`. Absent header is `428 Precondition
Required`.

This is the HTTP-native spelling of the optimistic-concurrency counter in
[`DATA-MODEL.md`](./DATA-MODEL.md).

## 6. Errors

One shape, always:

```json
{"error": "invalid_transition",
 "message": "cannot move case from closed to triaging",
 "request_id": "a3f91c...",
 "details": {"from": "closed", "to": "triaging"}}
```

`error` is a stable machine token; `message` is for humans and may be reworded.
`request_id` is in the body as well as the header so a screenshot of a failure
is enough to find the log line.

Status codes: 400 malformed, 401 unauthenticated, 403 wrong role, 404, 409
conflict, 412 precondition failed, 422 semantically invalid, 428 precondition
required, 429 rate limited with `Retry-After`, 503 not ready.

A 401 never distinguishes bad signature from expired from wrong audience in the
response body; the reason goes to the log and to
`sentinelai_auth_failures_total{reason=...}`. Telling an attacker which part of
their forged token was wrong is free help.

## 7. Security headers

Unchanged from v1 and applied to every response including errors:
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Content-Security-Policy: default-src 'none'`, `Cache-Control: no-store`,
`Referrer-Policy: no-referrer`. Plus `X-Request-ID` echoed back.

## 8. OpenAPI

`docs/openapi.json`, hand-maintained, served at `/openapi.json`. A test asserts
that every route in the router appears in the spec and vice versa. Hand-writing
a spec is normally a bad idea because it drifts; the test is what makes it
viable, and it is cheaper than a code-generation dependency that would break the
two-package rule.

## 9. Rate limiting

The existing token bucket, keyed by JWT `sub` rather than IP, since every
mutating route is authenticated and IP is meaningless behind the Space proxy.
`/metrics` and `/healthz` are exempt. `429` carries `Retry-After` in whole
seconds, rounded up.
