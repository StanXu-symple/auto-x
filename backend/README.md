# X Sentinel backend

FastAPI API and independent asyncio workers for monitoring X accounts and creating AI drafts. MySQL is the source of
truth; Redis provides distributed polling locks, global X API gates, login throttling, and worker
heartbeat data. Manual-trigger tokens and pagination checkpoints are persisted in MySQL.
Poll commits are fenced by both a database generation and a renewable Redis lease.

## Commands

```bash
pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000
python -m app.worker
python -m app.ai_worker
```

Copy `.env.example` to `.env` and set the database, Redis, JWT, administrator, and X bearer-token
values. `AUTO_CREATE_TABLES=true` offers an idempotent first-run path; production deployments can
run Alembic and set it to `false`.

Revision `0002_polling_fencing` adds resumable pagination and fencing fields with MySQL 5.7
compatible `ALTER TABLE ... ADD COLUMN` operations. Run migrations as a one-shot before starting
production replicas; the worker never creates schema or seeds the administrator.
Revision `0003_ai_creation` adds AI settings, editable Skills, fenced generation jobs, and drafts
using MySQL 5.7-compatible JSON/DDL. It also seeds the editable `观点提炼`, `中文短帖`, and
`线程拆分` Skills.

## HTTP surface

All JSON API routes use `/api/v1`: administrator login, dashboard summary, monitored-user CRUD and
pause/resume/poll actions, tweet history, polling logs, dynamic settings, JSON system metrics, and
liveness/readiness. Prometheus exposition is available at `/metrics`.
The worker exposes its process-local polling counters and histograms on port `8001`; Prometheus
should scrape `backend:8000/metrics`, `worker:8001/metrics`, and the AI worker on port `8002`.
The AI worker runs with `python -m app.ai_worker`, writes heartbeat
`xsentinel:ai-worker:heartbeat`, and exposes process-local metrics on port `8002`. Provider API
keys are environment-only AI-worker secrets; the API reports readiness from the heartbeat and
never accepts or returns key material. Runtime provider URLs live in the database and are managed
through `/api/v1/ai/settings`. Every destination host must also appear in
`AI_ALLOWED_PROVIDER_HOSTS`; HTTP redirects are not followed.

AI routes include `/api/v1/ai/settings`, `/ai/skills`, `/ai/jobs`, `/ai/drafts/{id}`, and
`/api/v1/tweets/{tweet_id}/generate`. Automatic jobs are inserted in the same transaction as a
new tweet. Each job freezes its selected Skill IDs, instructions, version, provider settings, and
source snapshot before execution.

The optional Codex Bridge receives this versioned minimum request (plus model/output controls):

```json
{
  "protocol": "x-sentinel-codex/1",
  "task": {"id": "42", "type": "compose_x_post"},
  "source": {"boundary": "BEGIN_UNTRUSTED_SOURCE", "text": "...", "end_boundary": "END_UNTRUSTED_SOURCE"},
  "skills": [{"id": 1, "name": "观点提炼", "version": 1, "instructions": "..."}],
  "instructions": "...",
  "input": "...",
  "output_schema": {"type": "object"}
}
```

It may respond with `{"draft":{"title":"...","content":"...","excerpt":null,"metadata":null}}`
or the same object under `output`.

Errors have a stable shape:

```json
{"error":{"code":"...","message":"...","details":null,"request_id":"..."}}
```
