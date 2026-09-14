
````
# BARQ DevOps Lab

## Overview
Comprehensive operational and technical guide for the **BARQ DevOps Lab** system, which features a Flask application (distributed across two instances, `app-01` and `app-02`), a PostgreSQL database, a Redis caching service, and an NGINX reverse proxy configured with advanced security standards, network isolation, and data persistence.
video link : https://drive.google.com/drive/folders/1BBgj4G-S5o7YZ3s-8iOkOsr4FyG0wY76?usp=drive_link

---

## Prerequisites
* **Docker Engine** (version 24.0 or later)
* **Docker Compose** (version v2 or later)
* **Python 3.12+** (for running validation and test scripts)

---

## Build and Run

1. **Configure Environment Variables:**
   Ensure `config/app.env` is populated (you can use `.env.example` as a secure template):
   ```bash
   cp config/app.env.example config/app.env

````

2. **Build and Start Containers:**

   Run the following command to build the container images and start all services in the background:




   Bash
   ```
   docker compose up -d --build

   ```
3. **Verify Service Health:**




   Bash
   ```
   docker compose ps

   ```

## Testing and Validation

- **Run Comprehensive Validation:**




  Bash
  ```
  python validate.py

  ```
  *(Verifies network isolation, non-published database/cache ports on the host, and general container health).*



- **Run Failover Testing:**




  Bash
  ```
  python failure_test.py

  ```

## Backup and Restore

- **Create Database Backup:**




  Bash
  ```
  ./backup.sh

  ```
- **Restore Database from Backup:**




  Bash
  ```
  ./restore.sh <backup_file_path>

  ```

## Architecture and Request Flow

The technical flow of the system illustrates how client requests are handled across isolated network layers:

```
[ Client ]
       │
       ▼ (via allowed public port)
[ NGINX (Frontend Network) ] ── (Strict isolation: no direct network path) ── ✖ [ PostgreSQL / Redis ]
       │
       ├──► [ App-01 (Backend Network - internal: true) ] ──┐
       │                                                   ├──► [ PostgreSQL (Persistent Volume) ]
       └──► [ App-02 (Backend Network - internal: true) ] ──┘    [ Redis (AOF Persistence) ]

```

- **Frontend Network (****`frontend`****):** Houses the NGINX reverse proxy and Flask application containers, handling incoming client traffic.



- **Backend Network (****`backend`** **- marked** **`internal: true`****):** Houses the application instances, PostgreSQL, and Redis. It has no direct route to the outside world or NGINX, preventing any direct proxy-to-database access as a defense-in-depth measure.

```

---

# Security and Production-Readiness Review (`security_review.md`)

## Finding 1 — Secrets: lab password committed in plaintext to `config/app.env`

- **Risk and evidence:** `config/app.env` contains `DATABASE_URL` with a plaintext password (`BarqLabOnly_7qN2vK8c`), committed to the git repository. Anyone with read access to the repo (or its history) has the database credential.
- **Impact:** If this were a real credential rather than a synthetic lab value, committing it would mean the secret is permanently in git history (removable only by history rewriting) and visible to every collaborator, CI log viewer, and — if the repo is ever made public — the internet.
- **Implemented fix / commit:** None beyond confirming this is a synthetic, lab-only value (per the task's explicit instruction: "Use synthetic lab data only; never submit real secrets"). `.env.example` exists in the repo as the documented safe pattern for real deployments (placeholder values, not committed real secrets).
- **Production follow-up:** Move all real secrets out of any committed file entirely. Use a secrets manager (Docker Swarm secrets, Kubernetes Secrets, AWS Secrets Manager/Parameter Store, HashiCorp Vault, etc.) or, at minimum, an uncommitted `.env` file populated at deploy time from CI/CD secret storage, with `config/app.env` (or equivalent) added to `.gitignore`.
- **How to verify:** `git log --all --full-history -- config/app.env` shows the file's full history; `grep -r "BarqLabOnly" .` confirms where the value currently appears.

---

## Finding 2 — Ports: PostgreSQL and Redis originally published to the host

- **Risk and evidence:** The original `docker-compose.yml` published `127.0.0.1:15432:5432` (postgres) and `127.0.0.1:16379:6379` (redis) — see `troubleshooting.md` Entry 10. Even bound to localhost, this widens the attack surface beyond what the task requires ("Do not publish app, PostgreSQL or Redis ports").
- **Impact:** Any process or user on the host machine (not just the Docker network) could connect directly to the database or cache, bypassing the application layer entirely — including any future malware or unrelated compromised process on the same host.
- **Implemented fix / commit:** Removed both `ports:` entries; commit "Complete docker-compose fixes: persistence, network isolation, restart policies, resource limits, verified image digests".
- **Production follow-up:** None needed for this specific issue in a correctly configured environment — the fix is complete and sufficient. In a multi-host production deployment, additionally restrict database access at the cloud-provider security-group/firewall level as defense in depth, in case a future compose change accidentally reintroduces a published port.
- **How to verify:** `docker port postgres` and `docker port redis` both return empty output (confirmed via `validate.py`, "Container 'postgres'/'redis' publishes no host ports" — PASS).

---

## Finding 3 — Container user: application ran as root

- **Risk and evidence:** The Dockerfile created a non-root `app` user but then set `USER root` immediately before `CMD`, silently reverting to root — see `troubleshooting.md` Entry 13.
- **Impact:** If the Flask application or one of its dependencies (e.g. a future vulnerable Python package) were exploited, the attacker's process inside the container would run as root, with full read/write access to everything inside the container's filesystem and a larger set of exploitable kernel capabilities than a non-root process would have.
- **Implemented fix / commit:** Removed `USER root`, added explicit `USER app` before `CMD`; commit "Fix Dockerfile to run as non-root user".
- **Production follow-up:** Add an automated CI check that fails the build if the final image runs as root (e.g. `docker run --rm <image> whoami` asserting `app`), so a future regression is caught automatically rather than requiring manual Dockerfile review.
- **How to verify:** `docker exec app-01 whoami` should return `app`.

---

## Finding 4 — Image selection: corrupted digest pin went undetected until manually verified

- **Risk and evidence:** The Python base image's pinned SHA256 digest was corrupted (one extra character), causing `docker pull` to fail outright — see `troubleshooting.md` Entry 14. This was caught only because the build had to succeed for the task to proceed; a less immediately-fatal supply-chain issue (e.g. a digest pointing to a *different but still valid* image) could go unnoticed indefinitely.
- **Impact:** Digest pinning is meant to guarantee exactly which image bytes are running; a corrupted or incorrect digest either breaks the build (this case, low risk — fails loudly) or, worse, could silently pin to an unintended image if the corruption happened to produce another valid digest (much higher risk — fails silently).
- **Implemented fix / commit:** Corrected digest verified via `docker pull` + `docker inspect --format='{{index .RepoDigests 0}}'` against the official image; commit "Fix corrupted python image digest ... re-pin postgres/redis/nginx digests after verification".
- **Production follow-up:** Add image signature verification (e.g. Docker Content Trust, Sigstore/cosign) so image provenance is cryptographically verified at pull time, not just digest-matched against what a human manually confirmed once.
- **How to verify:** `docker pull python:3.12-slim-bookworm@sha256:<digest-in-Dockerfile>` succeeds without error.

---

## Finding 5 — Networks: NGINX originally had a direct path to PostgreSQL/Redis

- **Risk and evidence:** The original compose file placed `nginx` on both the `frontend` and `backend` networks — see `troubleshooting.md` Entry 11.
- **Impact:** A compromised or misconfigured NGINX (e.g. a malicious `location` block added in a future change, or an NGINX-level vulnerability) would have had direct network-layer access to PostgreSQL and Redis, bypassing the application's authentication/authorization logic entirely — network segmentation is a defense-in-depth layer independent of the app.
- **Implemented fix / commit:** Restricted `nginx` to the `frontend` network only; `backend` network marked `internal: true` (no route to the host or outside world); commit "Complete docker-compose fixes: persistence, network isolation, restart policies, resource limits, verified image digests".
- **Production follow-up:** Split `backend` further into per-dependency networks (see `decisions.md` Decision 3) for tighter segmentation between Postgres and Redis themselves.
- **How to verify:** `validate.py`'s network-isolation check — `docker exec nginx` attempting to reach `postgres:5432` and `redis:6379` both fail (confirmed PASS, exit code 1 on both).

---

## Finding 6 — Persistence/backup: PostgreSQL data directory was on `tmpfs` (non-persistent)

- **Risk and evidence:** The original config mounted the named volume to an unused path (`/var/lib/postgresql/backup`) while the actual PostgreSQL data directory (`/var/lib/postgresql/data`) was `tmpfs` — in-memory, wiped on every container removal. See `troubleshooting.md` Entry 9.
- **Impact:** Any data written to the database would be permanently lost the moment the container was recreated (e.g. during a routine deployment, a crash-and-restart, or a host reboot) — a silent, total data-loss bug that would only be discovered the first time it actually happened in a real incident.
- **Implemented fix / commit:** Corrected the volume mount to the real data directory and removed the `tmpfs` entry; verified with a live test (create record → `docker compose down` → `docker compose up -d --build` → record still present). Commit "Complete docker-compose fixes: persistence, network isolation, restart policies, resource limits, verified image digests".
- **Production follow-up:** The named volume still lives on a single host and doesn't survive host-level disk failure. Add off-host backup replication (e.g. scheduled `pg_dump` to external object storage, or a managed database service with built-in point-in-time recovery) rather than relying solely on the local Docker volume. `backup.sh`/`restore.sh` as implemented are manual/on-demand — a production system needs this automated and scheduled, with backup integrity periodically test-restored.
- **How to verify:** See `troubleshooting.md` Entry 9's full persistence test transcript, and `backup.sh`/`restore.sh` execution logs.

---

## Finding 7 — Logging/monitoring: no centralized log aggregation or alerting

- **Risk and evidence:** All three logs (`access.log`, `error.log`, `application.log`) exist only as local files inside their respective containers (or as historical files supplied for this exercise). There is no log shipping, centralized aggregation, or alerting configured.
- **Impact:** In a real incident (e.g. the Redis/Postgres outages analyzed in `log_analysis.md`), an operator would have no automated notification and would need to manually inspect container logs after the fact — exactly as this analysis had to do retrospectively. A production incident could persist far longer before being noticed.
- **Implemented fix / commit:** None — this is out of scope for the current environment and is flagged here as a gap rather than something addressed.
- **Production follow-up:** Ship logs to a centralized system (e.g. Loki, ELK/OpenSearch, or a managed logging service) and configure alerts on `dependency_error` events in `application.log` and 5xx-rate spikes in `access.log`, so a Redis/Postgres outage like the ones in `log_analysis.md` triggers a page within seconds instead of being discovered via customer complaints or a later manual log review.
- **How to verify:** N/A (not implemented) — a completed version would be verified by confirming an alert fires within a defined SLA after a simulated dependency failure.

---

## Finding 8 — Availability: PostgreSQL and Redis are single points of failure

- **Risk and evidence:** `docker-compose.yml` runs exactly one `postgres` container and one `redis` container. `log_analysis.md` Incidents 2 and 3 show that when either dependency has a problem, **both** `app-01` and `app-02` are affected simultaneously — the app-tier redundancy (two instances behind NGINX) does not protect against a shared-dependency failure, since both instances depend on the same single Postgres/Redis.
- **Impact:** A single Postgres or Redis failure (crash, resource exhaustion, network partition) causes a full application-wide outage for any endpoint touching that dependency, despite having "two backends" — the redundancy that matters most for these incidents doesn't exist.
- **Implemented fix / commit:** None — this is a known, accepted limitation of the current single-node lab environment, explicitly called out in `log_analysis.md`'s Conclusions section (Q10) rather than left undocumented.
- **Production follow-up:** Run PostgreSQL with a standby replica and automated failover (e.g. Patroni, or a managed service like RDS Multi-AZ / Cloud SQL HA); run Redis in a Sentinel or Cluster configuration with at least one replica. Both changes require corresponding application-level changes (e.g. connection retry/failover logic) beyond what `app/server.py` currently implements.
- **How to verify:** N/A (not implemented) — a completed version would be verified by killing the primary Postgres/Redis node during a load test and confirming requests continue to succeed via automatic failover, analogous to `failure_test.py`'s existing app-tier test.

---

## Finding 9 — Unrelated project files (`.sf/` Salesforce metadata) accidentally tracked in early git history

- **Risk and evidence:** An early local git setup mistake (documented in this project's working history) resulted in an unrelated `.sf/orgs/.../catalog.json` file from a different, unrelated project being staged for commit at one point during setup.
- **Impact:** Low direct security impact in this case (the file contains no secrets relevant to BARQ), but accidentally committing files from unrelated projects into a shared repo is a general hygiene risk — it can leak unrelated proprietary code/config, bloats repo size, and signals insufficiently careful `git add` habits that could just as easily catch a real secret next time.
- **Implemented fix / commit:** Avoided adding `.sf/` to any BARQ commit; the working directory was migrated to a clean project folder (`BARQ-DevOps-Internship`) specifically to avoid carrying this contamination forward.
- **Production follow-up:** Add a `.gitignore` entry for `.sf/` and any other editor/tool-specific directories unrelated to the project, and use `git status`/`git diff --staged` as a habitual pre-commit check before every `git commit`, especially in shared or reused local working directories.
- **How to verify:** `git log --all -- .sf/` should show no BARQ-repo commits touching that path; confirmed by inspection of this repository's commit history.

---

## Finding 10 — CI security scan is informational only, not a merge gate

- **Risk and evidence:** `.github/workflows/ci.yml`'s Trivy image scan step runs with `continue-on-error: true` and `exit-code: "0"` — vulnerability findings are logged but never fail the pipeline (see `decisions.md` Decision 4 for the reasoning).
- **Impact:** A newly introduced CRITICAL vulnerability in a base image could be merged and deployed without anyone noticing, since the CI status stays green regardless of scan results.
- **Implemented fix / commit:** Deliberately left as informational, since this was marked optional/extra-credit in the task and the base images will realistically always have some outstanding CVEs that are out of scope to remediate within this assessment.
- **Production follow-up:** Make the scan a hard gate for CRITICAL-severity findings (`exit-code: "1"`, remove `continue-on-error`), with a documented exception/waiver process for cases where a finding is a false positive or genuinely unexploitable in this application's context.
- **How to verify:** Inspect the "Scan images with Trivy" step output in any CI run; changing `exit-code` to `"1"` and re-running against a deliberately outdated base image would confirm the gate behavior once implemented.

---

## Summary: completed vs. planned

**Completed and verified in this submission:** Findings 1 (secrets — confirmed lab-only), 2 (ports), 3 (container user), 4 (image digest integrity), 5 (network isolation), 6 (persistence), 9 (repo hygiene).

**Explicitly out of scope / planned for production only:** Findings 7 (centralized logging/alerting), 8 (database/cache high availability), 10 (CI scan as hard gate) — these require infrastructure (external log aggregation, multi-node database clusters, CI policy changes) beyond what a single-host Docker Compose lab environment can reasonably provide, and are documented here as explicit gaps rather than silently omitted.

---

# Technical Decisions (`decisions.md`)

## Decision 1 — Base image

- **Choice:** `python:3.12-slim-bookworm`, pinned by SHA256 digest, for `app-01`/`app-02`; `postgres:16-alpine`, `redis:7.4-alpine`, `nginx:1.28-alpine`, all digest-pinned.
- **Why:** `-slim`/`-alpine` variants minimize image size and attack surface (fewer installed packages = fewer potential CVEs) compared to full/default variants. Digest pinning (`image@sha256:...`) makes builds reproducible: the exact same bytes are pulled every time, regardless of what `latest` or a mutable tag currently points to.
- **Alternative:** Use unpinned tags (`python:3.12-slim`, `postgres:16-alpine`) for simpler maintenance, or `-alpine` for the Python image too (smaller, but musl-libc can cause subtle compatibility issues with some Python C-extension wheels like `psycopg[binary]`).
- **Trade-off:** Digest pinning trades convenience (no automatic security patches on rebuild) for reproducibility and auditability. A corrupted or stale digest has to be manually re-verified and updated (see Evidence below — this happened during this project).
- **Evidence / commit:** `troubleshooting.md` Entry 14 documents a corrupted digest for the Python base image (`invalid checksum digest length`) discovered via `docker pull`, fixed in commit "Fix corrupted python image digest ... re-pin postgres/redis/nginx digests after verification."
- **Production improvement:** Add a scheduled job (e.g. weekly GitHub Actions cron) that pulls the latest patch version of each pinned image, runs the full test suite against it, and opens a PR updating the digest if it passes — so security patches aren't permanently frozen out by pinning.

---

## Decision 2 — Health checks

- **Choice:** Each Flask container's Docker healthcheck calls `GET /health` (process liveness only — no dependency checks) using `python -c "import urllib.request; ..."` from inside the image, rather than `curl` or `wget`.
- **Why:** `/health` deliberately checks only that the process is alive and can respond, separate from `/ready` (which checks Postgres/Redis). This separation means Docker won't restart a healthy app process just because a shared dependency (Redis/Postgres) is temporarily down — restarting the app wouldn't fix a dependency outage and would cause unnecessary churn. `python -c` was used because the `python:3.12-slim-bookworm` image already has Python installed (no extra package needed), whereas `curl`/`wget` are not installed by default in the slim image and would require an extra `apt-get install` layer.
- **Alternative:** Use `curl -f http://localhost:8080/health` (more common in examples, but requires installing curl into the image); or point the healthcheck at `/ready` instead of `/health` (rejected — conflates process health with dependency health).
- **Trade-off:** The `python -c` one-liner is slightly less readable than a `curl` command, and depends on Python's `urllib` module always being importable — a safe assumption for the Python base image but not portable to other images.
- **Evidence / commit:** `troubleshooting.md` Entry 7 (original healthcheck targeted a non-existent `/healthz` path, causing containers to be permanently marked unhealthy); commit "Fix APP_HOST to 0.0.0.0, correct healthcheck path to /health, ...".
- **Production improvement:** Add a third-tier healthcheck or monitoring alert specifically on `/ready` failures (not just `/health`), since a `/ready` failure indicates a real dependency outage that operators need to know about even though it correctly does *not* trigger a container restart.

---

## Decision 3 — Networks

- **Choice:** Two Docker networks: `frontend` (nginx + both app instances) and `backend` (app instances + postgres + redis, marked `internal: true`). NGINX is attached to `frontend` only, not `backend`.
- **Why:** The task requires blocking direct NGINX access to PostgreSQL/Redis. Putting NGINX on `frontend` only, and marking `backend` as `internal: true` (no route to the outside world), enforces this at the Docker network layer rather than relying on application-level discipline — even a misconfigured NGINX location block couldn't reach postgres/redis, because there is no network path between them.
- **Alternative:** A single flat network for all five services (simpler, but gives NGINX a route to postgres/redis it doesn't need); or three networks (separate app↔postgres and app↔redis networks) for even tighter segmentation.
- **Trade-off:** Two networks is a reasonable middle ground — meaningfully more isolated than one flat network, without the added complexity of a third network for marginal extra isolation between Postgres and Redis (which trust each other no more or less than the app does).
- **Evidence / commit:** `troubleshooting.md` Entry 11 (original config had NGINX on both networks); retest evidence via `validate.py`'s network-isolation check (`docker exec nginx` attempting `nc -z postgres 5432` / `redis 6379`, both correctly fail) — see `validate.py` output, 17/17 PASS.
- **Production improvement:** Split `backend` into per-dependency networks (`app↔postgres`, `app↔redis`) so a future compromise of the Redis container, for example, doesn't put it on the same network segment as PostgreSQL.

---

## Decision 4 — Timeouts / retries

- **Choice:** NGINX upstream configured with `max_fails=3 fail_timeout=5s` and `proxy_next_upstream error timeout invalid_header http_502 http_503 http_504;`; `proxy_read_timeout 3s`.
- **Why:** The task requires demonstrating automatic failover when one backend goes down. `max_fails=3 fail_timeout=5s` tells NGINX to temporarily mark a backend as down after 3 failures within 5 seconds (rather than the original `max_fails=0`, which disabled failure tracking entirely); `proxy_next_upstream` with an explicit list of trigger conditions tells NGINX to retry a failed request against the other backend before giving up (the original had this set to `off`).
- **Alternative:** More aggressive failover (`max_fails=1`) trips faster but risks flapping a backend that had one transient blip; a longer `fail_timeout` reduces flapping but leaves a backend marked "down" for longer than necessary once it actually recovers.
- **Trade-off:** `proxy_read_timeout 3s` is short enough to fail fast on a truly stuck backend, but `log_analysis.md` Incident 4 shows this can be *too* short relative to a legitimately slow (but ultimately successful) `/records` query (~2.7s) — NGINX gives up and returns 504 to the client even though the backend was about to succeed.
- **Evidence / commit:** `troubleshooting.md` Entry 5 (original `max_fails=0` + `proxy_next_upstream off` disabled failover) with `failure_test.py` retest evidence: 5/5 requests succeeded via the surviving backend during a live outage, 0 failures. Separately, `log_analysis.md` Incident 4 documents the read-timeout-too-short case using the historical logs.
- **Production improvement:** Give `/records` (and any other DB-heavy endpoint) a longer, path-specific `proxy_read_timeout` via a separate `location` block, so genuinely slow queries aren't punished with a client-facing 504 while the backend was still working correctly.

---

## Decision 5 — Restart / resource settings

- **Choice:** `restart: unless-stopped` on every service; per-service CPU/memory limits via `deploy.resources.limits` (e.g. app containers 0.5 CPU / 256M, redis 0.3 CPU / 128M, nginx 0.3 CPU / 64M).
- **Why:** The original compose file had `restart: "no"` (via the shared `x-app` anchor) and no resource limits at all — a crashed container would stay down permanently, and a single runaway container could consume unbounded host resources. `unless-stopped` restarts a crashed container automatically but respects an operator's explicit `docker stop`.
- **Alternative:** `restart: always` (also restarts after a reboot even if the operator intentionally stopped it — rejected as surprising behavior for a lab/assessment environment); `on-failure` with a max retry count (avoids infinite restart loops on a container that's crash-looping due to a real bug, at the cost of eventually giving up and staying down).
- **Trade-off:** `unless-stopped` will restart a container indefinitely even if it's crash-looping due to a genuine bug (e.g. a bad config change), which could mask a persistent problem behind repeated silent restarts rather than surfacing it clearly.
- **Evidence / commit:** `troubleshooting.md` Entry 12; resource limits and restart policy confirmed present via `docker compose ps` (all containers `Up`) — restart policy inspection command documented in Entry 12's retest evidence.
- **Production improvement:** Switch to `restart: on-failure:5` (or similar) plus integrate with an external alerting system, so a crash-looping container pages an operator instead of silently restarting forever; tune resource limits based on real load-test data rather than the conservative estimates used here.

---

## Decision 6 — Storage

- **Choice:** Named Docker volume `postgres-data` mounted at PostgreSQL's actual data directory (`/var/lib/postgresql/data`); named volume `redis-data` mounted at `/data` with `--appendonly yes`.
- **Why:** The task requires that a record created via `/records` survives an app + Postgres container recreation. The original config mounted the named volume to an unused path (`/var/lib/postgresql/backup`) while the real data directory was `tmpfs` (in-memory, wiped on container removal) — meaning nothing was actually persisted despite a volume being defined. Fixing the mount path, and enabling Redis's AOF persistence (`appendonly yes`) instead of the original `--save ""` (which disabled all Redis persistence), makes both databases' data genuinely survive container recreation.
- **Alternative:** Use bind mounts to a host directory instead of named volumes (simpler to inspect from the host, but less portable and Docker-managed-volume best practice recommends named volumes for this use case); leave Redis without persistence entirely, since the `/counter` value is arguably not critical data (rejected — the task doesn't distinguish "critical" vs "non-critical" state, and persistence should be uniform and provable).
- **Trade-off:** AOF persistence for Redis has a small, constant write overhead compared to no persistence at all, in exchange for surviving a container restart.
- **Evidence / commit:** `troubleshooting.md` Entry 9, with a live full persistence test: create record → `docker compose down` → `docker compose up -d --build` → record still present in `GET /records`. Also verified via `restore.sh` correctly replacing the `records` table contents from an earlier backup (row count dropped from 6 to 5 as expected).
- **Production improvement:** Add off-host backup replication (e.g. periodic `pg_dump` to external object storage) rather than relying solely on the named volume, since a named volume still lives on the same host and doesn't protect against host-level disk failure.

---

## Decision 7 — Other meaningful choice: container user (non-root)

- **Choice:** The Dockerfile creates a dedicated non-root user `app` (uid 10001) and runs the container as that user (`USER app`), rather than as root.
- **Why:** Running application containers as root is a well-known security anti-pattern — if the application process is ever compromised (e.g. via a dependency vulnerability), running as a non-root user limits what the attacker can do inside the container (can't modify files owned by root, can't bind to privileged ports below 1024, etc.).
- **Alternative:** Run as root but drop specific Linux capabilities instead (more granular, but more complex to get right and less commonly needed for this application's simple Flask/HTTP workload); use a distroless base image with no shell at all (stronger isolation, but complicates debugging during development).
- **Trade-off:** Running as a fixed uid (10001) can occasionally cause file-permission friction if bind-mounting host directories with different ownership — not an issue here since the app writes nothing to disk itself, but worth noting for future changes.
- **Evidence / commit:** `troubleshooting.md` Entry 13 (original Dockerfile created the `app` user correctly but then had a stray `USER root` directive right before `CMD`, silently reverting to root); commit "Fix Dockerfile to run as non-root user".
- **Production improvement:** Add a CI check (e.g. `docker run --rm <image> whoami` asserting the output is `app`, not `root`) so a regression like Entry 13 fails CI automatically instead of requiring manual review to catch.

```