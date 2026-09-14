# Technical decisions

Record at least 5 decisions. Include assumptions and limits.

---

## Decision 1 — Base image

- **Choice:** `python:3.12-slim-bookworm`, pinned by SHA256 digest, for `app-01`/`app-02`;
  `postgres:16-alpine`, `redis:7.4-alpine`, `nginx:1.28-alpine`, all digest-pinned.
- **Why:** `-slim`/`-alpine` variants minimize image size and attack surface (fewer installed
  packages = fewer potential CVEs) compared to full/default variants. Digest pinning
  (`image@sha256:...`) makes builds reproducible: the exact same bytes are pulled every time,
  regardless of what `latest` or a mutable tag currently points to.
- **Alternative:** Use unpinned tags (`python:3.12-slim`, `postgres:16-alpine`) for simpler
  maintenance, or `-alpine` for the Python image too (smaller, but musl-libc can cause subtle
  compatibility issues with some Python C-extension wheels like `psycopg[binary]`).
- **Trade-off:** Digest pinning trades convenience (no automatic security patches on rebuild)
  for reproducibility and auditability. A corrupted or stale digest has to be manually
  re-verified and updated (see Evidence below — this happened during this project).
- **Evidence / commit:** `troubleshooting.md` Entry 14 documents a corrupted digest for the
  Python base image (`invalid checksum digest length`) discovered via `docker pull`, fixed in
  commit "Fix corrupted python image digest ... re-pin postgres/redis/nginx digests after
  verification."
- **Production improvement:** Add a scheduled job (e.g. weekly GitHub Actions cron) that pulls
  the latest patch version of each pinned image, runs the full test suite against it, and
  opens a PR updating the digest if it passes — so security patches aren't permanently frozen
  out by pinning.

---

## Decision 2 — Health checks

- **Choice:** Each Flask container's Docker healthcheck calls `GET /health` (process liveness
  only — no dependency checks) using `python -c "import urllib.request; ..."` from inside the
  image, rather than `curl` or `wget`.
- **Why:** `/health` deliberately checks only that the process is alive and can respond,
  separate from `/ready` (which checks Postgres/Redis). This separation means Docker won't
  restart a healthy app process just because a shared dependency (Redis/Postgres) is
  temporarily down — restarting the app wouldn't fix a dependency outage and would cause
  unnecessary churn. `python -c` was used because the `python:3.12-slim-bookworm` image
  already has Python installed (no extra package needed), whereas `curl`/`wget` are not
  installed by default in the slim image and would require an extra `apt-get install` layer.
- **Alternative:** Use `curl -f http://localhost:8080/health` (more common in examples,
  but requires installing curl into the image); or point the healthcheck at `/ready` instead
  of `/health` (rejected — conflates process health with dependency health).
- **Trade-off:** The `python -c` one-liner is slightly less readable than a `curl` command,
  and depends on Python's `urllib` module always being importable — a safe assumption for the
  Python base image but not portable to other images.
- **Evidence / commit:** `troubleshooting.md` Entry 7 (original healthcheck targeted a
  non-existent `/healthz` path, causing containers to be permanently marked unhealthy);
  commit "Fix APP_HOST to 0.0.0.0, correct healthcheck path to /health, ...".
- **Production improvement:** Add a third-tier healthcheck or monitoring alert specifically
  on `/ready` failures (not just `/health`), since a `/ready` failure indicates a real
  dependency outage that operators need to know about even though it correctly does *not*
  trigger a container restart.

---

## Decision 3 — Networks

- **Choice:** Two Docker networks: `frontend` (nginx + both app instances) and `backend`
  (app instances + postgres + redis, marked `internal: true`). NGINX is attached to
  `frontend` only, not `backend`.
- **Why:** The task requires blocking direct NGINX access to PostgreSQL/Redis. Putting NGINX
  on `frontend` only, and marking `backend` as `internal: true` (no route to the outside
  world), enforces this at the Docker network layer rather than relying on application-level
  discipline — even a misconfigured NGINX location block couldn't reach postgres/redis,
  because there is no network path between them.
- **Alternative:** A single flat network for all five services (simpler, but gives NGINX a
  route to postgres/redis it doesn't need); or three networks (separate app↔postgres and
  app↔redis networks) for even tighter segmentation.
- **Trade-off:** Two networks is a reasonable middle ground — meaningfully more isolated than
  one flat network, without the added complexity of a third network for marginal extra
  isolation between Postgres and Redis (which trust each other no more or less than the app
  does).
- **Evidence / commit:** `troubleshooting.md` Entry 11 (original config had NGINX on both
  networks); retest evidence via `validate.py`'s network-isolation check
  (`docker exec nginx` attempting `nc -z postgres 5432` / `redis 6379`, both correctly fail)
  — see `validate.py` output, 17/17 PASS.
- **Production improvement:** Split `backend` into per-dependency networks (`app↔postgres`,
  `app↔redis`) so a future compromise of the Redis container, for example, doesn't put it on
  the same network segment as PostgreSQL.

---

## Decision 4 — Timeouts / retries

- **Choice:** NGINX upstream configured with `max_fails=3 fail_timeout=5s` and
  `proxy_next_upstream error timeout invalid_header http_502 http_503 http_504;`;
  `proxy_read_timeout 3s`.
- **Why:** The task requires demonstrating automatic failover when one backend goes down.
  `max_fails=3 fail_timeout=5s` tells NGINX to temporarily mark a backend as down after 3
  failures within 5 seconds (rather than the original `max_fails=0`, which disabled failure
  tracking entirely); `proxy_next_upstream` with an explicit list of trigger conditions tells
  NGINX to retry a failed request against the other backend before giving up (the original
  had this set to `off`).
- **Alternative:** More aggressive failover (`max_fails=1`) trips faster but risks flapping a
  backend that had one transient blip; a longer `fail_timeout` reduces flapping but leaves a
  backend marked "down" for longer than necessary once it actually recovers.
- **Trade-off:** `proxy_read_timeout 3s` is short enough to fail fast on a truly stuck
  backend, but `log_analysis.md` Incident 4 shows this can be *too* short relative to a
  legitimately slow (but ultimately successful) `/records` query (~2.7s) — NGINX gives up
  and returns 504 to the client even though the backend was about to succeed.
- **Evidence / commit:** `troubleshooting.md` Entry 5 (original `max_fails=0` +
  `proxy_next_upstream off` disabled failover) with `failure_test.py` retest evidence: 5/5
  requests succeeded via the surviving backend during a live outage, 0 failures. Separately,
  `log_analysis.md` Incident 4 documents the read-timeout-too-short case using the historical
  logs.
- **Production improvement:** Give `/records` (and any other DB-heavy endpoint) a longer,
  path-specific `proxy_read_timeout` via a separate `location` block, so genuinely slow
  queries aren't punished with a client-facing 504 while the backend was still working
  correctly.

---

## Decision 5 — Restart / resource settings

- **Choice:** `restart: unless-stopped` on every service; per-service CPU/memory limits via
  `deploy.resources.limits` (e.g. app containers 0.5 CPU / 256M, redis 0.3 CPU / 128M, nginx
  0.3 CPU / 64M).
- **Why:** The original compose file had `restart: "no"` (via the shared `x-app` anchor) and
  no resource limits at all — a crashed container would stay down permanently, and a single
  runaway container could consume unbounded host resources. `unless-stopped` restarts a
  crashed container automatically but respects an operator's explicit `docker stop`.
- **Alternative:** `restart: always` (also restarts after a reboot even if the operator
  intentionally stopped it — rejected as surprising behavior for a lab/assessment
  environment); `on-failure` with a max retry count (avoids infinite restart loops on a
  container that's crash-looping due to a real bug, at the cost of eventually giving up and
  staying down).
- **Trade-off:** `unless-stopped` will restart a container indefinitely even if it's
  crash-looping due to a genuine bug (e.g. a bad config change), which could mask a
  persistent problem behind repeated silent restarts rather than surfacing it clearly.
- **Evidence / commit:** `troubleshooting.md` Entry 12; resource limits and restart policy
  confirmed present via `docker compose ps` (all containers `Up`) — restart policy inspection
  command documented in Entry 12's retest evidence.
- **Production improvement:** Switch to `restart: on-failure:5` (or similar) plus integrate
  with an external alerting system, so a crash-looping container pages an operator instead of
  silently restarting forever; tune resource limits based on real load-test data rather than
  the conservative estimates used here.

---

## Decision 6 — Storage

- **Choice:** Named Docker volume `postgres-data` mounted at PostgreSQL's actual data
  directory (`/var/lib/postgresql/data`); named volume `redis-data` mounted at `/data` with
  `--appendonly yes`.
- **Why:** The task requires that a record created via `/records` survives an app + Postgres
  container recreation. The original config mounted the named volume to an unused path
  (`/var/lib/postgresql/backup`) while the real data directory was `tmpfs` (in-memory, wiped
  on container removal) — meaning nothing was actually persisted despite a volume being
  defined. Fixing the mount path, and enabling Redis's AOF persistence (`appendonly yes`)
  instead of the original `--save ""` (which disabled all Redis persistence), makes both
  databases' data genuinely survive container recreation.
- **Alternative:** Use bind mounts to a host directory instead of named volumes (simpler to
  inspect from the host, but less portable and Docker-managed-volume best practice
  recommends named volumes for this use case); leave Redis without persistence entirely,
  since the `/counter` value is arguably not critical data (rejected — the task doesn't
  distinguish "critical" vs "non-critical" state, and persistence should be uniform and
  provable).
- **Trade-off:** AOF persistence for Redis has a small, constant write overhead compared to
  no persistence at all, in exchange for surviving a container restart.
- **Evidence / commit:** `troubleshooting.md` Entry 9, with a live full persistence test:
  create record → `docker compose down` → `docker compose up -d --build` → record still
  present in `GET /records`. Also verified via `restore.sh` correctly replacing the
  `records` table contents from an earlier backup (row count dropped from 6 to 5 as expected).
- **Production improvement:** Add off-host backup replication (e.g. periodic `pg_dump` to
  external object storage) rather than relying solely on the named volume, since a named
  volume still lives on the same host and doesn't protect against host-level disk failure.

---

## Decision 7 — Other meaningful choice: container user (non-root)

- **Choice:** The Dockerfile creates a dedicated non-root user `app` (uid 10001) and runs the
  container as that user (`USER app`), rather than as root.
- **Why:** Running application containers as root is a well-known security anti-pattern — if
  the application process is ever compromised (e.g. via a dependency vulnerability), running
  as a non-root user limits what the attacker can do inside the container (can't modify
  files owned by root, can't bind to privileged ports below 1024, etc.).
- **Alternative:** Run as root but drop specific Linux capabilities instead (more granular,
  but more complex to get right and less commonly needed for this application's simple
  Flask/HTTP workload); use a distroless base image with no shell at all (stronger isolation,
  but complicates debugging during development).
- **Trade-off:** Running as a fixed uid (10001) can occasionally cause file-permission friction
  if bind-mounting host directories with different ownership — not an issue here since the
  app writes nothing to disk itself, but worth noting for future changes.
- **Evidence / commit:** `troubleshooting.md` Entry 13 (original Dockerfile created the `app`
  user correctly but then had a stray `USER root` directive right before `CMD`, silently
  reverting to root); commit "Fix Dockerfile to run as non-root user".
- **Production improvement:** Add a CI check (e.g. `docker run --rm <image> whoami`
  asserting the output is `app`, not `root`) so a regression like Entry 13 fails CI
  automatically instead of requiring manual review to catch.