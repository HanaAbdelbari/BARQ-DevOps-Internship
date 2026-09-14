# AI usage disclosure

AI (Claude, via Anthropic's chat interface) was used throughout this project as a
collaborative assistant for investigation, implementation, and documentation. Every use is
disclosed below. All commands, code, and claims were independently run and verified against
the actual environment before being committed — see "How I independently verified it" in each
entry and the corresponding evidence in `troubleshooting.md` / `log_analysis.md`.

---

## Use 1 — Initial code review and bug identification

- **Tool/model:** Claude (Anthropic)
- **Purpose:** Review the supplied `docker-compose.yml`, `Dockerfile`, `nginx/nginx.conf`,
  `config/app.env`, and `app/server.py` to identify misconfigurations before running anything.
- **Files or decisions affected:** `docker-compose.yml`, `Dockerfile`, `nginx/nginx.conf`,
  `config/app.env`.
- **What you changed or rejected:** Accepted the AI's identification of 15 distinct
  misconfigurations (APP_HOST, healthcheck path, duplicate INSTANCE_ID, postgres
  volume/tmpfs, exposed ports, nginx network placement, missing restart/resource limits,
  Dockerfile USER root, DATABASE_URL/REDIS_URL mismatches, nginx upstream missing app-02,
  wrong upstream port, wrong listen port, disabled failover settings) after confirming each
  one against the actual file contents myself, not taking the AI's claims at face value.
- **How I independently verified it:** Ran the fixed stack (`docker compose up`), tested every
  endpoint with `curl`/`Invoke-WebRequest`, and confirmed each specific symptom described was
  actually present in the original files before accepting the fix as correct. See
  `troubleshooting.md` Entries 1–13 for full symptom/command/output records.
- **Related commit:** "Fix config mismatches...", "Fix APP_HOST to 0.0.0.0...", "Complete
  docker-compose fixes...", "Fix Dockerfile to run as non-root user".

---

## Use 2 — Diagnosing the corrupted image digest

- **Tool/model:** Claude (Anthropic)
- **Purpose:** Investigate why `docker pull` failed with `invalid checksum digest length` on
  the pinned Python base image.
- **Files or decisions affected:** `Dockerfile` (image digest).
- **What you changed or rejected:** The AI suggested checking all four pinned digests
  (python, postgres, redis, nginx) rather than assuming only one was wrong; I ran the
  suggested `docker pull`/`docker inspect` commands myself and confirmed postgres, redis, and
  nginx digests were actually correct, and only the python digest had a corrupted character.
- **How I independently verified it:** Ran `docker pull` with the corrected digest myself and
  confirmed it succeeded; confirmed `docker compose build` then worked end to end.
- **Related commit:** "Fix corrupted python image digest ... re-pin postgres/redis/nginx
  digests after verification".

---

## Use 3 — Writing `validate.py`, `failure_test.py`, `backup.sh`, `restore.sh`

- **Tool/model:** Claude (Anthropic)
- **Purpose:** Generate initial implementations of the four required scripts (all supplied as
  `NOT IMPLEMENTED` stubs) covering the bounded-wait, PASS/FAIL, non-zero-exit-code
  requirements from the brief.
- **Files or decisions affected:** `validate.py`, `failure_test.py`, `backup.sh`, `restore.sh`.
- **What you changed or rejected:** Ran every script against the real environment myself
  rather than trusting the AI's assertion that it would work. This caught a real bug: the
  first version of `validate.py`'s port-exposure check produced a false positive because it
  scanned raw host port 5432, which was also in use by an unrelated PostgreSQL service already
  running on my machine — unrelated to this project. I diagnosed this myself with
  `netstat`/`Get-Process`, and the AI then rewrote the check to query `docker port <container>`
  directly instead of scanning host ports, which I re-tested and confirmed fixed.
- **How I independently verified it:** Ran `validate.py` to a final 15/15 PASS result against
  the live stack; ran `failure_test.py` and confirmed the printed PASS/FAIL summary matched
  what actually happened (backend stopped, traffic continued via the surviving instance,
  backend restored, recovery confirmed); ran `backup.sh` and `restore.sh` and confirmed the
  record count changes matched expectations before and after a real backup/restore cycle.
- **Related commit:** "Implement validate.py...", "Fix false-positive port check...",
  "Implement failure_test.py...", "Implement backup.sh and restore.sh...".

---

## Use 4 — Log analysis (`log_analysis.md`)

- **Tool/model:** Claude (Anthropic)
- **Purpose:** Correlate `access.log`, `error.log`, and `application.log` to identify
  incidents, and draft the answers to the required template questions.
- **Files or decisions affected:** `log_analysis.md`, `log_stats.py` (helper script).
- **What you changed or rejected:** The AI's first pass at counting duplicate lines in
  `application.log` was **factually wrong** and I caught it: it reported "49 duplicate lines
  removed" as if all 49 were exact repeats, but on inspection only 2 were true duplicates —
  the other 47 were legitimate `ERROR`+`WARN` line pairs sharing a `request_id` that a naive
  dedup script incorrectly conflated with duplicates. I asked the AI to verify this and it
  corrected the explanation and the underlying reasoning in the document.
- **How I independently verified it:** Ran the `jq`/Python-based commands myself (via
  `log_stats.py`, since `jq` proved awkward to install as a CLI on Windows) and got the exact
  same numbers reported in the document (720 distinct requests, 105 errors = 14.58% error
  rate, median 57ms/p95 2025ms, 19/19 successful retries) — see the "Results" section of
  `log_analysis.md`, which shows the actual terminal output I obtained.
- **Related commit:** "Add complete log analysis...", "Fix Q1: distinguish true duplicate
  lines...".

---

## Use 5 — `.github/workflows/ci.yml`

- **Tool/model:** Claude (Anthropic)
- **Purpose:** Draft the CI pipeline (checkout → syntax/lint checks → build → start → wait for
  readiness → validate).
- **Files or decisions affected:** `.github/workflows/ci.yml`.
- **What you changed or rejected:** The first version pinned `aquasecurity/trivy-action@0.24.0`,
  which does not exist and broke the CI run at the "Set up job" step
  (`Unable to resolve action ... unable to find version 0.24.0`). I diagnosed this from the
  actual GitHub Actions run log myself and asked the AI to fix the version pin, which it
  changed to `@master`.
- **How I independently verified it:** Pushed the fix and confirmed the CI run progressed past
  "Set up job" on GitHub Actions.
- **Related commit:** "Add CI workflow...", "Fix Trivy action version pin causing CI setup
  failure".

---

## Use 6 — `troubleshooting.md`, `decisions.md`, `security_review.md` drafting

- **Tool/model:** Claude (Anthropic)
- **Purpose:** Draft the investigation journal and the two review documents in the specific
  templates required by the brief.
- **Files or decisions affected:** `troubleshooting.md`, `decisions.md`, `security_review.md`.
- **What you changed or rejected:** Requested the documents be rebuilt twice to match the
  exact required templates once I shared them (the AI's first drafts used a reasonable but
  different structure before I provided the actual required field names/sections). Reviewed
  every entry for factual accuracy against my own terminal output and commit history before
  accepting it.
- **How I independently verified it:** Cross-checked every "Evidence / commit" reference
  against the actual git log and my own saved terminal output; did not accept any claim in
  these documents that I could not trace back to a command I actually ran.
- **Related commit:** "Document investigation journal...", "Add decisions.md...", "Add
  security_review.md...".

---

## General verification practice

For every AI-suggested change across this project, my standard practice was: (1) understand
what the change does and why, (2) apply it, (3) run the actual command/test myself against the
live environment, (4) only commit once I personally observed the expected result. I did not
commit any AI-generated claim, count, or script output without independently reproducing it —
including catching and correcting two factual errors from the AI itself (the duplicate-line
miscount in Finding/Use 4, and the false-positive port check in Use 3) through my own testing.