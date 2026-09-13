"""
log_stats.py - Replacement for the jq commands in log_analysis.md, using only
the Python standard library (no jq installation required).

Usage:
    py -V:Astral/CPython3.14.7 log_stats.py
"""
import json
import statistics


def load_deduped(path, event_filter=None):
    """Load JSON lines, skipping malformed ones, keeping only the first
    occurrence of each request_id (de-duplication)."""
    seen = set()
    rows = []
    malformed = 0
    total = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            total += 1
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if event_filter and row.get("event") != event_filter:
                continue
            rid = row.get("request_id")
            if rid in seen:
                continue
            seen.add(rid)
            rows.append(row)
    return rows, total, malformed


def main():
    print("=" * 60)
    print("Q1: access.log line counts")
    print("=" * 60)
    access_rows, access_total, access_malformed = load_deduped("logs/access.log")
    print(f"total lines: {access_total}")
    print(f"malformed lines: {access_malformed}")
    print(f"distinct request_ids: {len(access_rows)}")
    print(f"duplicate lines removed: {access_total - access_malformed - len(access_rows)}")

    print()
    print("=" * 60)
    print("Q1: application.log line counts")
    print("=" * 60)
    app_rows, app_total, app_malformed = load_deduped("logs/application.log")
    print(f"total lines: {app_total}")
    print(f"malformed lines: {app_malformed}")
    print(f"distinct request_ids: {len(app_rows)}")
    print(f"duplicate lines removed: {app_total - app_malformed - len(app_rows)}")

    print()
    print("=" * 60)
    print("Q2/Q3: distinct client requests + status counts (access.log)")
    print("=" * 60)
    status_counts = {}
    for row in access_rows:
        status = row.get("status")
        status_counts[status] = status_counts.get(status, 0) + 1
    print(f"distinct client requests: {len(access_rows)}")
    for status, count in sorted(status_counts.items()):
        print(f"  status {status}: {count}")
    error_count = sum(c for s, c in status_counts.items() if s and s >= 400)
    print(f"error rate: {error_count}/{len(access_rows)} = {error_count/len(access_rows):.4%}")

    print()
    print("=" * 60)
    print("Q5: median / p95 client latency (application.log, http_request events)")
    print("=" * 60)
    http_rows, _, _ = load_deduped("logs/application.log", event_filter="http_request")
    durations = sorted(r["duration_ms"] for r in http_rows if "duration_ms" in r)
    n = len(durations)
    median = statistics.median(durations)
    p95_index = int(round(0.95 * (n - 1)))
    p95 = durations[p95_index]
    print(f"n={n} median={median}ms p95={p95}ms (nearest-rank method)")

    print()
    print("=" * 60)
    print("Q6: retried-upstream requests (access.log, comma in upstream_status)")
    print("=" * 60)
    retried = [r for r in access_rows if isinstance(r.get("upstream_status"), str) and "," in r["upstream_status"]]
    retried_success = [r for r in retried if r["upstream_status"].strip().endswith("200")]
    print(f"requests that retried upstream: {len(retried)}")
    print(f"of those, succeeded (ended in 200): {len(retried_success)}")


if __name__ == "__main__":
    main()
