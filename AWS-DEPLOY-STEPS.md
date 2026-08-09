# Deploy SurakshaDraft to AWS EC2 for a ~1-day demo

## Context

You want the backend running on AWS temporarily (~24h) against $100 of unspent
credits, at minimum cost. The repo already contains a complete production stack
(`docker-compose.prod.yml`, both Dockerfiles, `nginx.prod.conf`,
`scripts/ec2_bootstrap.sh`) and a 14-section runbook in
[DEPLOY.md](DEPLOY.md) that targets exactly this shape: one EC2 box running
Postgres + Redis + FastAPI + Celery + the Node gateway + nginx under Compose,
with real S3 for blobs and no managed services (RDS/ElastiCache/ALB are what
would actually cost money).

So this is **not** a "build a deployment" task — it's "execute the existing
runbook, with the four decisions filled in and three latent gaps closed."

**Total cost for 24h on the chosen config: ~$0.55.** The real risk isn't spend,
it's forgetting to delete something afterwards — §12 below is the part that
protects the credits.

### Decisions locked in

| Decision | Choice | Consequence |
|---|---|---|
| Instance | `t3.small` (2 GiB), `ap-south-1` | ~$0.54/day. No swap thrash, faster build. Uncomment the `MEM_*` block. |
| LLM | `gemini` | Reuses the `GOOGLE_API_KEY` already in your local `.env`. No watsonx signup. |
| Auth | `demo` | App works end-to-end incl. claim history. Bearer token *is* the user id — fine for a private one-day demo, do not leave running. |
| Rulesets | `file` (not `db`) | Avoids an unseeded catalogue; see gap #2. |

### Three gaps in DEPLOY.md this plan closes

1. **`AUTH_MODE`** — `.env.prod.example` ships `firebase`, but
   [identity.dart:26-37](mobile_app/lib/services/identity.dart#L26-L37) sends the
   raw Firebase **uid**, not a signed ID token. `verify_id_token` would reject it,
   [auth.py:63-65](agentic_pipeline/auth.py#L63-L65) fails open to `None`, and
   [service.py:211-213](agentic_pipeline/service.py#L211-L213) then returns
   `{"claims": []}` forever. → use `demo`.
2. **`RULESET_SOURCE=db`** in `.env.prod.example` requires a seeding step the
   runbook never mentions; without it `_load_db` returns `None` and the LOR
   gate has no master ruleset. → use `file` (the code default;
   `test_rulesets_db.py` asserts the two sources are byte-identical). Optional
   seeding commands are in the appendix.
3. **`LLM_PROVIDER=watsonx`** default with empty keys → every LLM call fails.
   → set `gemini` + your key.

Also worth knowing: **`.env` was never committed** (verified with
`git log --all -- .env`) — only `.env.prod.example` is tracked. Your public repo
has no leaked keys. `.env.prod` gets created on the server and stays there.

---

## Step 1 — CloudShell

No AWS CLI or Docker needed on your laptop. Open CloudShell (`>_` icon, top bar
of the AWS console). **Set the region selector to Mumbai `ap-south-1` first** —
everything must live in one region.

```bash
export AWS_REGION=ap-south-1
export BUCKET=suraksha-blobs-$(aws sts get-caller-identity --query Account --output text)
echo "$AWS_REGION / $BUCKET"
```

Keep this tab open. If CloudShell times out, re-export both vars.

## Step 2 — Budget alarm (do it before anything else)

Console → **Billing and Cost Management → Budgets → Create budget → Customize →
Cost budget**. Monthly, **$15**, alerts at 50/80/100% of *actual* to your email.
In the cost-scope options choose to **include credits** rather than net them out
— a credit-netted budget reads $0 right up until the credits are gone.

This also completes the *Set up a cost budget* console activity, which earns
credits. Keep the budget after teardown; it's the backstop for anything missed.

## Step 3 — S3 bucket

```bash
aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_REGION" \
  --create-bucket-configuration LocationConstraint="$AWS_REGION"

aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

aws s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
```

## Step 4 — IAM instance role

Gives the box S3 access without writing long-lived keys to disk — this is why
`S3_ACCESS_KEY`/`S3_SECRET_KEY` stay empty in Step 8. Run the block from
[DEPLOY.md §4](DEPLOY.md) verbatim (creates role `suraksha-ec2`, inline policy
scoped to this one bucket, attaches `AmazonSSMManagedInstanceCore`, creates and
populates the instance profile).

## Step 5 — Security group

Port 80 only, no SSH — shell comes from Session Manager. [DEPLOY.md §5](DEPLOY.md)
verbatim. Note the `echo "SG=$SG"` at the end; you need `$SG` in the next step.

## Step 6 — Launch the instance

```bash
AMI=$(aws ssm get-parameter \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query 'Parameter.Value' --output text)

aws ec2 run-instances \
  --image-id "$AMI" \
  --instance-type t3.small \
  --security-group-ids "$SG" \
  --iam-instance-profile Name=suraksha-ec2 \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":20,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --metadata-options '{"HttpTokens":"required"}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=suraksha}]' \
  --query 'Instances[0].InstanceId' --output text
```

`DeleteOnTermination:true` matters — it's what stops an orphaned EBS volume
billing forever. `HttpTokens=required` (IMDSv2) closes the SSRF→credential-theft
path, which is worth having on a box whose job is fetching user-supplied images.

Wait ~60s, then grab the IP and open a shell:

```bash
IID=$(aws ec2 describe-instances --filters Name=tag:Name,Values=suraksha \
      Name=instance-state-name,Values=running \
      --query 'Reservations[0].Instances[0].InstanceId' --output text)

aws ec2 describe-instances --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text   # save this

aws ssm start-session --target "$IID"
```

`start-session` failing means the SSM agent hasn't registered yet — wait a
minute, retry. Then `sudo su - ec2-user`.

## Step 7 — Prepare the machine

On the instance:

```bash
sudo dnf install -y git
git clone https://github.com/dev-khanna/surakshadraft.git suraksha
cd suraksha
sudo bash scripts/ec2_bootstrap.sh
exit          # then re-enter: sudo su - ec2-user
docker ps && free -h    # expect empty container list, 4.0Gi swap
```

`ec2_bootstrap.sh` adds 4 GB swap, installs Docker + Compose v2 plugin, and caps
container log sizes. On `t3.small` the swap is insurance rather than mandatory,
but leave it — the pip install of the langchain tree peaks over 1 GiB.

## Step 8 — Configure `.env.prod`

```bash
cd ~/suraksha
mkdir -p secrets
cp .env.prod.example .env.prod
echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
echo "PII_MASTER_KEY=$(openssl rand -hex 32)"
nano .env.prod
```

Edit to match the following. **Nine lines differ from the shipped example** —
they are the three gaps plus the sizing block:

```ini
# --- host sizing: UNCOMMENT for t3.small ---
MEM_DB=512m
MEM_REDIS=128m
MEM_MIGRATE=512m
MEM_API=640m
MEM_WORKER=640m
MEM_GATEWAY=256m
MEM_NGINX=64m

POSTGRES_USER=suraksha
POSTGRES_DB=suraksha
POSTGRES_PASSWORD=<the openssl hex-24 output>
DATABASE_URL=postgresql+psycopg://suraksha:<THAT SAME PASSWORD SPELLED OUT>@db:5432/suraksha
DATABASE_ECHO=false

REDIS_URL=redis://redis:6379/0

S3_BUCKET=<the $BUCKET value from Step 1>
AWS_REGION=ap-south-1
S3_ENDPOINT_URL=
S3_ACCESS_KEY=
S3_SECRET_KEY=

PII_MASTER_KEY=<the openssl hex-32 output>

AUTH_MODE=demo                          # CHANGED from firebase — see gap #1
# GOOGLE_APPLICATION_CREDENTIALS unused in demo mode; leave or delete the line

LLM_PROVIDER=gemini                     # CHANGED from watsonx — see gap #3
GOOGLE_API_KEY=<your key from the local .env>
GEMINI_DEFAULT_MODEL=gemini-3.1-flash-lite

RULESET_SOURCE=file                     # CHANGED from db — see gap #2
WRITE_PACK_FILES=false
RECORD_LLM_INVOCATIONS=true
DOC_GATE_MODE=enforce
LOR_GATE_MODE=enforce
LANGSMITH_TRACING=false
```

Three things that bite here:

- **`DATABASE_URL` does not interpolate `${POSTGRES_PASSWORD}`.** That file is
  read as a literal env-file. Spell the password out in both places.
- Leave the three `S3_*` credential lines **empty** — that's what makes boto3 and
  the JS SDK fall through to the instance role.
- The watsonx lines can stay empty; `LLM_PROVIDER=gemini` means they're unread.

Copy `PII_MASTER_KEY` somewhere off the box now. It envelope-encrypts every
blob; losing it makes all stored uploads permanently unreadable.

## Step 9 — Deploy

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

**First build is 8–15 minutes on `t3.small`** — the pip layer is nearly all of
it, and it's cached for every redeploy afterwards. Compose runs `alembic upgrade
head` as a one-shot `migrate` service and refuses to start `api`/`worker` unless
it exits 0, so a bad migration stops the deploy instead of half-applying.

## Step 10 — Verify

On the instance:

```bash
C="docker compose -f docker-compose.prod.yml --env-file .env.prod"
$C ps                          # 6 services up (migrate should show Exited 0)
curl -s localhost/healthz      # -> ok
curl -s localhost/             # -> gateway root
curl -s localhost/api/requirements/default | head -c 300
```

That last one is the real end-to-end read: **nginx:80 → gateway:3000 →
api:8000 → ruleset loader**. If it returns JSON, the whole proxy chain and the
FastAPI service are healthy.

Then from your laptop browser: `http://<PUBLIC_IP>/healthz`.

If `api` restart-loops, `$C logs api` — it's almost always a malformed
`DATABASE_URL`. Give it the full 90s healthcheck grace first; the langchain
import graph takes 20–40s to load.

Full write-path check (does an LLM call, so it proves `GOOGLE_API_KEY` works):
submit a claim from the app in Step 11 and watch `$C logs -f worker`.

## Step 11 — Point the Flutter app at it

[config.dart](mobile_app/lib/config.dart) reads `API_BASE` at compile time. nginx
is on port 80, so no port suffix:

```bash
flutter build apk --release --dart-define=API_BASE=http://<PUBLIC_IP>
```

The manifest already sets `usesCleartextTraffic="true"`, so plain HTTP works
unchanged. The public IP changes on every instance stop/start — for a one-day
run, just don't stop it.

## Step 12 — Teardown (the step that actually saves the credits)

Do this the moment you're done. A forgotten instance is ~$0.54/day and an
orphaned volume ~$0.06/day: individually invisible, billing indefinitely.

Save anything you want first — on the instance:

```bash
$C exec -T db pg_dump -U suraksha suraksha | gzip > /tmp/suraksha-final.sql.gz
aws s3 cp /tmp/suraksha-final.sql.gz "s3://$BUCKET/backups/"
```

Pull it down in CloudShell (`aws s3 cp ... ~/`, then *Actions → Download file*)
**before** emptying the bucket. Also save `.env.prod` off the box if you might
redeploy — a regenerated `PII_MASTER_KEY` orphans every existing blob.

Then run [DEPLOY.md §14](DEPLOY.md) "Delete everything" in order (instance →
S3 → IAM → security group; the SG won't delete until the instance is gone),
followed by its two confirmation blocks: the orphaned-resource check and the
**all-regions sweep**. The sweep matters — the console shows one region at a
time and a stray instance elsewhere is the classic way this goes wrong.

---

## Appendix — optional extras

**Earn the console-activity credits** while you're in there. Five activities are
worth $100 between them and you perform two of them in this runbook anyway
(*Launch an instance using EC2* §6, *Set up a cost budget* §2). Bedrock and
Lambda cost pennies. The RDS one bills hourly — create it, confirm the credit
landed, delete it the same day.

**If you'd rather demo the DB ruleset catalogue** instead of `RULESET_SOURCE=file`,
set `RULESET_SOURCE=db` in `.env.prod` and seed it after Step 9 (the version
string comes from `agentic_pipeline/rulesets/default.json`):

```bash
$C run --rm api python -m agentic_pipeline.rulesets_cli import-json agentic_pipeline/rulesets/default.json
$C run --rm api python -m agentic_pipeline.rulesets_cli activate default 2026-08-09
$C restart api worker      # clears the loader's in-process cache
```

**Optional repo edits** (not required to deploy; say the word and I'll make them):

- `DEPLOY.md` §9 — correct the `AUTH_MODE` guidance to match gap #1, and add the
  ruleset-seeding step for gap #2.
- `.env.prod.example` — flip the `AUTH_MODE` / `RULESET_SOURCE` / `LLM_PROVIDER`
  defaults so the file is deployable as-shipped.
- `.github/workflows/ci.yml` — the `alembic upgrade head` step runs with
  `working-directory: ./agentic_pipeline`, but `alembic.ini` is at the repo root,
  so that step isn't doing what it looks like. Already flagged in DEPLOY.md §13.5.

## Known limits of this deployment

- **No HTTPS.** Claim photos and PII cross in plaintext. Acceptable for a
  one-day demo with synthetic data; do not put real claimant data through it.
- **`AUTH_MODE=demo`** means the bearer token is the user id — anyone who knows
  a uid can read that user's claims. Another reason not to leave it running.
- `blobs.local_path_from_ref` stages S3 downloads into the worker's `/tmp` with
  no cleanup. Irrelevant over 24h on a 20 GB volume; `$C restart worker` clears it.
- One instance, one EBS volume, no replica or backup beyond the `pg_dump` above.
