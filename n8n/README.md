# n8n Automation Workflows (Feature 8)

These workflows let [n8n](https://n8n.io) drive AlphaPartner's recurring analysis
jobs on a cron schedule, as an external alternative to the in-process
APScheduler. Each workflow is a **Schedule Trigger → HTTP Request** pair that
POSTs to a backend webhook with a shared-secret header.

| Workflow file | Schedule (IST) | Webhook |
|---|---|---|
| `morning_scan_workflow.json` | 08:55, Mon–Fri | `POST /api/webhooks/morning-scan` |
| `evening_summary_workflow.json` | 15:35, Mon–Fri | `POST /api/webhooks/evening-summary` |
| `weekly_review_workflow.json` | Sunday 10:00 | `POST /api/webhooks/weekly-review` |
| `news_digest_workflow.json` | 09:30 & 13:00, Mon–Fri | `POST /api/webhooks/news-digest` |

Each backend webhook validates an `X-Webhook-Key` request header against the
`WEBHOOK_API_KEY` environment variable and returns **403** if it is missing or
wrong. Every successful/failed call is written to the `webhook_logs` MongoDB
collection, which the Settings → **n8n Automation** panel reads via
`GET /api/webhooks/logs` to show "last run" times.

## 1. Start n8n

The included `docker-compose.yml` already defines an `n8n` service:

```bash
docker-compose up -d n8n
```

Then open the editor at **http://localhost:5678** and log in with the basic-auth
credentials from `docker-compose.yml`:

- User: `admin`
- Password: `alphapartner123`

## 2. Set the webhook API key

The backend and n8n must agree on the same secret.

1. In the repo-root `.env`, set a strong value:

   ```env
   WEBHOOK_API_KEY=<a-long-random-string>
   ```

   (See `.env.example` for the placeholder.) The backend reads this on startup;
   restart the backend after changing it.

2. `docker-compose.yml` passes the same `WEBHOOK_API_KEY` into the n8n container,
   and each HTTP Request node reads it via the expression
   `={{ $env.WEBHOOK_API_KEY }}`. So once the env var is set for both services,
   no further editing is needed.

   **Alternative (no env access):** if you prefer not to expose the key as an
   n8n environment variable, open each HTTP Request node and replace the
   `X-Webhook-Key` header value with the literal secret, **or** create an n8n
   *Header Auth* credential (`Name: X-Webhook-Key`, `Value: <secret>`) and switch
   the node's Authentication to "Generic Credential → Header Auth".

## 3. Import the workflows

In the n8n editor: **Workflows → ⋮ (top-right) → Import from File**, then select
each JSON file in this folder (repeat for all four). Or use the CLI inside the
container:

```bash
docker exec -it alphapartner_n8n \
  n8n import:workflow --separate --input=/path/to/n8n
```

After import, open each workflow and toggle it **Active** (top-right switch) so
its schedule starts running. The workflows are exported inactive on purpose.

## 4. Point the HTTP Request node at the backend

The nodes ship with the URL set for the Docker Compose network:

- **Inside docker-compose:** `http://backend:8000/api/webhooks/...` (default — the
  `n8n` service resolves the backend by its service name `backend`).
- **Local dev / n8n outside Docker:** change the host to
  `http://localhost:8000/api/webhooks/...`.
- **Remote backend:** use its public base URL, e.g.
  `https://api.yourdomain.com/api/webhooks/...`.

## 5. Test

Open a workflow and click **Execute Workflow** (or **Test step** on the HTTP
node). A `200` with `{"status":"success", ...}` means the webhook ran; a `403`
means the `X-Webhook-Key` header does not match the backend's `WEBHOOK_API_KEY`.
Confirm the run shows up under Settings → n8n Automation.

## Notes

- Cron expressions are evaluated in **Asia/Kolkata** because the `n8n` service
  sets `GENERIC_TIMEZONE=Asia/Kolkata` (and each workflow pins the same
  timezone), so the times above are already IST.
- These webhooks reuse the exact same service functions as the built-in
  scheduler (`morning_analysis_job`, `eod_report_job`, `generate_weekly_review`,
  `fetch_news`), so running both would double up. Disable the corresponding
  APScheduler jobs if you want n8n to be the sole trigger.
