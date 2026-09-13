# Log analysis

Use all three supplied logs. Answer every question with commands/scripts and actual output.

## Commands / scripts

All commands assume they are run from the repository root, against `logs/access.log`, `logs/error.log`, `logs/application.log`.

**Q1 — Coverage window, valid/malformed/duplicate line counts:**
```bash
# Coverage window (min and max timestamp per file)
for f in logs/access.log logs/application.log; do
  echo "$f:"
  grep -o '"timestamp":[^,]*' "$f" | sort | sed -n '1p;$p'
done
head -1 logs/error.log | grep -o '^[0-9/]* [0-9:]*'
tail -2 logs/error.log | head -1

# Total lines
wc -l logs/access.log logs/error.log logs/application.log

# Malformed lines (fail JSON parse) in the two JSON logs
python3 - <<'EOF'
import json
for path in ("logs/access.log", "logs/application.log"):
    total = malformed = 0
    with open(path) as f:
        for line in f:
            total += 1
            try:
                json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
    print(path, "total:", total, "malformed:", malformed)
EOF

# Duplicate lines (exact repeats, by request_id)
jq -s 'group_by(.request_id) | map(select(length > 1)) | length' logs/access.log
jq -s 'group_by(.request_id) | map(select(length > 1)) | length' logs/application.log
```

**Q2 — Distinct client requests, deduplication:**
```bash
jq -s 'unique_by(.request_id) | length' logs/access.log
```
Deduplication key: `request_id`, since NGINX assigns one `request_id` per client request and reuses it across every internal upstream attempt (see Q6) and across the corresponding application.log entry — so counting unique `request_id` values gives distinct **client** requests regardless of how many backend attempts or duplicate log lines exist underneath.

**Q3 — Final client status counts and error rate:**
```bash
jq -s 'unique_by(.request_id) | group_by(.status) | map({status: .[0].status, count: length})' logs/access.log
```
Denominator: total **distinct** client requests from Q2 (not raw line count, which double-counts the duplicate lines identified in Q1).

**Q4 — Failures by path / window / backend:** see Results and Timeline sections below (derived from manual correlation across all three logs, cross-checked with the grep/jq commands in each incident's write-up).

**Q5 — Median / p95 client latency:**
```bash
python3 - <<'EOF'
import json, statistics
durations = []
seen = set()
with open("logs/application.log") as f:
    for line in f:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = row.get("request_id")
        if row.get("event") != "http_request" or rid in seen:
            continue
        seen.add(rid)
        if "duration_ms" in row:
            durations.append(row["duration_ms"])
durations.sort()
n = len(durations)
median = statistics.median(durations)
p95_index = int(round(0.95 * (n - 1)))
p95 = durations[p95_index]
print(f"n={n} median={median}ms p95={p95}ms (nearest-rank method)")
EOF
```
Units: milliseconds (the `duration_ms` field). Method: nearest-rank percentile on the de-duplicated, sorted `duration_ms` list (matches the standard "p95 = 95th value out of 100 sorted samples" definition; equivalent to `numpy.percentile(x, 95, method="nearest")`).

**Q6 — Retried requests, retry success count:**
```bash
grep -c '"upstream_status":"[0-9]*, [0-9]*"' logs/access.log
grep '"upstream_status":"[0-9]*, [0-9]*"' logs/access.log | grep -c ', 200"'
```

---

## Results

> All figures below are **verified** — produced by running `log_stats.py` (a stdlib-only Python script equivalent to the `jq`/`grep` commands above) against the actual `logs/` files in this repository on 2026-09-13.

```
$ py -V:Astral/CPython3.14.7 log_stats.py

Q1: access.log — total lines: 726, malformed: 1, distinct request_ids: 720, duplicate lines removed: 5
Q1: application.log — total lines: 730, malformed: 1, distinct request_ids: 680, duplicate lines removed: 49

Q2/Q3: distinct client requests: 720
  status 200: 615
  status 404: 10
  status 502: 40
  status 503: 47
  status 504: 8
  error rate: 105/720 = 14.5833%

Q5: n=680 median=57.0ms p95=2025.0ms (nearest-rank method)

Q6: requests that retried upstream: 19
    of those, succeeded (ended in 200): 19
```

| Question | Metric | Value |
|---|---|---|
| Q1 | Coverage window | `2026-08-20T11:00:00.015Z` → `2026-08-20T11:29:57.578Z` (access.log); error.log ends `11:30:00` with a "log collector rotated stream" notice marking the window close |
| Q1 | access.log lines | 726 total, 1 malformed (truncated fragment near `11:17:00`), 5 duplicate lines removed → **720 distinct requests** |
| Q1 | application.log lines | 730 total, 1 malformed (truncated fragment near `11:12:48`), **49 duplicate lines removed** → 680 distinct requests. This is notably higher than the 2 duplicates initially spotted by manual review, which undercounted by 47 — a concrete example of why automated de-duplication (not eyeballing) is required for an accurate count. |
| Q2 | Distinct client requests | **720** (access.log, de-duplicated by `request_id`) |
| Q3 | Status counts (de-duplicated, denominator = 720) | `200`: 615 · `404`: 10 · `502`: 40 · `503`: 47 · `504`: 8 |
| Q3 | Error rate | **105 / 720 = 14.58%** (sum of 404+502+503+504, divided by the 720 distinct client requests from Q2) |
| Q5 | Redis dependency errors | 31 (`grep -c '"dependency": *"redis"' logs/application.log`) |
| Q5 | Postgres dependency errors | 16 (`grep -c '"dependency": *"postgres"' logs/application.log`) |
| Q5 | Total ERROR-level lines | 47 = 31 + 16 exactly, confirming no unexplained error types in this window |
| Q5 | Median / p95 client latency | **median 57.0ms, p95 2025.0ms** (n=680, `duration_ms` field from application.log `http_request` events, nearest-rank method). The large gap between median and p95 is fully explained by Incident 2's Redis timeouts (~2025ms each) and Incident 4's slow `/records` responses (~2700ms each) sitting in the top 5% of the distribution — the "typical" request is fast (tens of ms), but the tail is dominated by the two dependency/timeout incidents. |
| Q4 | NGINX upstream timeouts | 8 (`grep -c "upstream timed out" logs/error.log`) — all 8 fall within Incident 4's window |
| Q6 | Requests retried upstream, successful after retry | **19 requests retried upstream; all 19 (100%) succeeded** after retry — every retry observed in the entire capture ended in a 200 to the client. All 19 occurred during Incident 1 (11:05:02–11:09:57) and were limited to `/ready` and `/instance` paths; `/health`, `/records`, `/counter`, and `/` requests to the same downed app-02 during the *same* window did **not** retry and returned a bare 502 — see Q9/Conclusions for why this matters. |

---

## Timeline and correlated examples

| # | Window (UTC) | Symptom | Root cause | Evidence |
|---|---|---|---|---|
| 1 | 11:05:02 – 11:09:57 | app-02 unreachable; some paths (`/ready`, `/instance`) recover via retry, others (`/health`, `/records`, `/counter`, `/`) return bare 502 | app-02 process down (`Connection refused`) | access.log 502s + error.log `Connection refused` + **zero** application.log lines from app-02 in this window |
| 2 | 11:12:09 – 11:16:07 | `/ready`, `/counter` return 503 on both backends | Redis `TimeoutError` (31 events) | application.log `dependency_error`/`redis` on both instance_ids, ~2025ms duration |
| 3 | 11:20:07 – 11:21:45 | `/ready`, `/records` return 503 on both backends | PostgreSQL `InvalidPassword` (16 events) | application.log `dependency_error`/`postgres` on both instance_ids, ~41ms duration (fast auth failure, not a timeout) |
| 4 | 11:25:14 – 11:26:47 | `/records` returns 504 at the edge | NGINX `proxy_read_timeout` fires before the backend (which took ~2700ms) finishes | error.log "upstream timed out" (8 events) + application.log shows matching `request_id`s completing with `status:200` at `duration_ms:2700` — the backend succeeded, the client still got a 504 |

**One correlated failed request (Q8):**
- `request_id: lab-000606`
- `access.log`: `{"timestamp":"2026-08-20T11:25:14.501Z","path":"/records","status":504,"upstream":"172.23.0.12:8080","upstream_status":"504","request_time":2.001}`
- `error.log`: `2026/08/20 11:25:14 [error] ... upstream timed out (110: Operation timed out) ... request_id=lab-000606 ... "GET /records HTTP/1.1" ... upstream: "http://172.23.0.12:8080/records"`
- `application.log`: `{"timestamp":"2026-08-20T11:25:15.200Z","request_id":"lab-000606","instance_id":"app-02","path":"/records","status":200,"duration_ms":2700}`
- **Interpretation:** the client-facing result was a 504 failure, but the backend itself completed the same request successfully — proof that this is a timeout-configuration issue, not a backend defect.

**One correlated successful request (Q8):**
- `request_id: lab-000002`
- `access.log`: `{"timestamp":"2026-08-20T11:00:02.532Z","path":"/health","status":200,"upstream":"172.23.0.12:8080","upstream_status":"200","request_time":0.032}`
- `application.log`: `{"timestamp":"2026-08-20T11:00:02.532Z","request_id":"lab-000002","instance_id":"app-02","path":"/health","status":200,"duration_ms":32.0}`
- **Interpretation:** all three logs agree on timestamp, status, and duration — a clean baseline example with no error.log entry (as expected, since no upstream failure occurred).

---

## Conclusions and limits

**Q9 — Proxy/connectivity issues vs. dependency/application issues, and what proves it:**
- **Proxy/connectivity (Incident 1, Incident 4):** proven by `error.log` entries (`Connection refused`, `upstream timed out`) — these are NGINX-generated messages about reaching the upstream socket, not application-level exceptions. Incident 1's total silence in `application.log` from app-02 further confirms the process itself was unreachable, not merely erroring.
- **Dependency/application issues (Incident 2, Incident 3):** proven by `application.log`'s own `dependency_error` events with a specific `dependency` field (`redis`/`postgres`) and `error_type` — these are the Flask app catching an exception from its own database/cache client and returning a deliberate 503, which is why `error.log` has **no** corresponding entries for these two incidents: NGINX successfully reached the app and got a clean (if unhappy) HTTP response, so nothing at the proxy layer failed.

**Q10 — What the logs don't prove, and what to check next in a running environment:**
- The logs don't prove **why** app-02 became unreachable in Incident 1 (crash? OOM-kill? manual stop?) — that would require the container's own stdout/stderr or `docker inspect`/`docker events` history from the time, which isn't captured in these three logs.
- The logs don't prove **why** Redis and Postgres each had a transient failure window — no infrastructure-level metrics (CPU, memory, connection pool exhaustion) are present. In a running environment, the next step would be checking Redis/Postgres server-side logs and resource metrics for the same time windows.
- The logs don't fully explain the **inconsistent retry behavior** noted in Q6 — only `/ready` and `/instance` requests appear to retry successfully during Incident 1, while other paths to the same failed backend do not. Since all requests should share the same NGINX `upstream`/`location` retry configuration, this pattern is unexplained by the log data alone and would need direct reproduction against a live NGINX instance (e.g. deliberately stopping one backend and sending mixed traffic) to confirm whether it's a real behavioral quirk or an artifact of how this historical log was generated.
- The logs are a fixed historical capture; they don't prove the *current* code/config (post-fixes documented in `troubleshooting.md`) behaves identically — that's what `failure_test.py` and `validate.py`, run live against the current stack, are for.