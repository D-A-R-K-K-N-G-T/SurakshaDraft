# Deploying SurakshaDraft to AWS (free tier)

One `t3.micro` EC2 instance running the whole stack under Docker Compose, with
real S3 for blobs. Postgres and Redis run as containers on the same box rather
than RDS/ElastiCache — not because that is better, but because managed versions
of them would multiply the bill for a deployment this size.

Expect **45–60 minutes** end to end, most of it waiting on the first image build.

---

## 1. What this costs

This account is on AWS's **credit-based free plan** (the model for accounts
opened from mid-2025 onward): a starting credit balance, more earnable by
completing console activities, and a time limit on the plan itself. There is no
separate "750 free hours" allowance to stay under — usage draws down credits
instead, so the goal is a low burn rate rather than a magic zero.

Approximate on-demand rates in `ap-south-1`. **Verify current figures in the
AWS Pricing Calculator** — these move, and they differ per region.

| Resource | Rate | Running 24×7 |
|---|---|---|
| EC2 `t3.micro` | ~$0.0112 /h | ~$8.20 /mo |
| Public IPv4 address | ~$0.005 /h | ~$3.65 /mo |
| EBS gp3 root volume, 20 GB | ~$0.0912 /GB-mo | ~$1.80 /mo |
| S3 Standard, a few GB | ~$0.025 /GB-mo | ~$0.15 /mo |
| Data transfer out | first 100 GB/mo free | $0 |
| **RDS / ElastiCache / ALB / NAT** | — | **not used — these are the expensive part** |

**≈ $13–14/month running continuously.** The plan's window, not the credit
balance, is usually what runs out first.

### If this is a short-lived deployment (demo, hackathon, ~a day)

Then the arithmetic inverts and most cost advice stops applying. **24 hours of
the whole stack is roughly $0.50.** At that scale:

- **Do not optimise for cost — optimise for your time.** Size *up*. On
  `t3.small` (2 GiB) the extra compute is about $0.30 for the day and it removes
  the entire class of problems the 1 GiB defaults exist to work around: no swap
  thrashing, a materially faster image build, no memory ceiling anywhere near
  what the containers actually want. `t3.medium` (4 GiB) is ~$1 for the day if
  you would rather not think about memory at all. See §6, and raise the
  `MEM_*` ceilings in `.env.prod` to match.
- **Earn the activity credits anyway.** Five console activities are worth $100
  between them, against a spend of well under a dollar. Two of them you perform
  in this runbook regardless — *Launch an instance using EC2* (§6) and *Set up a
  cost budget* (§7). Bedrock and Lambda cost pennies. The RDS one bills by the
  hour, so create it, confirm the credit landed, and delete it the same day.
- **The only real risk is forgetting to delete something.** A running instance
  is ~$0.29/day and an orphaned EBS volume ~$0.06/day — individually invisible,
  but they bill silently for months and there is nothing to remind you. Work
  through **§14** when you are done, and keep the §7 budget alarm afterward as
  the backstop for anything you missed.

### If you are keeping it running

In rough order of impact:

1. **Stop the instance when you are not using it.** A stopped instance is
   charged for nothing but its EBS volume (~$1.80/mo) — no compute, and the
   auto-assigned public IP is released so that charge stops too. Demoing a few
   hours a day puts this near **$3–5/month**. See §12 for the commands. The IP
   changes on each start; §11 covers that.
2. **Earn the activity credits**, as above.
3. **Don't oversize the volume.** §6 asks for 20 GB, which fits 4 GB of swap,
   ~4 GB of images and build cache, the OS, and room for Postgres to grow.
   Every extra 10 GB is ~$0.90/month for nothing.
4. **Terminate rather than stop** once you are finished with the project.
   Stopping still bills the volume, indefinitely.

### Things that will silently bill you

- An Elastic IP **not attached to a running instance** is charged hourly. That
  includes the whole time your instance is stopped. This guide uses the
  auto-assigned public IP and no EIP for exactly this reason.
- EBS snapshots and AMIs you create and forget.
- The EBS volume left behind after terminating an instance, if you did not set
  `DeleteOnTermination` (the §6 command does).
- Anything launched in a **different region** than the one you are looking at —
  the console only shows you one region at a time.

Set the budget alarm in **§7** before anything else. Do not skip it.

---

## 2. Prerequisites

You do **not** need the AWS CLI or Docker on your laptop. Every command below
runs either in **AWS CloudShell** (browser terminal, preinstalled CLI, free) or
on the instance itself.

Open CloudShell with the `>_` icon in the AWS console top bar. Pick your region
first — everything must live in one region. This guide uses `ap-south-1`
(Mumbai); substitute freely, but be consistent.

```bash
export AWS_REGION=ap-south-1
export BUCKET=suraksha-blobs-$(aws sts get-caller-identity --query Account --output text)
echo "$AWS_REGION / $BUCKET"
```

---

## 3. Create the S3 bucket

Blob storage for claim photos. The bucket stays fully private — the gateway
reads and writes it through the instance role, never via a public URL.

```bash
aws s3api create-bucket \
  --bucket "$BUCKET" \
  --region "$AWS_REGION" \
  --create-bucket-configuration LocationConstraint="$AWS_REGION"

# Belt and braces: block every form of public access.
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# Encrypt at rest with the free S3-managed key. The app also envelope-encrypts
# PII blobs before upload (PII_MASTER_KEY) — this is the second layer.
aws s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
```

Keep an eye on the 5 GB free allowance. `scripts/retention_gc.py` and the
`gc-expired-claims` beat task delete blobs past their retention window, which is
what stops this growing without bound.

---

## 4. IAM role for the instance

The instance gets a role instead of access keys, so no long-lived credentials
are ever written to disk. This is why `S3_ACCESS_KEY`/`S3_SECRET_KEY` are left
empty in `.env.prod` — both SDKs fall through to the role automatically.

```bash
cat > trust.json <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
 "Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}
JSON

aws iam create-role --role-name suraksha-ec2 \
  --assume-role-policy-document file://trust.json

# Scoped to this one bucket, and only the four calls the code actually makes
# (see blobs.py: put_object / head_object / download_file, and the GC delete).
cat > s3policy.json <<JSON
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],
  "Resource":"arn:aws:s3:::${BUCKET}/*"},
 {"Effect":"Allow","Action":["s3:ListBucket"],
  "Resource":"arn:aws:s3:::${BUCKET}"}]}
JSON

aws iam put-role-policy --role-name suraksha-ec2 \
  --policy-name suraksha-s3 --policy-document file://s3policy.json

# Lets you open a shell via Session Manager — no SSH key, no port 22 open.
aws iam attach-role-policy --role-name suraksha-ec2 \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore

aws iam create-instance-profile --instance-profile-name suraksha-ec2
aws iam add-role-to-instance-profile \
  --instance-profile-name suraksha-ec2 --role-name suraksha-ec2
```

---

## 5. Security group

Only port 80 is open. Postgres, Redis, and the FastAPI service are reachable
solely on the Docker network — none of them publishes a host port, which is
deliberate: an exposed Postgres on a public IP is found by scanners in minutes.

```bash
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
      --query 'Vpcs[0].VpcId' --output text)

SG=$(aws ec2 create-security-group --group-name suraksha-web \
     --description "SurakshaDraft public edge" --vpc-id "$VPC" \
     --query GroupId --output text)

aws ec2 authorize-security-group-ingress --group-id "$SG" \
  --protocol tcp --port 80 --cidr 0.0.0.0/0

echo "SG=$SG"
```

No inbound SSH rule. Shell access comes from Session Manager in §6.

---

## 6. Launch the instance

Pick the instance type before you run this — it is the one decision here that is
annoying to change later (it needs a stop, a modify, and a start):

| Type | RAM | ~$/day | Use when |
|---|---|---|---|
| `t3.micro` | 1 GiB | $0.29 | Running for weeks; conserving credits matters |
| `t3.small` | 2 GiB | $0.54 | **Short-lived demo — recommended.** No swap pressure, faster build |
| `t3.medium` | 4 GiB | $1.08 | You want zero memory tuning at all |

On `t3.small` or larger, also uncomment the `MEM_*` block in `.env.prod` (§9) so
the containers get ceilings that match the host.

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

`HttpTokens=required` forces IMDSv2, which closes the SSRF-to-credential-theft
path — worth having on a box whose whole job is fetching user-supplied content.

Wait ~60 seconds, then grab the address and open a shell:

```bash
IID=$(aws ec2 describe-instances --filters Name=tag:Name,Values=suraksha \
      Name=instance-state-name,Values=running \
      --query 'Reservations[0].Instances[0].InstanceId' --output text)

aws ec2 describe-instances --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text

aws ssm start-session --target "$IID"
```

If `start-session` errors, the SSM agent has not registered yet — wait a minute
and retry. Then, inside the session:

```bash
sudo su - ec2-user
```

---

## 7. Budget alarm (do this first, not last)

Two free budgets are included, and this is the only thing standing between a
misconfiguration and a drained credit balance. It also completes the *Set up a
cost budget using AWS Budgets* console activity, so it pays for itself.

**Billing and Cost Management → Budgets → Create budget → Customize → Cost
budget**:

- Period **Monthly**, budget amount **$15** (roughly the 24×7 figure from §1 —
  crossing it means something unexpected is running).
- Under the cost-scope options, **include credits** rather than netting them
  out. A budget that subtracts credits reads $0 right up until the credits are
  gone, which is exactly when the warning is useless. You want gross usage.
- Alert thresholds at **50%, 80% and 100% of actual**, to your email.

Then bookmark **Billing → Free tier**, which shows the remaining credit balance
and the plan's expiry date. Check it weekly; that page, not the invoice, is what
tells you how much runway is left.

---

## 8. Prepare the machine

On the instance:

```bash
sudo dnf install -y git
git clone <YOUR_REPO_URL> suraksha
cd suraksha

sudo bash scripts/ec2_bootstrap.sh
```

That script adds 4 GB of swap, installs Docker + the Compose v2 plugin, and caps
container log sizes. **The swap is not optional** — installing the
langchain/langgraph dependency tree peaks over 1 GiB and the build is otherwise
killed by the OOM reaper with a bare `Killed`.

Log out and back in (`exit`, then `sudo su - ec2-user`) so the `docker` group
membership applies. Verify:

```bash
docker ps && free -h    # expect an empty container list and 4.0Gi of swap
```

---

## 9. Configure

```bash
cd ~/suraksha
mkdir -p secrets
cp .env.prod.example .env.prod

# Generate the two secrets. Copy the output into .env.prod.
echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
echo "PII_MASTER_KEY=$(openssl rand -hex 32)"

nano .env.prod
```

Read the comments in the file; the four that must be right:

- `POSTGRES_PASSWORD` **and** the same password spelled out inside
  `DATABASE_URL`. That file does not interpolate `${...}` into itself.
- `S3_BUCKET` — the bucket from §3. Leave `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`
  and `S3_SECRET_KEY` **empty** so the instance role is used.
- `PII_MASTER_KEY` — **back this up off the instance before going live.** Lose
  it and every stored blob is permanently undecryptable.
- `AUTH_MODE`. Leaving it `firebase` requires a service-account JSON at
  `~/suraksha/secrets/firebase.json` (compose mounts `./secrets` read-only at
  `/app/secrets`). If you are not wiring Firebase yet, set `AUTH_MODE=disabled`;
  claims then run anonymously. Never use `demo` on a public box — there the
  bearer token *is* the user id, so anyone can impersonate anyone.

---

## 10. Deploy

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

First build takes **10–20 minutes** on a burstable vCPU; the pip layer is the
slow part and is cached for every subsequent deploy. Compose runs `alembic
upgrade head` as a one-shot `migrate` service and refuses to start `api` or
`worker` unless it exits cleanly, so a broken migration stops the deploy rather
than corrupting a running system.

Check it:

```bash
docker compose -f docker-compose.prod.yml ps
curl -s localhost/healthz
curl -s localhost/            # gateway root
```

Then from your laptop's browser: `http://<PUBLIC_IP>/healthz`.

### Redeploying after a code change

```bash
cd ~/suraksha && git pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

---

## 11. Point the mobile app at it

`mobile_app/lib/config.dart` reads `API_BASE` at compile time and defaults to
the Android emulator alias. Rebuild against the instance — port 80, so no port
suffix:

```bash
flutter build apk --release --dart-define=API_BASE=http://<PUBLIC_IP>
```

The manifest already sets `usesCleartextTraffic="true"`, so plain HTTP works
without further changes. It is fine for a demo and **not** fine once real claim
photos and PII are flowing — see §13.

The public IP changes every time you stop and start the instance. Attach an
Elastic IP if you need it stable, and re-read the warning in §1 about EIPs
billing while detached.

---

## 12. Operating it

```bash
cd ~/suraksha
C="docker compose -f docker-compose.prod.yml --env-file .env.prod"

$C logs -f api          # or: worker, gateway, nginx, db
$C ps
$C restart worker
$C down                 # stop everything (volumes survive)

docker stats --no-stream   # per-container memory against the mem_limits
free -h                    # if swap used is large and growing, see below
```

**Stopping between demos** is the main cost lever (§1). From CloudShell:

```bash
IID=$(aws ec2 describe-instances --filters Name=tag:Name,Values=suraksha \
      Name=instance-state-name,Values=running,stopped \
      --query 'Reservations[0].Instances[0].InstanceId' --output text)

aws ec2 stop-instances  --instance-ids "$IID"
aws ec2 start-instances --instance-ids "$IID"

# The public IP is reassigned on every start — fetch it and rebuild the APK (§11).
aws ec2 describe-instances --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text
```

Compose services are all `restart: unless-stopped` and Docker starts on boot, so
the stack comes back on its own after a start; give it a couple of minutes and
the `api` container its 90-second healthcheck grace period. Data survives in the
named volumes. When the project is finished, **terminate** rather than stop —
a stopped instance still bills for its EBS volume indefinitely.

**Database backup.** Nothing backs this up for you; the data lives on one EBS
volume. At minimum, before any risky change:

```bash
$C exec -T db pg_dump -U suraksha suraksha | gzip > ~/suraksha-$(date +%F).sql.gz
aws s3 cp ~/suraksha-$(date +%F).sql.gz "s3://$BUCKET/backups/"
```

**Ad-hoc pipeline commands** run in a throwaway container with the full env:

```bash
$C run --rm api python -m scripts.retention_gc
$C run --rm api alembic current
```

### When it misbehaves

| Symptom | Cause | Fix |
|---|---|---|
| Build dies with `Killed` | Out of memory | Swap missing — re-run `ec2_bootstrap.sh`, check `free -h` |
| `api` restarts in a loop | Slow langchain import tripping the healthcheck | Already given a 90s `start_period`; if still failing, read `$C logs api` — usually a bad `DATABASE_URL` |
| `migrate` exits non-zero | Migration failure | `$C logs migrate`. `api`/`worker` correctly refuse to start |
| 413 on upload | Payload over nginx's cap | Raise `client_max_body_size` in `nginx.prod.conf` |
| 504 on claim submit | LLM call slower than the proxy timeout | Raise `proxy_read_timeout` in `nginx.prod.conf` |
| Everything sluggish, swap climbing | 1 GiB genuinely exceeded | Set `AUTH_MODE=disabled` to drop `firebase-admin`, or move Postgres to RDS (§13) |

---

## 13. Known gaps

Deliberately out of scope above, in the order I would fix them:

1. **No HTTPS.** Traffic — including claim photos and PII — crosses the network
   in plaintext. Do not run real claimant data through this. Fix: point a domain
   at the instance and add Caddy or certbot in front of nginx for a free Let's
   Encrypt certificate. An ALB with ACM is the tidier answer but is not free.
2. **Single point of failure.** One instance, one EBS volume, no replica. An AZ
   failure or a corrupted volume loses the database. The `pg_dump` in §12 is the
   only recovery path until this changes.
3. **`PII_MASTER_KEY` sits in a file on the box.** AWS Secrets Manager or SSM
   Parameter Store (the latter has a free tier) is the right home for it.
4. **Postgres shares 1 GiB with everything else.** The cleanest relief valve is
   RDS `db.t4g.micro`, itself free-tier eligible for 12 months: create it, point
   `DATABASE_URL` at its endpoint, and delete the `db` service and its
   `depends_on` entries from `docker-compose.prod.yml`. That frees ~250 MB.
5. **CI does not deploy.** `.github/workflows/ci.yml` only tests. Also worth
   noting: its `alembic upgrade head` step runs with `working-directory:
   ./agentic_pipeline`, but `alembic.ini` lives at the repo root — that step is
   not doing what it appears to.

---

## 14. Teardown

For a short-lived deployment this is the section that actually protects your
credits. A forgotten instance is ~$0.29/day and an orphaned volume ~$0.06/day:
too small to notice on any single day, and they bill indefinitely.

Run it all in CloudShell, in order — the security group cannot be deleted until
the instance using it is gone.

**First, keep anything you want.** Everything below is irreversible.

```bash
# On the INSTANCE: dump the database and park it in S3.
cd ~/suraksha
docker compose -f docker-compose.prod.yml --env-file .env.prod \
  exec -T db pg_dump -U suraksha suraksha | gzip > /tmp/suraksha-final.sql.gz
aws s3 cp /tmp/suraksha-final.sql.gz "s3://$BUCKET/backups/"
```

```bash
# In CLOUDSHELL: pull it down, then use Actions -> Download file to save it
# locally. Do this BEFORE deleting the bucket.
aws s3 cp "s3://$BUCKET/backups/suraksha-final.sql.gz" ~/
```

Also save `.env.prod` off the box if you might redeploy — regenerating
`PII_MASTER_KEY` would orphan every blob encrypted under the old one.

### Delete everything

```bash
export AWS_REGION=ap-south-1
export BUCKET=<the bucket name from §3>

# 1. Instance. The root volume goes with it (DeleteOnTermination was set in §6).
IID=$(aws ec2 describe-instances --filters Name=tag:Name,Values=suraksha \
      "Name=instance-state-name,Values=running,stopped,stopping" \
      --query 'Reservations[*].Instances[*].InstanceId' --output text)
aws ec2 terminate-instances --instance-ids $IID
aws ec2 wait instance-terminated --instance-ids $IID

# 2. S3. The bucket must be emptied before it can be deleted.
aws s3 rm "s3://$BUCKET" --recursive
aws s3api delete-bucket --bucket "$BUCKET" --region "$AWS_REGION"

# 3. IAM. Free, but leaving a role with S3 write access lying around is untidy.
#    The profile must release the role before either can be deleted.
aws iam remove-role-from-instance-profile \
  --instance-profile-name suraksha-ec2 --role-name suraksha-ec2
aws iam delete-instance-profile --instance-profile-name suraksha-ec2
aws iam delete-role-policy --role-name suraksha-ec2 --policy-name suraksha-s3
aws iam detach-role-policy --role-name suraksha-ec2 \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam delete-role --role-name suraksha-ec2

# 4. Security group. Only deletable once nothing references it.
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=suraksha-web \
     --query 'SecurityGroups[0].GroupId' --output text)
aws ec2 delete-security-group --group-id "$SG"
```

### Confirm nothing is left billing

```bash
# Anything printed here still costs money.
aws ec2 describe-volumes --filters Name=status,Values=available \
  --query 'Volumes[].[VolumeId,Size]' --output text          # orphaned volumes
aws ec2 describe-addresses --query 'Addresses[].PublicIp' --output text
aws ec2 describe-snapshots --owner-ids self --query 'Snapshots[].SnapshotId' --output text
aws rds describe-db-instances \
  --query 'DBInstances[].DBInstanceIdentifier' --output text  # if you did the RDS activity
```

Then sweep **every** region, because the console only ever shows you one and a
stray instance elsewhere is the classic way this goes wrong:

```bash
for r in $(aws ec2 describe-regions --query 'Regions[].RegionName' --output text); do
  n=$(aws ec2 describe-instances --region "$r" \
      --filters Name=instance-state-name,Values=running,stopped \
      --query 'length(Reservations[].Instances[])' --output text)
  [ "$n" != "0" ] && echo "$r: $n instance(s)"
done
echo "sweep complete"
```

**Keep the §7 budget.** It costs nothing and is your only safety net for
anything this checklist missed.
