# Security and production-readiness review

Record at least 8 concrete risks or improvements relevant to your final solution.
This is a review requirement, not the number of hidden faults.

---

## Finding 1 — Secrets: lab password committed in plaintext to `config/app.env`

- **Risk and evidence:** `config/app.env` contains `DATABASE_URL` with a plaintext password
  (`BarqLabOnly_7qN2vK8c`), committed to the git repository. Anyone with read access to the
  repo (or its history) has the database credential.
- **Impact:** If this were a real credential rather than a synthetic lab value, committing it
  would mean the secret is permanently in git history (removable only by history rewriting)
  and visible to every collaborator, CI log viewer, and — if the repo is ever made public —
  the internet.
- **Implemented fix / commit:** None beyond confirming this is a synthetic, lab-only value
  (per the task's explicit instruction: "Use synthetic lab data only; never submit real
  secrets"). `.env.example` exists in the repo as the documented safe pattern for real
  deployments (placeholder values, not committed real secrets).
- **Production follow-up:** Move all real secrets out of any committed file entirely. Use a
  secrets manager (Docker Swarm secrets, Kubernetes Secrets, AWS Secrets Manager/Parameter
  Store, HashiCorp Vault, etc.) or, at minimum, an uncommitted `.env` file populated at deploy
  time from CI/CD secret storage, with `config/app.env` (or equivalent) added to `.gitignore`.
- **How to verify:** `git log --all --full-history -- config/app.env` shows the file's full
  history; `grep -r "BarqLabOnly" .` confirms where the value currently appears.

---

## Finding 2 — Ports: PostgreSQL and Redis originally published to the host

- **Risk and evidence:** The original `docker-compose.yml` published
  `127.0.0.1:15432:5432` (postgres) and `127.0.0.1:16379:6379` (redis) — see
  `troubleshooting.md` Entry 10. Even bound to localhost, this widens the attack surface
  beyond what the task requires ("Do not publish app, PostgreSQL or Redis ports").
- **Impact:** Any process or user on the host machine (not just the Docker network) could
  connect directly to the database or cache, bypassing the application layer entirely —
  including any future malware or unrelated compromised process on the same host.
- **Implemented fix / commit:** Removed both `ports:` entries; commit "Complete
  docker-compose fixes: persistence, network isolation, restart policies, resource limits,
  verified image digests".
- **Production follow-up:** None needed for this specific issue in a correctly configured
  environment — the fix is complete and sufficient. In a multi-host production deployment,
  additionally restrict database access at the cloud-provider security-group/firewall level
  as defense in depth, in case a future compose change accidentally reintroduces a published
  port.
- **How to verify:** `docker port postgres` and `docker port redis` both return empty output
  (confirmed via `validate.py`, "Container 'postgres'/'redis' publishes no host ports" — PASS).

---

## Finding 3 — Container user: application ran as root

- **Risk and evidence:** The Dockerfile created a non-root `app` user but then set
  `USER root` immediately before `CMD`, silently reverting to root — see `troubleshooting.md`
  Entry 13.
- **Impact:** If the Flask application or one of its dependencies (e.g. a future vulnerable
  Python package) were exploited, the attacker's process inside the container would run as
  root, with full read/write access to everything inside the container's filesystem and a
  larger set of exploitable kernel capabilities than a non-root process would have.
- **Implemented fix / commit:** Removed `USER root`, added explicit `USER app` before `CMD`;
  commit "Fix Dockerfile to run as non-root user".
- **Production follow-up:** Add an automated CI check that fails the build if the final image
  runs as root (e.g. `docker run --rm <image> whoami` asserting `app`), so a future regression
  is caught automatically rather than requiring manual Dockerfile review.
- **How to verify:** `docker exec app-01 whoami` should return `app`.

---

## Finding 4 — Image selection: corrupted digest pin went undetected until manually verified

- **Risk and evidence:** The Python base image's pinned SHA256 digest was corrupted (one
  extra character), causing `docker pull` to fail outright — see `troubleshooting.md`
  Entry 14. This was caught only because the build had to succeed for the task to proceed;
  a less immediately-fatal supply-chain issue (e.g. a digest pointing to a *different but
  still valid* image) could go unnoticed indefinitely.
- **Impact:** Digest pinning is meant to guarantee exactly which image bytes are running; a
  corrupted or incorrect digest either breaks the build (this case, low risk — fails loudly)
  or, worse, could silently pin to an unintended image if the corruption happened to produce
  another valid digest (much higher risk — fails silently).
- **Implemented fix / commit:** Corrected digest verified via `docker pull` +
  `docker inspect --format='{{index .RepoDigests 0}}'` against the official image; commit
  "Fix corrupted python image digest ... re-pin postgres/redis/nginx digests after
  verification".
- **Production follow-up:** Add image signature verification (e.g. Docker Content Trust,
  Sigstore/cosign) so image provenance is cryptographically verified at pull time, not just
  digest-matched against what a human manually confirmed once.
- **How to verify:** `docker pull python:3.12-slim-bookworm@sha256:<digest-in-Dockerfile>`
  succeeds without error.

---

## Finding 5 — Networks: NGINX originally had a direct path to PostgreSQL/Redis

- **Risk and evidence:** The original compose file placed `nginx` on both the `frontend` and
  `backend` networks — see `troubleshooting.md` Entry 11.
- **Impact:** A compromised or misconfigured NGINX (e.g. a malicious `location` block added
  in a future change, or an NGINX-level vulnerability) would have had direct network-layer
  access to PostgreSQL and Redis, bypassing the application's authentication/authorization
  logic entirely — network segmentation is a defense-in-depth layer independent of the app.
- **Implemented fix / commit:** Restricted `nginx` to the `frontend` network only; `backend`
  network marked `internal: true` (no route to the host or outside world); commit "Complete
  docker-compose fixes: persistence, network isolation, restart policies, resource limits,
  verified image digests".
- **Production follow-up:** Split `backend` further into per-dependency networks (see
  `decisions.md` Decision 3) for tighter segmentation between Postgres and Redis themselves.
- **How to verify:** `validate.py`'s network-isolation check — `docker exec nginx` attempting
  to reach `postgres:5432` and `redis:6379` both fail (confirmed PASS, exit code 1 on both).

---

## Finding 6 — Persistence/backup: PostgreSQL data directory was on `tmpfs` (non-persistent)

- **Risk and evidence:** The original config mounted the named volume to an unused path
  (`/var/lib/postgresql/backup`) while the actual PostgreSQL data directory
  (`/var/lib/postgresql/data`) was `tmpfs` — in-memory, wiped on every container removal.
  See `troubleshooting.md` Entry 9.
- **Impact:** Any data written to the database would be permanently lost the moment the
  container was recreated (e.g. during a routine deployment, a crash-and-restart, or a host
  reboot) — a silent, total data-loss bug that would only be discovered the first time it
  actually happened in a real incident.
- **Implemented fix / commit:** Corrected the volume mount to the real data directory and
  removed the `tmpfs` entry; verified with a live test (create record → `docker compose down`
  → `docker compose up -d --build` → record still present). Commit "Complete docker-compose
  fixes: persistence, network isolation, restart policies, resource limits, verified image
  digests".
- **Production follow-up:** The named volume still lives on a single host and doesn't survive
  host-level disk failure. Add off-host backup replication (e.g. scheduled `pg_dump` to
  external object storage, or a managed database service with built-in point-in-time
  recovery) rather than relying solely on the local Docker volume. `backup.sh`/`restore.sh`
  as implemented are manual/on-demand — a production system needs this automated and
  scheduled, with backup integrity periodically test-restored.
- **How to verify:** See `troubleshooting.md` Entry 9's full persistence test transcript, and
  `backup.sh`/`restore.sh` execution logs.

---

## Finding 7 — Logging/monitoring: no centralized log aggregation or alerting

- **Risk and evidence:** All three logs (`access.log`, `error.log`, `application.log`) exist
  only as local files inside their respective containers (or as historical files supplied for
  this exercise). There is no log shipping, centralized aggregation, or alerting configured.
- **Impact:** In a real incident (e.g. the Redis/Postgres outages analyzed in
  `log_analysis.md`), an operator would have no automated notification and would need to
  manually inspect container logs after the fact — exactly as this analysis had to do
  retrospectively. A production incident could persist far longer before being noticed.
- **Implemented fix / commit:** None — this is out of scope for the current environment and
  is flagged here as a gap rather than something addressed.
- **Production follow-up:** Ship logs to a centralized system (e.g. Loki, ELK/OpenSearch, or
  a managed logging service) and configure alerts on `dependency_error` events in
  `application.log` and 5xx-rate spikes in `access.log`, so a Redis/Postgres outage like the
  ones in `log_analysis.md` triggers a page within seconds instead of being discovered via
  customer complaints or a later manual log review.
- **How to verify:** N/A (not implemented) — a completed version would be verified by
  confirming an alert fires within a defined SLA after a simulated dependency failure.

---

## Finding 8 — Availability: PostgreSQL and Redis are single points of failure

- **Risk and evidence:** `docker-compose.yml` runs exactly one `postgres` container and one
  `redis` container. `log_analysis.md` Incidents 2 and 3 show that when either dependency has
  a problem, **both** `app-01` and `app-02` are affected simultaneously — the app-tier
  redundancy (two instances behind NGINX) does not protect against a shared-dependency
  failure, since both instances depend on the same single Postgres/Redis.
- **Impact:** A single Postgres or Redis failure (crash, resource exhaustion, network
  partition) causes a full application-wide outage for any endpoint touching that dependency,
  despite having "two backends" — the redundancy that matters most for these incidents
  doesn't exist.
- **Implemented fix / commit:** None — this is a known, accepted limitation of the current
  single-node lab environment, explicitly called out in `log_analysis.md`'s Conclusions
  section (Q10) rather than left undocumented.
- **Production follow-up:** Run PostgreSQL with a standby replica and automated failover
  (e.g. Patroni, or a managed service like RDS Multi-AZ / Cloud SQL HA); run Redis in a
  Sentinel or Cluster configuration with at least one replica. Both changes require
  corresponding application-level changes (e.g. connection retry/failover logic) beyond what
  `app/server.py` currently implements.
- **How to verify:** N/A (not implemented) — a completed version would be verified by killing
  the primary Postgres/Redis node during a load test and confirming requests continue to
  succeed via automatic failover, analogous to `failure_test.py`'s existing app-tier test.

---

## Finding 9 — Unrelated project files (`.sf/` Salesforce metadata) accidentally tracked in early git history

- **Risk and evidence:** An early local git setup mistake (documented in this project's
  working history) resulted in an unrelated `.sf/orgs/.../catalog.json` file from a different,
  unrelated project being staged for commit at one point during setup.
- **Impact:** Low direct security impact in this case (the file contains no secrets relevant
  to BARQ), but accidentally committing files from unrelated projects into a shared repo is a
  general hygiene risk — it can leak unrelated proprietary code/config, bloats repo size, and
  signals insufficiently careful `git add` habits that could just as easily catch a real
  secret next time.
- **Implemented fix / commit:** Avoided adding `.sf/` to any BARQ commit; the working
  directory was migrated to a clean project folder (`BARQ-DevOps-Internship`) specifically to
  avoid carrying this contamination forward.
- **Production follow-up:** Add a `.gitignore` entry for `.sf/` and any other
  editor/tool-specific directories unrelated to the project, and use `git status`/`git diff
  --staged` as a habitual pre-commit check before every `git commit`, especially in shared or
  reused local working directories.
- **How to verify:** `git log --all -- .sf/` should show no BARQ-repo commits touching that
  path; confirmed by inspection of this repository's commit history.

---

## Finding 10 — CI security scan is informational only, not a merge gate

- **Risk and evidence:** `.github/workflows/ci.yml`'s Trivy image scan step runs with
  `continue-on-error: true` and `exit-code: "0"` — vulnerability findings are logged but never
  fail the pipeline (see `decisions.md` Decision 4 for the reasoning).
- **Impact:** A newly introduced CRITICAL vulnerability in a base image could be merged and
  deployed without anyone noticing, since the CI status stays green regardless of scan
  results.
- **Implemented fix / commit:** Deliberately left as informational, since this was marked
  optional/extra-credit in the task and the base images will realistically always have some
  outstanding CVEs that are out of scope to remediate within this assessment.
- **Production follow-up:** Make the scan a hard gate for CRITICAL-severity findings
  (`exit-code: "1"`, remove `continue-on-error`), with a documented exception/waiver process
  for cases where a finding is a false positive or genuinely unexploitable in this
  application's context.
- **How to verify:** Inspect the "Scan images with Trivy" step output in any CI run; changing
  `exit-code` to `"1"` and re-running against a deliberately outdated base image would confirm
  the gate behavior once implemented.

---

## Summary: completed vs. planned

**Completed and verified in this submission:** Findings 1 (secrets — confirmed lab-only),
2 (ports), 3 (container user), 4 (image digest integrity), 5 (network isolation),
6 (persistence), 9 (repo hygiene).

**Explicitly out of scope / planned for production only:** Findings 7 (centralized
logging/alerting), 8 (database/cache high availability), 10 (CI scan as hard gate) — these
require infrastructure (external log aggregation, multi-node database clusters, CI policy
changes) beyond what a single-host Docker Compose lab environment can reasonably provide, and
are documented here as explicit gaps rather than silently omitted.