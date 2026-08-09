# Database (Phases 1–8 — complete)

The Python pipeline is the **sole owner** of the PostgreSQL schema. The Express
gateway stays stateless (it hashes and stores blobs, then hands the pipeline a
resolvable URI).

- **Phase 1 — Foundation:** engine/session wiring, the enum registry, and the
  identity/blob tables (`file_blobs`, `insurers`, `users`).
- **Phase 2 — Ruleset catalogue:** versioned master-LOR storage (`rulesets`,
  `ruleset_claim_types`, `requirement_rules`, `requirement_rule_claim_types`),
  a DB-backed ruleset loader behind `RULESET_SOURCE`, and the `rulesets_cli`
  importer.
- **Phase 3 — Content-addressed blobs:** the gateway stores each upload at
  `uploads/<sha[:2]>/<sha><ext>` and emits `fs://` URIs (with the sha);
  `images.resolve_file_ref` handles `fs://`/`s3://` with a small LRU; and
  `scripts.backfill_blobs` dedupes legacy uploads into `file_blobs`.
- **Phase 4 — Claim persistence (the pivot):** the claim aggregate (`claims`,
  `claim_runs`, `evidence`, `documents`, `line_items` + join tables,
  `claim_notes`). The service is off the in-memory store — claims survive a
  restart. `repository.py` maps rows ↔ `ClaimState`; resume rebuilds an
  inputs-only state (no more `_RESET_ON_RESUME`), so re-runs never duplicate
  warnings. The derived OUTPUT tables (draft packs, LOR packs, reserves) are
  Phase 5; for now the full state + current LOR live in `claims.latest_state` /
  `claims.latest_lor` (JSONB), which back `GET /claim/{ref}` unchanged.
- **Phase 5 — Outputs & LOR:** the derived output tables (`reserve_estimates`,
  `draft_packs` + `qc_results`, `proof_receipts`, `lor_packs` +
  `lor_requirement_results` + `lor_result_documents`). The repository projects
  them from each run's final state (and writes a `lor_packs` row per checklist
  revision at submit / run / override), so every revision and every shipped pack
  is queryable. The graph's `./out/*.json` dump is now optional
  (`write_pack_files`, default off).
- **Phase 6 — Fraud registries (the payoff):** `plausibility_check_node` builds
  its cross-claim hash/serial registries from the DB
  (`repository.find_cross_claim_hashes` / `find_cross_claim_serials`, excluding
  the current claim) instead of the old hardcoded mocks. Reusing a photo or
  serial from a prior claim now rejects the item, citing that claim's ref. The
  lookup is read-only and fail-open (a DB hiccup never blocks a claimant).
- **Phase 7 — Identity & policies:** `policies` (+ `policy_sums_insured`,
  `policy_clauses`), a pluggable `auth.py` (`auth_mode` = disabled/demo/firebase),
  and endpoints `POST /api/v1/policies`, `GET /api/v1/claims`,
  `GET /api/v1/claim/{ref}/lor`. Claims can now carry a `user_id` (resolved from
  an optional bearer token) and a `policy_id`; supplying a policy merges its
  `premises_geo` into the claim, making the geofence check live. Auth is
  **disabled by default** (claims stay anonymous) — non-breaking.
- **Phase 8 — Ops hardening:** `idempotency_keys`, `audit_log`,
  `llm_invocations`, `outbox_messages`, and `claims.retention_expires_at`.
  Submits carrying an `Idempotency-Key` are deduped (a retry returns the same
  claim); a claim-type override writes an `audit_log` row; a proof receipt
  enqueues an `outbox_messages` row (transactional outbox); `invoke_structured`
  optionally records into `llm_invocations` (`record_llm_invocations`, default
  off); and `scripts.retention_gc` deletes expired claims + orphan blobs.

## Local setup

```bash
# 1. Start Postgres 16 (credentials match the DATABASE_URL default in config.py)
docker compose up -d db

# 2. Install deps (from repo root)
pip install -r agentic_pipeline/requirements.txt

# 3. Apply migrations
alembic upgrade head
```

`DATABASE_URL` (SQLAlchemy URL, psycopg v3 driver) overrides the default in
`agentic_pipeline/config.py`. The default is
`postgresql+psycopg://suraksha:suraksha@localhost:5432/suraksha`, matching
`docker-compose.yml`.

Optional SQL UI: http://localhost:8080 (Adminer — server `db`, user/pass `suraksha`).

## Migrations

Hand-authored, reviewed, linear chain under `agentic_pipeline/migrations/versions/`:

| Rev | What |
|-----|------|
| `0001_extensions` | `pgcrypto` (gen_random_uuid), `citext` |
| `0002_enums` | all 11 closed-set enum types (frozen label snapshot) |
| `0003_identity_blobs` | `file_blobs`, `insurers`, `users` |
| `0004_rulesets` | `rulesets` + `ruleset_claim_types` + `requirement_rules` + `requirement_rule_claim_types` |
| `0005_claims` | `claims`, `claim_runs`, `evidence`, `documents`, `line_items` (+ joins), `claim_notes` |
| `0006_outputs` | `reserve_estimates`, `draft_packs`, `qc_results`, `proof_receipts`, `lor_packs`, `lor_requirement_results`, `lor_result_documents` |
| `0007_policies` | `policies`, `policy_sums_insured`, `policy_clauses` |
| `0008_ops` | `idempotency_keys`, `audit_log`, `llm_invocations`, `outbox_messages`; `claims.retention_expires_at` |

Common commands (run from repo root):

```bash
alembic upgrade head          # apply
alembic downgrade base        # tear down
alembic history               # show the chain
alembic revision -m "msg"     # scaffold a new revision — then HAND-REVIEW it
alembic check                 # fail on un-migrated model drift
```

Rules (see the DB plan §5):
- Every revision needs a working `downgrade()`.
- **Adding an enum value** is its own revision (`ALTER TYPE ... ADD VALUE`) and
  is not reversible in a transaction on older PG. Never edit `0002_enums`.
- The enum label snapshot in `0002_enums.py` must stay equal to
  `agentic_pipeline/models.ENUM_LABELS` — `test_enum_parity` enforces this.

## Rulesets (Phase 2)

Master LORs are versioned and immutable per `(slug, version)`, with at most one
`active` version per slug. `requirements.load_ruleset` reads from either source
depending on `RULESET_SOURCE` (config `ruleset_source`):

- `file` (default) — the JSON under `agentic_pipeline/rulesets/`. Keeps local
  dev and the pure-function tests DB-free.
- `db` — the catalogue tables. A DB reconstruction is **byte-identical** to the
  JSON it was imported from, so `GET /api/v1/requirements/default` is unchanged.

Both paths **fail open**: a load error logs and returns `None` (claim proceeds).

Import → activate is a two-step, so hand corrections in a draft are protected
structurally (no `--force` overwrite):

```bash
export DATABASE_URL=postgresql+psycopg://suraksha:suraksha@localhost:5432/suraksha
python -m agentic_pipeline.rulesets_cli import-json agentic_pipeline/rulesets/default.json
python -m agentic_pipeline.rulesets_cli activate default 2026-08-09
python -m agentic_pipeline.rulesets_cli list
python -m agentic_pipeline.rulesets_cli diff default 2026-08-09 2026-09-01
```

To serve rulesets from the DB at runtime, set `RULESET_SOURCE=db` (and import +
activate at least the `default` ruleset first).

## Blob store (Phase 3)

Uploads are content-addressed: a file with content hash `sha` lives at
`<blob_store_root>/<sha[:2]>/<sha><ext>` (default root `backend/uploads`,
config `blob_store_root`). The gateway writes there and sends the pipeline an
`fs://<abspath>` URI plus the `sha256`. The layout is defined once in
`agentic_pipeline/blobs.py` and mirrored in `backend/server.js`. Keeping the
extension lets `images.resolve_file_ref` derive a mime type without a DB lookup;
it also handles `s3://` (lazy boto3) and caches recent blobs in a small LRU.

Backfill legacy flat uploads into the layout + `file_blobs` (idempotent; also
reconciles already-sharded blobs whose rows are missing):

```bash
export DATABASE_URL=postgresql+psycopg://suraksha:suraksha@localhost:5432/suraksha
python -m scripts.backfill_blobs --dry-run   # report dedupe, touch nothing
python -m scripts.backfill_blobs             # move + insert rows
```

## Identity & policies (Phase 7)

`auth_mode` (config / `AUTH_MODE`) selects how a bearer token becomes a `user_id`:

- `disabled` (default) — no identity; claims are anonymous. Nothing changes.
- `demo` — the bearer token **is** the user subject (dev/testing only).
- `firebase` — verify a Firebase ID token via `firebase-admin` (needs
  `GOOGLE_APPLICATION_CREDENTIALS`; install the extra: `pip install firebase-admin`).

Auth is **fail-open**: a verification error leaves the claim anonymous rather
than rejecting it. Endpoints:

- `POST /api/v1/policies` → `{policy_id}` (captures `premises_lat/lon`, sums, clauses).
- `GET /api/v1/claims?limit=&cursor=` → the caller's claim history (token-scoped;
  `?user_id=` accepted for dev).
- `GET /api/v1/claim/{ref}/lor` → latest LOR pack only.

The gateway forwards `Authorization` and `Idempotency-Key` to the pipeline and
proxies `/api/policies`, `/api/claims`, `/api/claim/:id/lor`.

**App-side wiring (done in `learningdart`, verify on the emulator):** the Flutter
app now sends a stable bearer subject (`lib/services/identity.dart`: Firebase
`uid`, else a device id) + an `Idempotency-Key` on submit; onboarding POSTs to
`/api/policies` and stores `policy_id` (sent on submit → geofence terms); the
dashboard loads history from `GET /api/claims` and hydrates a claim's full state
on tap. The unsent PII (§11) is left untransmitted with a code note.

**Backend `auth_mode` to pair with the app:** the app sends the Firebase `uid`
as the subject, so run `auth_mode="demo"` for dev (token == subject). For real
token verification, set `auth_mode="firebase"` **and** change
`identity.subjectToken()` to return `await user.getIdToken()`. With
`auth_mode="disabled"` the app still runs but claims stay anonymous and
`GET /api/claims` returns `[]`.

## Ops (Phase 8)

**Idempotency:** send an `Idempotency-Key` header on submit; a retry with the
same key returns the same claim (no duplicate). Keys expire after 24h.

**Audit:** claim-type overrides write an `audit_log` row
(`action='claim_type.override'`, with before/after). Add `write_audit(...)` calls
for other privileged actions (e.g. ruleset activation) as needed.

**Outbox:** a proof-of-intimation receipt enqueues an `outbox_messages` row
(`topic='insurer.intimation'`, `status='pending'`) in the same transaction. A
future worker drains `outbox_due` and POSTs to the insurer intake API.

**LLM audit trail:** set `record_llm_invocations=true` to log every
`invoke_structured` call into `llm_invocations` (provider, model, latency,
success, run_id). Off by default.

**Retention GC** (nightly): deletes claims past `retention_expires_at` (cascades
to all children) and removes unreferenced `file_blobs` (+ their on-disk files):

```bash
python -m scripts.retention_gc --dry-run
python -m scripts.retention_gc
```

Nothing sets `retention_expires_at` automatically yet — set it per policy/tenant
when a retention policy is decided.

## Backups & restore drill

Daily logical dump + point-in-time recovery via WAL archiving; version the blob
bucket separately. **An untested backup is not a backup** — rehearse a restore
before go-live:

```bash
# Dump (custom format)
pg_dump -Fc -U suraksha -h localhost -p 5432 suraksha > suraksha_$(date +%F).dump

# Restore into a scratch database and verify it comes up at head
createdb -U suraksha -h localhost -p 5432 suraksha_restore
pg_restore -U suraksha -h localhost -p 5432 -d suraksha_restore suraksha_YYYY-MM-DD.dump
DATABASE_URL=postgresql+psycopg://suraksha:suraksha@localhost:5432/suraksha_restore \
  alembic current          # must print the latest revision (currently 0008_ops)
```

Confirm row counts on the key tables (`claims`, `file_blobs`, `lor_packs`) match
the source, then drop the scratch DB. For the portable dev server the binaries
live in `~/pgsql/bin` (pg_dump/pg_restore/createdb).

## Tests

Pure-function tests need no DB. DB-backed tests (migration round-trip, model
drift, live enum parity) **skip** unless `TEST_DATABASE_URL` points at a
**throwaway** database — the round-trip runs `downgrade base`, which drops every
table, so never point it at a real DB.

```bash
# DB-free (always runs)
pytest agentic_pipeline/tests/

# With a throwaway Postgres (enables the migration + parity tests)
export TEST_DATABASE_URL=postgresql+psycopg://suraksha:suraksha@localhost:5432/suraksha
pytest agentic_pipeline/tests/test_migrations.py agentic_pipeline/tests/test_enum_parity.py
```

## CI gate

```yaml
# GitHub Actions sketch
services:
  postgres:
    image: postgres:16
    env: { POSTGRES_USER: suraksha, POSTGRES_PASSWORD: suraksha, POSTGRES_DB: suraksha }
    ports: ["5432:5432"]
    options: >-
      --health-cmd "pg_isready -U suraksha" --health-interval 5s
      --health-timeout 5s --health-retries 10
steps:
  - run: pip install -r agentic_pipeline/requirements.txt
  - run: pytest agentic_pipeline/tests/
    env:
      # Ruleset DB tests use the app engine, so both must point at the same DB.
      DATABASE_URL: postgresql+psycopg://suraksha:suraksha@localhost:5432/suraksha
      TEST_DATABASE_URL: postgresql+psycopg://suraksha:suraksha@localhost:5432/suraksha
```
