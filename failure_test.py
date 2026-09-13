#!/usr/bin/env python3
"""Failure and recovery test for the BARQ assessment stack.

Stops one backend container (app-01 by default), sends continuous traffic through
NGINX to prove the service stays available using the remaining healthy backend,
measures errors during the outage, restores the stopped backend, and verifies it
resumes serving requests. Includes cleanup (always restarts the backend, even on
failure or interruption).

Usage:
    python failure_test.py [--host localhost] [--port 8080]
                            [--target app-01] [--duration 15]

Exit codes:
    0 -> service remained available during failure AND target backend recovered
    1 -> service was unavailable during failure, or target backend failed to recover
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

PASS = "PASS"
FAIL = "FAIL"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def docker(*args):
    proc = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=30)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def probe(base_url, path="/instance", timeout=3):
    """Single request; returns (ok, status_code_or_None, instance_id_or_None)."""
    try:
        req = urllib.request.Request(base_url + path)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            try:
                body = json.loads(resp.read())
            except json.JSONDecodeError:
                body = {}
            return status < 500, status, body.get("instance_id")
    except urllib.error.HTTPError as exc:
        return exc.code < 500, exc.code, None
    except Exception:
        return False, None, None


def send_traffic(base_url, duration, interval=0.3, label=""):
    """Sends requests for `duration` seconds, returns a summary dict."""
    end = time.time() + duration
    total = 0
    ok_count = 0
    fail_count = 0
    instances_seen = set()
    while time.time() < end:
        ok, status, instance_id = probe(base_url)
        total += 1
        if ok:
            ok_count += 1
            if instance_id:
                instances_seen.add(instance_id)
        else:
            fail_count += 1
        time.sleep(interval)
    log(f"{label}: {total} requests sent, {ok_count} ok, {fail_count} failed, "
        f"instances observed: {sorted(instances_seen)}")
    return {"total": total, "ok": ok_count, "failed": fail_count, "instances": instances_seen}


def wait_for_container_state(container, want_running, timeout=20, interval=1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, out, _ = docker("inspect", "-f", "{{.State.Running}}", container)
        if code == 0:
            running = out.strip() == "true"
            if running == want_running:
                return True
        time.sleep(interval)
    return False


def main():
    parser = argparse.ArgumentParser(description="Run a backend failure/recovery test.")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--target", default="app-01", help="Container name to stop")
    parser.add_argument("--duration", type=int, default=15,
                         help="Seconds of traffic to send during each phase")
    args = parser.parse_args()

    base_url = f"http://{args.host}:{args.port}"
    other = "app-02" if args.target == "app-01" else "app-01"
    overall_ok = True

    try:
        # --- Phase 1: baseline traffic before failure ---
        log(f"=== Phase 1: baseline traffic (both backends up) ===")
        baseline = send_traffic(base_url, min(args.duration, 6), label="baseline")
        if baseline["failed"] > 0:
            log("WARNING: baseline already showed failures before inducing any fault; "
                "results below may be affected by a pre-existing issue.")

        # --- Phase 2: stop target backend ---
        log(f"=== Phase 2: stopping backend '{args.target}' ===")
        code, out, err = docker("stop", args.target)
        if code != 0:
            print(f"[{FAIL}] Failed to stop container '{args.target}': {err}")
            sys.exit(1)
        stopped = wait_for_container_state(args.target, want_running=False, timeout=15)
        print(f"[{PASS if stopped else FAIL}] Backend '{args.target}' stopped",
              "" if stopped else "(did not reach stopped state in time)")
        overall_ok = overall_ok and stopped

        # --- Phase 3: measure traffic during outage ---
        log(f"=== Phase 3: measuring traffic while '{args.target}' is down ===")
        during = send_traffic(base_url, args.duration, label="during-failure")
        availability_ok = during["total"] > 0 and (during["ok"] / during["total"]) >= 0.95
        only_other_served = during["instances"] <= {other}
        print(f"[{PASS if availability_ok else FAIL}] Service remained available during failure "
              f"({during['ok']}/{during['total']} requests succeeded, "
              f"{during['failed']} failed)")
        print(f"[{PASS if only_other_served else FAIL}] Only the surviving backend "
              f"('{other}') served traffic during the outage "
              f"(observed: {sorted(during['instances'])})")
        overall_ok = overall_ok and availability_ok and only_other_served

        # --- Phase 4: restore target backend ---
        log(f"=== Phase 4: restoring backend '{args.target}' ===")
        code, out, err = docker("start", args.target)
        if code != 0:
            print(f"[{FAIL}] Failed to start container '{args.target}': {err}")
            overall_ok = False
        else:
            started = wait_for_container_state(args.target, want_running=True, timeout=15)
            print(f"[{PASS if started else FAIL}] Backend '{args.target}' restarted",
                  "" if started else "(did not reach running state in time)")
            overall_ok = overall_ok and started

        # Wait for the container's healthcheck to pass before testing recovery
        log(f"Waiting for '{args.target}' healthcheck to pass...")
        healthy = False
        deadline = time.time() + 30
        while time.time() < deadline:
            code, out, _ = docker("inspect", "-f", "{{.State.Health.Status}}", args.target)
            if code == 0 and out.strip() == "healthy":
                healthy = True
                break
            time.sleep(2)
        print(f"[{PASS if healthy else FAIL}] Backend '{args.target}' reports healthy after restart")
        overall_ok = overall_ok and healthy

        # --- Phase 5: verify recovered backend serves requests ---
        log(f"=== Phase 5: verifying recovered backend serves requests ===")
        recovery = send_traffic(base_url, args.duration, label="post-recovery")
        target_recovered = args.target in recovery["instances"]
        print(f"[{PASS if target_recovered else FAIL}] Recovered backend '{args.target}' "
              f"served at least one request post-recovery "
              f"(instances observed: {sorted(recovery['instances'])})")
        overall_ok = overall_ok and target_recovered

    finally:
        # --- Cleanup: always ensure the target backend is running before exiting ---
        log("=== Cleanup: ensuring target backend is running ===")
        code, out, _ = docker("inspect", "-f", "{{.State.Running}}", args.target)
        if code == 0 and out.strip() != "true":
            log(f"Backend '{args.target}' is not running; starting it as cleanup.")
            docker("start", args.target)
            wait_for_container_state(args.target, want_running=True, timeout=15)
        else:
            log(f"Backend '{args.target}' already running; no cleanup action needed.")

    print("\n=== Summary ===")
    if overall_ok:
        print("Result: PASS - service stayed available during the failure and the "
              "recovered backend resumed serving requests.")
        sys.exit(0)
    else:
        print("Result: FAIL - one or more checks did not pass. See details above.")
        sys.exit(1)


if __name__ == "__main__":
    main()