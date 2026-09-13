# Troubleshooting journal

## Entry 1 / 2026-09-12 / initial code review
- **Symptom:** Before running the environment, reviewed all source files (Dockerfile, docker-compose.yml, app/server.py, nginx/nginx.conf, config/app.env) to establish a baseline of expected behavior per the required endpoints (/, /health, /ready, /instance, /records, /counter).
- **Hypothesis:** The task states the environment is "broken" without disclosing issue types or count — assumed multiple misconfigurations across networking, environment variables, and Docker settings.
- **Command or test:** Manual code review, comparing docker-compose.yml environment values against app/server.py's expected env vars, and nginx.conf's upstream config against docker-compose.yml's service names/ports.
- **Actual output:** Identified 16 distinct issues (listed in Entries 2–14 below), spanning config file mismatches, networking/security misconfigurations, a Dockerfile privilege issue, and a corrupted image digest.
- **Failed attempt:** None yet — this was a static review before any execution.
- **Root cause:** N/A (investigation entry)
- **Fix:** N/A (investigation entry)
- **Retest evidence:** N/A (investigation entry)
- **Related commit:** (baseline commit hash)
- **Remaining uncertainty:** Whether all issues were caught, or additional runtime-only issues would surface once the stack was started.

---

## Entry 2 / 2026-09-12 / DATABASE_URL / REDIS_URL mismatch
- **Symptom:** `config/app.env` specified `DATABASE_URL` with password `BarqLabOnly_7qN2vK8d` and port `5433`, while `docker-compose.yml` set `POSTGRES_PASSWORD` to `BarqLabOnly_7qN2vK8c` and exposed Postgres internally on the default port `5432` (no port remap). `REDIS_URL` specified port `6380` while Redis runs on the default port `6379` with no remap.
- **Hypothesis:** Password and port mismatches would cause the app to fail connecting to Postgres/Redis, surfacing as `/ready` returning `postgres: unavailable` / `redis: unavailable`.
- **Command or test:** Manual diff between `config/app.env` and `docker-compose.yml` environment/image defaults.
- **Actual output:** Confirmed values differed by one character (password) and by port number.
- **Failed attempt:** None — caught via code review before first run, so no failed live attempt was made for this issue.
- **Root cause:** Typos in `config/app.env`: wrong password character and wrong ports for both services.
- **Fix:** Corrected `DATABASE_URL` to use password `BarqLabOnly_7qN2vK8c` and port `5432`; corrected `REDIS_URL` to port `6379`.
- **Retest evidence:**
  ```
  curl http://localhost:8080/ready
  -> {"dependencies":{"postgres":"ready","redis":"ready"}, ..., "status":"ready"}
  ```
  (see terminal output, 2026-09-13)
- **Related commit:** "Fix config mismatches: correct DB/Redis ports+password, add app-02 to nginx upstream, fix upstream port and listen port"
- **Remaining uncertainty:** None — confirmed working via live `/ready` check.

---

## Entry 3 / 2026-09-12 / nginx upstream missing app-02 and wrong port
- **Symptom:** `nginx/nginx.conf`'s `upstream application_pool` block only listed `app-01:8081`; `app-02` was absent, and the port (8081) did not match the Flask app's actual listening port (8080, per `APP_PORT`).
- **Hypothesis:** With only one backend registered and the wrong port, NGINX would either fail to reach any backend, or would only ever serve app-01, breaking the "both backends serve requests" requirement.
- **Command or test:** Manual review of `nginx.conf` against `docker-compose.yml`'s `APP_PORT` value.
- **Actual output:** Confirmed app-02 missing, port mismatch (8081 vs 8080).
- **Failed attempt:** None — caught via code review.
- **Root cause:** Incomplete/incorrect upstream block in the original `nginx.conf`.
- **Fix:** Added `server app-02:8080 max_fails=3 fail_timeout=5s;` alongside app-01, corrected port to 8080 for both.
- **Retest evidence:**
  ```
  curl http://localhost:8080/instance   (repeated 4x)
  -> "instance_id":"app-01"
  -> "instance_id":"app-02"
  -> "instance_id":"app-01"
  -> "instance_id":"app-02"
  ```
  (see terminal output, 2026-09-13), proving both backends are reachable through NGINX.
- **Related commit:** "Fix config mismatches: correct DB/Redis ports+password, add app-02 to nginx upstream, fix upstream port and listen port"
- **Remaining uncertainty:** None — confirmed alternating responses.

---

## Entry 4 / 2026-09-12 / nginx listen port vs compose port mapping mismatch
- **Symptom:** `nginx.conf` had `listen 80;` but `docker-compose.yml` mapped the host port to container port 81 (`127.0.0.1:${PUBLIC_PORT:-8080}:81`).
- **Hypothesis:** NGINX would not accept connections on the port Docker was forwarding to, causing connection-refused errors on the host.
- **Command or test:** Manual review of port mapping vs nginx `listen` directive.
- **Actual output:** Confirmed mismatch (80 vs 81).
- **Failed attempt:** None — caught via code review.
- **Root cause:** `nginx.conf`'s listen directive was never updated to match the compose port mapping.
- **Fix:** Changed `listen 80;` to `listen 81;` to match the compose mapping.
- **Retest evidence:** `curl http://localhost:8080/` returns HTTP 200 (see terminal output), confirming the host-to-container port chain works end to end.
- **Related commit:** "Fix config mismatches: correct DB/Redis ports+password, add app-02 to nginx upstream, fix upstream port and listen port"
- **Remaining uncertainty:** None.

---

## Entry 5 / 2026-09-12 / max_fails=0 and proxy_next_upstream off disable failover
- **Symptom:** `nginx.conf` had `max_fails=0` on the upstream server and `proxy_next_upstream off;`, which together disable NGINX's automatic failover to a healthy backend if one fails.
- **Hypothesis:** This would break the Part 3 requirement to demonstrate traffic continuing and recovering when one backend is stopped.
- **Command or test:** Manual review of nginx failover directives.
- **Actual output:** Confirmed both settings would prevent failover.
- **Failed attempt:** None yet — `failure_test.py` has not been run against this config; this fix was applied preemptively before the failure test, and will be validated when `failure_test.py` is executed.
- **Root cause:** Failover was explicitly disabled in the original config.
- **Fix:** Changed to `max_fails=3 fail_timeout=5s` and `proxy_next_upstream error timeout invalid_header http_502 http_503 http_504;`.
- **Retest evidence:** Pending — to be confirmed with `failure_test.py` (stopping one backend and observing continued service).
- **Related commit:** "Fix config mismatches: correct DB/Redis ports+password, add app-02 to nginx upstream, fix upstream port and listen port"
- **Remaining uncertainty:** Not yet retested under an actual backend failure; planned for Part 3 testing.

---

## Entry 6 / 2026-09-12 / APP_HOST bound to 127.0.0.1 instead of 0.0.0.0
- **Symptom:** `docker-compose.yml`'s shared `x-app` environment set `APP_HOST: "127.0.0.1"`.
- **Hypothesis:** Flask running inside a container and binding only to its own loopback interface would be unreachable from NGINX (a separate container), even on the same Docker network.
- **Command or test:** Manual review of `app/server.py`'s `app.run(host=os.getenv("APP_HOST", ...))` against the compose environment value.
- **Actual output:** Confirmed the bind address would prevent cross-container access.
- **Failed attempt:** None — caught via code review before first run.
- **Root cause:** Misconfigured environment variable in `docker-compose.yml`.
- **Fix:** Changed `APP_HOST` to `"0.0.0.0"`.
- **Retest evidence:** `curl http://localhost:8080/` succeeds through NGINX → app containers (see terminal output), confirming the apps are reachable over the Docker network.
- **Related commit:** "Fix APP_HOST to 0.0.0.0, correct healthcheck path to /health, fix app-02 duplicate INSTANCE_ID"
- **Remaining uncertainty:** None.

---

## Entry 7 / 2026-09-12 / healthcheck targets /healthz, which does not exist
- **Symptom:** `docker-compose.yml`'s `x-app` healthcheck called `http://127.0.0.1:8080/healthz`, but `app/server.py` only defines a `/health` route.
- **Hypothesis:** The healthcheck would always fail with 404, marking containers unhealthy regardless of actual app status.
- **Command or test:** Manual review of `server.py` routes vs healthcheck URL.
- **Actual output:** Confirmed no `/healthz` route exists; only `/health`.
- **Failed attempt:** None — caught via code review.
- **Root cause:** Typo/mismatch in healthcheck URL.
- **Fix:** Changed healthcheck URL to `/health`.
- **Retest evidence:** `docker compose ps` shows `app-01` and `app-02` as `Up ... (healthy)` (see terminal output, 2026-09-13).
- **Related commit:** "Fix APP_HOST to 0.0.0.0, correct healthcheck path to /health, fix app-02 duplicate INSTANCE_ID"
- **Remaining uncertainty:** None.

---

## Entry 8 / 2026-09-12 / app-02 shares INSTANCE_ID with app-01
- **Symptom:** In `docker-compose.yml`, the `app-02` service environment block set `INSTANCE_ID: "app-01"` instead of `"app-02"`.
- **Hypothesis:** This would make both backends report identical identities via `/instance`, making it impossible to prove two distinct backends are being load balanced.
- **Command or test:** Manual review of app-01 vs app-02 environment blocks.
- **Actual output:** Confirmed both were set to `"app-01"`.
- **Failed attempt:** None — caught via code review.
- **Root cause:** Copy-paste error when defining app-02's environment override.
- **Fix:** Changed app-02's `INSTANCE_ID` to `"app-02"`.
- **Retest evidence:**
  ```
  curl http://localhost:8080/instance   (repeated)
  -> "instance_id":"app-01"
  -> "instance_id":"app-02"
  ```
  (see terminal output, 2026-09-13).
- **Related commit:** "Fix APP_HOST to 0.0.0.0, correct healthcheck path to /health, fix app-02 duplicate INSTANCE_ID"
- **Remaining uncertainty:** None.

---

## Entry 9 / 2026-09-12 / PostgreSQL data directory mounted as tmpfs, volume mount path wrong
- **Symptom:** `docker-compose.yml` mounted the named volume `postgres-data` to `/var/lib/postgresql/backup` (an arbitrary path with no significance to Postgres), while the actual data directory `/var/lib/postgresql/data` was mounted as `tmpfs` (in-memory, non-persistent).
- **Hypothesis:** Any records created would be lost on container recreation, since the real data directory was never persisted to disk.
- **Command or test:** Manual review of the postgres service's `volumes` and `tmpfs` keys.
- **Actual output:** Confirmed the persistent volume was mounted to the wrong path and the real data path was ephemeral.
- **Failed attempt:** None yet — this was caught via code review before running the backup/restore test (Part 3). The actual persistence test (create record → recreate containers → verify record survives) is planned but not yet executed.
- **Root cause:** Misconfigured volume/tmpfs mapping in the original `docker-compose.yml`.
- **Fix:** Mounted `postgres-data` to `/var/lib/postgresql/data` and removed the `tmpfs` entry entirely.
- **Retest evidence:** Pending — to be confirmed by the Part 3 backup/restore persistence test (create record via `/records`, recreate app + postgres containers keeping the volume, verify record still present).
- **Related commit:** "Complete docker-compose fixes: persistence, network isolation, restart policies, resource limits, verified image digests"
- **Remaining uncertainty:** Not yet retested with an actual container recreation; planned for Part 3.

---

## Entry 10 / 2026-09-12 / PostgreSQL and Redis ports published to host
- **Symptom:** `docker-compose.yml` published `127.0.0.1:15432:5432` for postgres and `127.0.0.1:16379:6379` for redis.
- **Hypothesis:** The task explicitly requires "Do not publish app, PostgreSQL or Redis ports" — these host-bound ports violate that requirement even though they were bound to localhost only.
- **Command or test:** Manual review of the `ports:` keys for both services.
- **Actual output:** Confirmed both services published host ports.
- **Failed attempt:** None — caught via code review.
- **Root cause:** Unnecessary host port publication in the original compose file.
- **Fix:** Removed the `ports:` entries for both postgres and redis; they remain reachable only via the internal `backend` network.
- **Retest evidence:** `docker compose ps` output (2026-09-13) shows postgres and redis with no host port mappings (only `5432/tcp` and `6379/tcp`, no `0.0.0.0:` or `127.0.0.1:` prefix), confirming they are not published to the host.
- **Related commit:** "Complete docker-compose fixes: persistence, network isolation, restart policies, resource limits, verified image digests"
- **Remaining uncertainty:** None — confirmed via `docker compose ps` port listing.

---

## Entry 11 / 2026-09-12 / nginx connected to backend network
- **Symptom:** `docker-compose.yml`'s nginx service was on both `frontend` and `backend` networks.
- **Hypothesis:** This would allow NGINX direct network access to postgres/redis, violating "Block direct NGINX access to PostgreSQL/Redis."
- **Command or test:** Manual review of nginx's `networks:` key.
- **Actual output:** Confirmed nginx was on both networks.
- **Failed attempt:** None yet — caught via code review; direct verification (attempting to reach postgres/redis from inside the nginx container) is planned as part of `validate.py`'s network isolation check.
- **Root cause:** Unnecessary network attachment in the original compose file.
- **Fix:** Restricted nginx to `frontend` only.
- **Retest evidence:** Pending — to be confirmed by `validate.py`'s network isolation check (e.g. `docker exec nginx` attempting to reach `postgres:5432` and expecting failure).
- **Related commit:** "Complete docker-compose fixes: persistence, network isolation, restart policies, resource limits, verified image digests"
- **Remaining uncertainty:** Not yet retested by directly attempting a connection from inside the nginx container.

---

## Entry 12 / 2026-09-12 / missing restart policy and resource limits
- **Symptom:** All services in the original `docker-compose.yml` had `restart: "no"` (via the shared `x-app` anchor) and no `deploy.resources.limits` defined anywhere.
- **Hypothesis:** The task requires "restart policies and resource limits" for all services; without them, a crashed container stays down and containers have unbounded resource usage.
- **Command or test:** Manual review of `docker-compose.yml`.
- **Actual output:** Confirmed no restart policy (other than `"no"`) and no resource limits existed.
- **Failed attempt:** None — caught via code review.
- **Root cause:** Not configured in the original compose file.
- **Fix:** Set `restart: unless-stopped` on all services; added `deploy.resources.limits` (cpus/memory) per service.
- **Retest evidence:** `docker compose ps` (2026-09-13) shows all containers running; restart policy to be additionally confirmed via `docker inspect <container> --format='{{.HostConfig.RestartPolicy.Name}}'` (planned follow-up command).
- **Related commit:** "Complete docker-compose fixes: persistence, network isolation, restart policies, resource limits, verified image digests"
- **Remaining uncertainty:** Resource limits not yet stress-tested under load.

---

## Entry 13 / 2026-09-12 / Dockerfile reverts to USER root
- **Symptom:** The Dockerfile created a non-root user `app` (uid 10001) but then ran `USER root` immediately before `CMD`, effectively cancelling the non-root setup.
- **Hypothesis:** The container would run as root at runtime despite the earlier `groupadd`/`useradd` steps, violating "avoid root/privileged operation where practical."
- **Command or test:** Manual review of the Dockerfile's final `USER` directive.
- **Actual output:** Confirmed `USER root` was the last user directive before `CMD`.
- **Failed attempt:** None — caught via code review.
- **Root cause:** Leftover/incorrect `USER` directive in the original Dockerfile.
- **Fix:** Removed `USER root` and added `USER app` explicitly before `CMD`; also fixed ownership of the copied `config/app.env` file to `app:app`.
- **Retest evidence:** Pending — `docker exec app-01 whoami` to be run and logged, expected to return `app`, not `root`.
- **Related commit:** "Fix Dockerfile to run as non-root user"
- **Remaining uncertainty:** `whoami` check inside the running container not yet captured in this log; to be added.

---

## Entry 14 / 2026-09-13 / corrupted python base image digest
- **Symptom:** `docker pull python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d40188aff08145c85b262bb63dc0c7522254` failed with `invalid checksum digest length`.
- **Hypothesis:** The pinned SHA256 digest in the original Dockerfile was malformed (wrong length), possibly a deliberately-seeded bug given the task's "Verify starter values" instruction.
- **Command or test:**
  ```
  docker pull python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d40188aff08145c85b262bb63dc0c7522254
  # -> invalid checksum digest length

  docker pull python:3.12-slim-bookworm
  docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim-bookworm
  # -> python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
  ```
- **Actual output:** The corrected digest differs from the original by one extra "0" character inserted after "d4018": original had `...d40188aff...`, correct is `...d4018aff...`.
- **Failed attempt:** Initially assumed all four pinned digests (python, postgres, redis, nginx) might be wrong; verified all four independently before concluding only python's was corrupted. `docker pull` for postgres:16-alpine, redis:7.4-alpine, and nginx:1.28-alpine with their original pinned digests all succeeded without modification.
- **Root cause:** A single corrupted character in the pinned digest string in the original Dockerfile.
- **Fix:** Replaced with the verified correct digest `sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254`.
- **Retest evidence:** `docker pull python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254` succeeds (confirmed after correction); `docker compose up --build -d` builds successfully using the corrected Dockerfile (see `docker compose ps` output, 2026-09-13, all containers Up).
- **Related commit:** "Fix corrupted python image digest (extra character caused invalid checksum); re-pin postgres/redis/nginx digests after verification"
- **Remaining uncertainty:** None — postgres, redis, and nginx digests in the original file were confirmed correct (matched freshly pulled digests exactly); only python was affected.

---

## Entry 15 / 2026-09-13 / full stack startup verification
- **Symptom:** After applying all fixes above, ran `docker compose up --build -d` for the first time.
- **Hypothesis:** With all identified issues fixed, all five containers should start and reach a healthy state, and all required endpoints should respond correctly.
- **Command or test:** `docker compose up --build -d` followed by `docker compose ps` and manual endpoint checks against `/`, `/health`, `/ready`, `/instance`, `/records` (GET + POST), `/counter`.
- **Actual output:**
  ```
  NAME       STATUS
  app-01     Up (healthy)
  app-02     Up (healthy)
  nginx      Up (healthy, after startup grace period)
  postgres   Up (healthy)
  redis      Up (healthy)
  ```
  - `/` → 200, `{"instance_id":"app-01","message":"Welcome to BARQ Systems",...}`
  - `/health` → 200, `{"instance_id":"app-02","status":"alive",...}`
  - `/ready` → 200, `{"dependencies":{"postgres":"ready","redis":"ready"},"status":"ready",...}`
  - `/instance` (called 4x) → alternates between app-01 and app-02
  - `/records` (GET) → 200, returns the 2 seeded records from `database/init.sql`
  - `/records` (POST, `{"title":"test record"}`) → 201, `{"record":{"id":3,"title":"test record"},...}`
  - `/counter` → 200, `{"counter":1,...}`, increments correctly via Redis
- **Failed attempt:** First `POST /records` attempt failed using PowerShell's `curl` alias (`Invoke-WebRequest`), which does not accept `-H`/`-d` flags the same way as real curl — not an application bug. Retried successfully with:
  ```
  Invoke-WebRequest -Uri "http://localhost:8080/records" -Method POST -ContentType "application/json" -Body '{"title":"test record"}'
  ```
  which returned `201 CREATED` with `{"id":3,"title":"test record"}`.
- **Root cause:** N/A (this entry documents successful verification, not a new application bug).
- **Fix:** N/A.
- **Retest evidence:** See "Actual output" above — all terminal output captured 2026-09-13.
- **Related commit:** (link the commit that finalized docker-compose.yml/Dockerfile fixes)
- **Remaining uncertainty:** Network isolation (Entry 11), failover under failure (Entry 5), restart policy confirmation (Entry 12), non-root user confirmation (Entry 13), and backup/restore persistence (Entry 9) are all still pending live verification — planned for Part 3 (validate.py, failure_test.py, backup.sh/restore.sh).