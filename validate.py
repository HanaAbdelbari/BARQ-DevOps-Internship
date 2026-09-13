#!/usr/bin/env python3
"""Environment validation for the BARQ assessment stack.

Checks, with bounded waits, PASS/FAIL reporting, and non-zero exit on failure:
  1. Public access to NGINX on the published host port.
  2. All required endpoints respond correctly (/, /health, /ready, /instance,
     /records GET+POST, /counter).
  3. Both backends (app-01, app-02) are reachable through NGINX.
  4. PostgreSQL and Redis readiness (via /ready).
  5. Network isolation: PostgreSQL/Redis ports are NOT published on the host.
  6. Network isolation: NGINX cannot directly reach PostgreSQL/Redis
     (backend network should be unreachable from the frontend-only nginx container).

Usage:
    python validate.py [--host localhost] [--port 8080] [--timeout 30]

Exit codes:
    0 -> all checks passed
    1 -> one or more checks failed
"""
import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

PASS = "PASS"
FAIL = "FAIL"

results = []


def record(name, ok, detail=""):
    status = PASS if ok else FAIL
    results.append((name, status, detail))
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))
    return ok


def http_get(url, timeout=3, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = None
        return resp.status, parsed, dict(resp.headers)


def wait_for(check_fn, timeout, interval=1, description=""):
    """Bounded wait: retries check_fn until it returns truthy or timeout expires."""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            result = check_fn()
            if result:
                return result
        except Exception as exc:
            last_err = exc
        time.sleep(interval)
    if last_err:
        print(f"  (bounded wait for '{description}' timed out after {timeout}s; last error: {last_err})")
    else:
        print(f"  (bounded wait for '{description}' timed out after {timeout}s)")
    return None


def check_public_access(base_url, timeout):
    def probe():
        status, _, _ = http_get(base_url + "/", timeout=3)
        return status == 200

    ok = wait_for(probe, timeout, description="public access on " + base_url)
    return record("Public access to NGINX", bool(ok), base_url + "/")


def check_endpoint(name, url, method="GET", data=None, expect_status=200, expect_keys=None):
    try:
        headers = {"Content-Type": "application/json"} if data else {}
        body = json.dumps(data).encode() if data else None
        status, parsed, resp_headers = http_get(url, timeout=5, method=method, data=body, headers=headers)
        ok = status == expect_status
        if ok and expect_keys and isinstance(parsed, dict):
            ok = all(k in parsed for k in expect_keys)
        detail = f"HTTP {status}"
        return record(name, ok, detail), parsed, resp_headers
    except urllib.error.HTTPError as exc:
        ok = exc.code == expect_status
        return record(name, ok, f"HTTP {exc.code}"), None, None
    except Exception as exc:
        return record(name, False, f"error: {exc}"), None, None


def check_both_backends(base_url, attempts=10):
    seen = set()
    for _ in range(attempts):
        try:
            _, parsed, headers = http_get(base_url + "/instance", timeout=3)
            if parsed and "instance_id" in parsed:
                seen.add(parsed["instance_id"])
            elif headers and "X-Instance-Id" in headers:
                seen.add(headers["X-Instance-Id"])
        except Exception:
            pass
        time.sleep(0.2)
    ok = {"app-01", "app-02"}.issubset(seen)
    return record("Both backends (app-01 and app-02) served requests", ok, f"observed: {sorted(seen)}")


def check_readiness(base_url):
    try:
        status, parsed, _ = http_get(base_url + "/ready", timeout=5)
        deps = (parsed or {}).get("dependencies", {})
        pg_ready = deps.get("postgres") == "ready"
        redis_ready = deps.get("redis") == "ready"
        record("PostgreSQL readiness (via /ready)", pg_ready, f"postgres={deps.get('postgres')}")
        record("Redis readiness (via /ready)", redis_ready, f"redis={deps.get('redis')}")
        record("/ready overall status is ready", status == 200 and pg_ready and redis_ready, f"HTTP {status}")
        return pg_ready and redis_ready
    except Exception as exc:
        record("PostgreSQL readiness (via /ready)", False, f"error: {exc}")
        record("Redis readiness (via /ready)", False, f"error: {exc}")
        return False


def check_prohibited_ports():
    """Verify PostgreSQL and Redis ports are NOT published on the host."""
    prohibited = {"postgres": 15432, "redis": 16379, "postgres-default": 5432, "redis-default": 6379}
    all_closed = True
    for name, port in prohibited.items():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        try:
            result = sock.connect_ex(("127.0.0.1", port))
            is_closed = result != 0
            all_closed = all_closed and is_closed
            record(f"Host port {port} ({name}) is NOT published", is_closed,
                   "closed" if is_closed else "OPEN - violates isolation requirement")
        finally:
            sock.close()
    return all_closed


def check_network_isolation():
    """Verify NGINX container cannot directly reach postgres/redis (not on backend network)."""
    checks_ok = True
    for target, port in [("postgres", 5432), ("redis", 6379)]:
        try:
            proc = subprocess.run(
                ["docker", "exec", "nginx", "sh", "-c",
                 f"timeout 2 nc -z {target} {port} 2>&1; echo EXIT:$?"],
                capture_output=True, text=True, timeout=10
            )
            output = proc.stdout + proc.stderr
            unreachable = "EXIT:0" not in output
            checks_ok = checks_ok and unreachable
            record(f"nginx cannot directly reach {target}:{port}", unreachable,
                   output.strip().splitlines()[-1] if output.strip() else "no output")
        except FileNotFoundError:
            record(f"nginx cannot directly reach {target}:{port}", False,
                   "docker CLI not found - cannot verify network isolation")
            checks_ok = False
        except Exception as exc:
            record(f"nginx cannot directly reach {target}:{port}", False, f"error: {exc}")
            checks_ok = False
    return checks_ok


def main():
    parser = argparse.ArgumentParser(description="Validate the BARQ assessment environment.")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--timeout", type=int, default=30, help="Bounded wait timeout in seconds")
    args = parser.parse_args()

    base_url = f"http://{args.host}:{args.port}"
    print(f"=== Validating BARQ environment at {base_url} ===\n")

    # 1. Public access (bounded wait for readiness after startup)
    check_public_access(base_url, args.timeout)

    # 2. Individual required endpoints
    check_endpoint("GET / responds", base_url + "/", expect_keys=["message", "service"])
    check_endpoint("GET /health responds", base_url + "/health", expect_keys=["status"])
    check_endpoint("GET /instance responds", base_url + "/instance", expect_keys=["instance_id"])
    check_endpoint("GET /records responds", base_url + "/records", expect_keys=["records"])
    _, created, _ = check_endpoint(
        "POST /records creates a record", base_url + "/records", method="POST",
        data={"title": "validate.py smoke-test record"}, expect_status=201, expect_keys=["record"]
    )
    check_endpoint("GET /counter responds", base_url + "/counter", expect_keys=["counter"])

    # 3. Readiness (postgres + redis)
    check_readiness(base_url)

    # 4. Both backends served traffic (avoid double counting by checking distinct instance_ids observed)
    check_both_backends(base_url)

    # 5. Prohibited host ports
    check_prohibited_ports()

    # 6. Network isolation (nginx -> backend network should be blocked)
    check_network_isolation()

    # Summary
    print("\n=== Summary ===")
    passed = sum(1 for _, status, _ in results if status == PASS)
    failed = sum(1 for _, status, _ in results if status == FAIL)
    total = len(results)
    print(f"{passed}/{total} checks passed, {failed} failed")

    if failed > 0:
        print("\nFailed checks:")
        for name, status, detail in results:
            if status == FAIL:
                print(f"  - {name}: {detail}")
        sys.exit(1)

    print("\nAll checks PASSED.")
    sys.exit(0)


if __name__ == "__main__":
    main()