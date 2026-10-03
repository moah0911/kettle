# Webhook intake contract

Kettle does not speak raw provider webhooks. Every provider route expects a
small relay envelope, verified by the provider's own signature scheme. Pointing
a provider directly at these routes verifies the signature and then drops the
event — loudly (422), not silently.

The reason is shape, not security. GitHub, Slack, Linear, and Jira each nest
the same facts differently (`issue` vs `data` vs `event`), version their
payloads independently, and (Slack) require a subscription handshake. Normalizing
once, in a relay you own, keeps provider churn out of the intake path.

## The rule

1. Verify the signature first. Every route 401s on a bad signature before
   reading the body.
2. Then match the envelope. A well-signed body in the wrong shape gets a 422
   that names this file. That is deliberate: a 200 that drops the event is how
   integrations fail for weeks unnoticed.

## GitHub — `POST /webhooks/github`

Expects:

```json
{
  "type": "github.issue_labeled",
  "context": {
    "issue_id": "7",
    "title": "Bug",
    "body": "...",
    "repo": "acme/app",
    "labels": ["factory"],
    "author_role": "MEMBER"
  }
}
```

- `type` selects the automation (`github.issue_labeled`,
  `github.comment`, `github.check_suite.failed`).
- `context.labels` must include `factory`, and `context.author_role` must be
  `OWNER`, `MEMBER`, or `COLLABORATOR` — otherwise the item is refused and the
  response says why.
- Intake stamps GitHub items **untrusted**. The workflow's gates, not the
  label, are the spend control.
- A body with `action`/`issue`/`repository` and no `type` is a raw GitHub
  delivery → 422. Relay it into the envelope above.

## Slack — `POST /webhooks/slack`

Expects the flat envelope:

```json
{ "text": "fix the login redirect", "repo": "acme/app", "event_id": "e1",
  "channel": "general", "user": "u" }
```

- `repo` is required and must be in the factory allowlist.
- The subscription handshake is answered: a signed `{"type":
  "url_verification", "challenge": "..."}` gets `{"challenge": "..."}` back.
  Without this, Slack never enables the subscription.
- A body with a nested `event` object is a raw Slack delivery → 422.

Slack items are stamped **trusted** (workspace membership is the
authentication). Treat the signing secret accordingly.

## Linear — `POST /webhooks/linear`

Expects:

```json
{ "title": "...", "body": "...", "repo": "acme/app", "issue_id": "lin-9" }
```

A body with a top-level `data` object is a raw Linear delivery → 422.
Trusted, like Slack.

## Jira — `POST /webhooks/jira`

Expects:

```json
{ "title": "...", "body": "...", "repo": "acme/app", "key": "J-9" }
```

A body with `webhookEvent` is a raw Jira delivery → 422. Trusted, like Slack.

## Custom — `POST /webhooks/custom`

API-key authenticated (not signature-verified) and already flat:

```json
{ "title": "...", "body": "...", "repo": "acme/app", "labels": [] }
```

This is the reference shape: if you are writing a relay for any provider
above, emit this.

## After intake

Accepted intake dispatches the coordinator when `TEMPORAL_HOST` is configured;
every intake response carries `dispatched: true|false`. Without Temporal the
item waits for a manual `POST /v1/work-items/{id}/dispatch`. Nothing in the
intake path merges, closes, or approves anything — see `docs/pr-review-checklist.md`
for what the review gate requires.