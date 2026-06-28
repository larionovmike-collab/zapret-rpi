#!/usr/bin/python3
"""Root-only asynchronous zapret2 strategy selection built around blockcheck2."""

import datetime as dt
import concurrent.futures
import ipaddress
import json
import os
import re
import secrets
import selectors
import signal
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

STATE_DIR = Path("/var/lib/zapret-rpi/autotune")
JOBS_DIR = STATE_DIR / "jobs"
CURRENT = STATE_DIR / "current.json"
MONITOR_CONFIG = STATE_DIR / "monitor.json"
PROFILE_DIR = Path("/etc/zapret-rpi/zapret2/profiles")
AUTOTUNE_PROFILE = "autotune"
BLOCKCHECK = Path("/opt/zapret2/blockcheck2.sh")
PROFILE_TOOL = Path("/usr/local/sbin/zapret-rpi-profile")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:/[^\s]*)?$", re.I)
SAFE_ARG_RE = re.compile(r"^[A-Za-z0-9_./:@,+%<>=~-]+$")
SUMMARY_RE = re.compile(r"^(?:curl_test_)?(http|https_tls12|https_tls13|http3) ipv4\s+(\S+)\s+:\s+nfqws2\s+(.+)$")
TEST_LINE_RE = re.compile(r"^-\s+(?:curl_test_)?(http|https_tls12|https_tls13|http3) ipv4\s+(\S+)\s+:\s+nfqws2\s+(.+)$")
PROTOCOLS = {"http": "http", "https_tls12": "https", "https_tls13": "https", "http3": "quic"}
MAX_RUNTIME = {"quick": 20 * 60, "standard": 45 * 60, "force": 90 * 60}
QUICK_TESTS = {"http": 6, "https": 9, "quic": 5}
MONITOR_MIN_INTERVAL = 15
MONITOR_MAX_INTERVAL = 24 * 60
MONITOR_COOLDOWN = 6 * 60 * 60
MONITOR_NETNS = "zapret-rpi-monitor"
MONITOR_HOST_IF = "zapret-mon"
MONITOR_PEER_IF = "zapret-peer"
MONITOR_HOST_CIDR = "192.0.2.1/30"
MONITOR_PEER_CIDR = "192.0.2.2/30"
MONITOR_PEER_IP = "192.0.2.2"
MONITOR_MARK_COMMENT = "zapret-rpi-monitor"


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def save(job: dict) -> None:
    atomic_json(JOBS_DIR / f"{job['id']}.json", job)
    atomic_json(CURRENT, job)


def load_job(job_id: str | None = None) -> dict:
    if job_id is not None and not re.fullmatch(r"[0-9]{8}T[0-9]{6}-[a-f0-9]{6}", job_id):
        raise ValueError("invalid run id")
    path = JOBS_DIR / f"{job_id}.json" if job_id else CURRENT
    if not path.is_file():
        raise ValueError("autotune run not found")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_request(body: dict) -> dict:
    domains = body.get("domains", ["rutracker.org"])
    protocols = body.get("protocols", ["http", "https", "quic"])
    repeats = body.get("repeats", 2)
    scan_level = body.get("scan_level", "quick")
    test_set = body.get("test_set", "auto")
    if not isinstance(domains, list) or not 1 <= len(domains) <= 30 or any(not isinstance(x, str) or not DOMAIN_RE.fullmatch(x) for x in domains):
        raise ValueError("domains must contain 1-30 valid domain names or domain paths")
    if not isinstance(protocols, list) or not protocols or not set(protocols) <= {"http", "https", "quic"}:
        raise ValueError("protocols must contain http, https or quic")
    if not isinstance(repeats, int) or not 1 <= repeats <= 5:
        raise ValueError("repeats must be between 1 and 5")
    if scan_level not in {"quick", "standard", "force"}:
        raise ValueError("invalid scan level")
    if not isinstance(test_set, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", test_set):
        raise ValueError("invalid test set")
    if test_set == "auto":
        test_set = "zapret-rpi-quick" if scan_level == "quick" else "standard"
    if not (Path("/opt/zapret2/blockcheck2.d") / test_set).is_dir():
        raise ValueError("unknown blockcheck2 test set")
    return {"domains": domains, "protocols": sorted(set(protocols)), "repeats": repeats, "scan_level": scan_level, "test_set": test_set}


def enqueue(body: dict, origin: str = "manual", auto_apply: bool = False) -> dict:
    request = validate_request(body)
    if CURRENT.exists():
        current = load_job()
        if current.get("status") in {"queued", "running"}:
            raise ValueError("an autotune run is already in progress")
    job_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
    expected_tests = None
    if request["test_set"] == "zapret-rpi-quick":
        expected_tests = len(request["domains"]) * sum(QUICK_TESTS[p] for p in request["protocols"])
    job = {
        "id": job_id, "status": "queued", "phase": "queued", "progress": 0,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "request": request,
        "tested": 0, "expected_tests": expected_tests, "successful": 0,
        "candidates": [], "current_test": None, "best_profile": None, "score": None,
        "origin": origin, "auto_apply": auto_apply,
    }
    save(job)
    return job


def default_monitor() -> dict:
    return {
        "enabled": False,
        "interval_minutes": 60,
        "request": {
            "domains": ["rutracker.org"],
            "protocols": ["http", "https", "quic"],
            "repeats": 2,
            "scan_level": "quick",
            "test_set": "auto",
        },
        "baseline": None,
        "last_results": None,
        "status": "disabled",
    }


def load_monitor() -> dict:
    if not MONITOR_CONFIG.is_file():
        return default_monitor()
    try:
        value = json.loads(MONITOR_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("cannot read availability monitor configuration")
    return {**default_monitor(), **value}


def monitor_public(config: dict | None = None) -> dict:
    config = config or load_monitor()
    result = dict(config)
    baseline = result.get("baseline")
    if isinstance(baseline, dict):
        result["baseline_summary"] = {
            "available": sum(1 for item in baseline.values() if item.get("available")),
            "total": len(baseline),
        }
    else:
        result["baseline_summary"] = None
    result.pop("baseline", None)
    result.pop("last_results", None)
    return result


def monitor_set(body: dict) -> dict:
    enabled = body.get("enabled")
    interval = body.get("interval_minutes")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be boolean")
    if not isinstance(interval, int) or not MONITOR_MIN_INTERVAL <= interval <= MONITOR_MAX_INTERVAL:
        raise ValueError(f"interval must be between {MONITOR_MIN_INTERVAL} and {MONITOR_MAX_INTERVAL} minutes")
    request = validate_request({
        "domains": body.get("domains"),
        "protocols": body.get("protocols", ["http", "https", "quic"]),
        "repeats": body.get("repeats", 2),
        "scan_level": body.get("scan_level", "quick"),
        "test_set": body.get("test_set", "auto"),
    })
    current = load_monitor()
    same_request = current.get("request") == request
    config = {
        **current,
        "enabled": enabled,
        "interval_minutes": interval,
        "request": request,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": (
            ("healthy" if same_request and current.get("baseline") is not None else "pending")
            if enabled else "disabled"
        ),
    }
    if not same_request:
        config["baseline"] = None
        config["last_results"] = None
        config.pop("last_check", None)
    atomic_json(MONITOR_CONFIG, config)
    return monitor_public(config)


def domain_url(domain: str) -> str:
    return "https://" + domain


def cleanup_monitor_network() -> None:
    listed = subprocess.run(
        ["nft", "-a", "list", "chain", "inet", "zapret2", "forward_lan_filter"],
        text=True, capture_output=True, check=False,
    )
    for line in listed.stdout.splitlines():
        if MONITOR_MARK_COMMENT not in line:
            continue
        match = re.search(r"# handle (\d+)", line)
        if match:
            subprocess.run([
                "nft", "delete", "rule", "inet", "zapret2", "forward_lan_filter",
                "handle", match.group(1),
            ], check=False)
    subprocess.run(["ip", "link", "del", MONITOR_HOST_IF], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["ip", "netns", "del", MONITOR_NETNS], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def prepare_monitor_network() -> None:
    cleanup_monitor_network()
    subprocess.run(["ip", "netns", "add", MONITOR_NETNS], check=True)
    try:
        subprocess.run([
            "ip", "link", "add", MONITOR_HOST_IF, "type", "veth",
            "peer", "name", MONITOR_PEER_IF,
        ], check=True)
        subprocess.run(["ip", "link", "set", MONITOR_PEER_IF, "netns", MONITOR_NETNS], check=True)
        subprocess.run(["ip", "address", "add", MONITOR_HOST_CIDR, "dev", MONITOR_HOST_IF], check=True)
        subprocess.run(["ip", "link", "set", MONITOR_HOST_IF, "up"], check=True)
        subprocess.run(["ip", "netns", "exec", MONITOR_NETNS, "ip", "link", "set", "lo", "up"], check=True)
        subprocess.run([
            "ip", "netns", "exec", MONITOR_NETNS, "ip", "address", "add",
            MONITOR_PEER_CIDR, "dev", MONITOR_PEER_IF,
        ], check=True)
        subprocess.run([
            "ip", "netns", "exec", MONITOR_NETNS, "ip", "link", "set",
            MONITOR_PEER_IF, "up",
        ], check=True)
        subprocess.run([
            "ip", "netns", "exec", MONITOR_NETNS, "ip", "route", "add",
            "default", "via", "192.0.2.1",
        ], check=True)
        subprocess.run([
            "nft", "insert", "rule", "inet", "zapret2", "forward_lan_filter",
            "iifname", MONITOR_HOST_IF, "ip", "saddr", MONITOR_PEER_IP,
            "meta", "mark", "set", "meta", "mark", "or", "0x10000000",
            "comment", MONITOR_MARK_COMMENT,
        ], check=True)
    except Exception:
        cleanup_monitor_network()
        raise


def public_ipv4_addresses(host: str) -> list[str]:
    try:
        values = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    addresses = []
    for value in values:
        address = value[4][0]
        if ipaddress.ip_address(address).is_global and address not in addresses:
            addresses.append(address)
    return addresses


def probe_domain(domain: str) -> dict:
    started = time.monotonic()
    host = domain.split("/", 1)[0]
    addresses = public_ipv4_addresses(host)
    code = 0
    for address in addresses[:3]:
        result = subprocess.run([
            "ip", "netns", "exec", MONITOR_NETNS,
            "curl", "--ipv4", "--silent", "--show-error",
            "--connect-timeout", "5", "--max-time", "12",
            "--range", "0-0", "--output", "/dev/null", "--write-out", "%{http_code}",
            "--resolve", f"{host}:443:{address}", domain_url(domain),
        ], text=True, capture_output=True, check=False)
        code_text = result.stdout.strip()[-3:]
        code = int(code_text) if code_text.isdigit() else 0
        if 100 <= code <= 599:
            break
    return {
        "available": 100 <= code <= 599,
        "http_code": code,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


def probe_domains(domains: list[str]) -> dict:
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(domains))) as pool:
        values = pool.map(probe_domain, domains)
    return dict(zip(domains, values))


def probe_domains_as_forwarded_client(domains: list[str]) -> dict:
    prepare_monitor_network()
    try:
        return probe_domains(domains)
    finally:
        cleanup_monitor_network()


def find_degraded_domains(baseline: dict, current: dict, domains: list[str]) -> list[str]:
    return [
        domain for domain in domains
        if baseline.get(domain, {}).get("available") and not current.get(domain, {}).get("available")
    ]


def iso_age_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return (dt.datetime.now(dt.timezone.utc) - parsed).total_seconds()


def active_profile() -> str:
    result = subprocess.run([str(PROFILE_TOOL), "get"], text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def capture_monitor_baseline(run_id: str | None = None) -> None:
    config = load_monitor()
    if not config.get("enabled"):
        return
    results = probe_domains_as_forwarded_client(config["request"]["domains"])
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    config.update(
        baseline=results,
        last_results=results,
        last_check=now,
        baseline_at=now,
        status="healthy",
        degraded_domains=[],
    )
    if run_id:
        config["last_applied_run"] = run_id
    config.pop("error", None)
    atomic_json(MONITOR_CONFIG, config)


def invalidate_monitor_baseline(job: dict) -> None:
    config = load_monitor()
    if not config.get("enabled"):
        return
    config.update(baseline=None, status="pending", degraded_domains=[])
    config.pop("error", None)
    atomic_json(MONITOR_CONFIG, config)


def mark_monitor_error(message: str, run_id: str | None = None) -> None:
    config = load_monitor()
    if not config.get("enabled"):
        return
    config.update(status="error", error=message[:500])
    if run_id:
        config["last_triggered_run"] = run_id
    atomic_json(MONITOR_CONFIG, config)


def monitor_check() -> dict:
    config = load_monitor()
    if not config.get("enabled"):
        return monitor_public(config)
    age = iso_age_seconds(config.get("last_check"))
    if config.get("baseline") is not None and age is not None and age < config["interval_minutes"] * 60:
        return monitor_public(config)
    if active_profile() != AUTOTUNE_PROFILE:
        config.update(status="paused", error="Autotune profile is not active")
        atomic_json(MONITOR_CONFIG, config)
        return monitor_public(config)
    if subprocess.run(["systemctl", "is-active", "--quiet", "zapret2.service"], check=False).returncode:
        config.update(status="paused", error="zapret2 service is not active")
        atomic_json(MONITOR_CONFIG, config)
        return monitor_public(config)
    if subprocess.run(["systemctl", "is-active", "--quiet", "zapret-rpi-autotune.service"], check=False).returncode == 0:
        config.update(status="waiting", error="autotune is already running")
        atomic_json(MONITOR_CONFIG, config)
        return monitor_public(config)

    domains = config["request"]["domains"]
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    config["status"] = "checking"
    atomic_json(MONITOR_CONFIG, config)
    try:
        prepare_monitor_network()
        results = probe_domains(domains)
        baseline = config.get("baseline")
        if baseline is None:
            config.update(
                baseline=results, last_results=results, last_check=now,
                baseline_at=now, status="healthy", degraded_domains=[],
            )
            config.pop("error", None)
            atomic_json(MONITOR_CONFIG, config)
            return monitor_public(config)

        degraded = find_degraded_domains(baseline, results, domains)
        if degraded:
            time.sleep(2)
            confirmed = probe_domains(degraded)
            degraded = [domain for domain in degraded if not confirmed[domain]["available"]]
            results.update(confirmed)
    except (OSError, subprocess.SubprocessError) as error:
        config.update(status="error", error=f"cannot prepare forwarded availability probe: {error}")
        atomic_json(MONITOR_CONFIG, config)
        return monitor_public(config)
    finally:
        cleanup_monitor_network()
    config.update(last_results=results, last_check=now, degraded_domains=degraded)
    if not degraded:
        config.update(status="healthy")
        config.pop("error", None)
        atomic_json(MONITOR_CONFIG, config)
        return monitor_public(config)

    cooldown_age = iso_age_seconds(config.get("last_triggered_at"))
    if cooldown_age is not None and cooldown_age < MONITOR_COOLDOWN:
        config.update(status="cooldown", error="availability is degraded; automatic retune is in cooldown")
        atomic_json(MONITOR_CONFIG, config)
        return monitor_public(config)

    try:
        job = enqueue(config["request"], origin="monitor", auto_apply=True)
    except ValueError as error:
        config.update(status="waiting", error=str(error)[:500])
        atomic_json(MONITOR_CONFIG, config)
        return monitor_public(config)
    config.update(
        status="retuning",
        last_triggered_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        last_triggered_run=job["id"],
    )
    config.pop("error", None)
    atomic_json(MONITOR_CONFIG, config)
    started = subprocess.run(["systemctl", "start", "--no-block", "zapret-rpi-autotune.service"], check=False)
    if started.returncode:
        fail_queued("availability monitor could not start autotune service")
        config.update(status="error", error="cannot start automatic retune")
        atomic_json(MONITOR_CONFIG, config)
    return monitor_public(config)


def clean_strategy(raw: str) -> str | None:
    try:
        tokens = shlex.split(raw)
    except ValueError:
        return None
    ignored_prefixes = ("--qnum", "--daemon", "--pidfile", "--uid", "--gid", "--wf-")
    tokens = [token for token in tokens if not token.startswith(ignored_prefixes)]
    if not tokens or any(not token.startswith("--") or not SAFE_ARG_RE.fullmatch(token) for token in tokens):
        return None
    return " ".join(tokens)


def parse_results(lines: list[str], request: dict) -> list[dict]:
    observations: dict[tuple[str, str], set[str]] = {}
    for line in lines:
        match = SUMMARY_RE.match(line.strip())
        if not match:
            continue
        protocol = PROTOCOLS[match.group(1)]
        if protocol not in request["protocols"]:
            continue
        strategy = clean_strategy(match.group(3))
        if strategy:
            observations.setdefault((protocol, strategy), set()).add(match.group(2))
    selected = []
    for protocol in request["protocols"]:
        candidates = []
        for (candidate_protocol, strategy), domains in observations.items():
            if candidate_protocol != protocol:
                continue
            complexity = len(strategy.split()) + strategy.count("fake") * 2 + strategy.count("repeats=")
            coverage = len(domains) / len(request["domains"])
            candidates.append((coverage, -complexity, strategy, sorted(domains)))
        if candidates:
            coverage, negative_complexity, strategy, domains = max(candidates)
            selected.append({"protocol": protocol, "strategy": strategy, "domains": domains, "coverage": round(coverage, 3), "score": round(coverage * 100 + negative_complexity * 0.35, 1)})
    return selected


def candidate_rows(stats: dict, request: dict, limit: int = 30) -> list[dict]:
    rows = []
    domain_count = len(request["domains"])
    for item in stats.values():
        attempts = item["attempts"]
        successes = item["successes"]
        successful_domains = sorted(domain for domain, values in item["domains"].items() if values["successes"])
        suitability = round(successes / attempts * 100) if attempts else 0
        coverage = len(successful_domains) / domain_count
        rows.append({
            "protocol": item["protocol"],
            "strategy": item["strategy"],
            "attempts": attempts,
            "successes": successes,
            "suitability": suitability,
            "coverage": round(coverage, 3),
            "domains": successful_domains,
            "last_seen": item["last_seen"],
        })
    rows.sort(key=lambda row: (row["successes"] > 0, row["coverage"], row["suitability"], -len(row["strategy"]), row["last_seen"]), reverse=True)
    return rows[:limit]


def select_from_candidates(stats: dict, request: dict) -> list[dict]:
    rows = candidate_rows(stats, request, limit=len(stats))
    selected = []
    for protocol in request["protocols"]:
        choices = [row for row in rows if row["protocol"] == protocol and row["successes"]]
        if not choices:
            continue
        best = max(choices, key=lambda row: (row["coverage"], row["suitability"], -len(row["strategy"])))
        selected.append({
            "protocol": protocol,
            "strategy": best["strategy"],
            "domains": best["domains"],
            "coverage": best["coverage"],
            "score": round(best["coverage"] * 70 + best["suitability"] * 0.3, 1),
            "suitability": best["suitability"],
        })
    return selected


def parse_candidate_attempts(lines: list[str], request: dict) -> dict:
    stats = {}
    current = None
    sequence = 0
    for raw_line in lines:
        line = raw_line.rstrip("\n")
        match = TEST_LINE_RE.match(line)
        if match:
            protocol = PROTOCOLS[match.group(1)]
            strategy = clean_strategy(match.group(3))
            if protocol not in request["protocols"] or strategy is None:
                current = None
                continue
            sequence += 1
            current = stats.setdefault((protocol, strategy), {
                "protocol": protocol, "strategy": strategy, "attempts": 0,
                "successes": 0, "domains": {}, "last_seen": sequence,
            })
            current["last_seen"] = sequence
            current["domain"] = match.group(2)
            current["attempt_marker_seen"] = False
            continue
        if current is None:
            continue
        domain = current["domain"]
        if line.startswith("[attempt "):
            domain_stats = current["domains"].setdefault(domain, {"attempts": 0, "successes": 0})
            current["attempts"] += 1
            domain_stats["attempts"] += 1
            current["attempt_marker_seen"] = True
            if "AVAILABLE" in line:
                current["successes"] += 1
                domain_stats["successes"] += 1
            continue
        if line.startswith("UNAVAILABLE") or line == "AVAILABLE":
            if not current["attempt_marker_seen"]:
                domain_stats = current["domains"].setdefault(domain, {"attempts": 0, "successes": 0})
                current["attempts"] += 1
                domain_stats["attempts"] += 1
                if line == "AVAILABLE":
                    current["successes"] += 1
                    domain_stats["successes"] += 1
            if line.startswith("UNAVAILABLE"):
                current = None
            continue
        if line.startswith("!!!!!"):
            current = None
    return stats


def explain_empty_result(lines: list[str], tested: int = 0) -> str:
    if tested:
        return f"Проверено стратегий: {tested}. Ни одна не дала успешного ответа для выбранных целей."
    joined = "\n".join(lines).lower()
    if "working without bypass" in joined or "available" in joined:
        return "blockcheck2 не подобрал стратегию, потому что проверяемые цели доступны без DPI-bypass."
    return "blockcheck2 завершился без ошибки, но не нашёл применимую стратегию для выбранных целей."


def render_profile(job: dict, selected: list[dict]) -> tuple[str, str]:
    by_protocol = {item["protocol"]: item for item in selected}
    fragments = []
    if "http" in by_protocol:
        fragments.append(f"--filter-tcp=80 --filter-l7=http <HOSTLIST> {by_protocol['http']['strategy']}")
    if "https" in by_protocol:
        fragments.append(f"--filter-tcp=443 --filter-l7=tls <HOSTLIST> {by_protocol['https']['strategy']}")
    if "quic" in by_protocol:
        fragments.append(f"--filter-udp=443 --filter-l7=quic <HOSTLIST_NOAUTO> {by_protocol['quic']['strategy']}")
    profile_name = AUTOTUNE_PROFILE
    lines = ['PROFILE_DESCRIPTION="Автоматически подобранные стратегии"']
    tcp_ports = ",".join(port for key, port in (("http", "80"), ("https", "443")) if key in by_protocol)
    lines.extend([f"NFQWS2_PORTS_TCP={tcp_ports}", f"NFQWS2_TCP_PKT_OUT={'20' if tcp_ports else '0'}"])
    if "quic" not in by_protocol:
        lines.extend(["NFQWS2_PORTS_UDP=", "NFQWS2_UDP_PKT_OUT=0"])
    lines.append('NFQWS2_OPT="\n' + " --new\n".join(fragments) + '\n"')
    return profile_name, "\n".join(lines) + "\n"


def run_job() -> None:
    job = load_job()
    if job["status"] != "queued":
        raise ValueError("no queued autotune run")
    request = job["request"]
    log_path = JOBS_DIR / f"{job['id']}.log"
    was_active = subprocess.run(["systemctl", "is-active", "--quiet", "zapret2.service"], check=False).returncode == 0
    job.update(
        status="running", phase="preparing", progress=0,
        started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        limit_seconds=MAX_RUNTIME[request["scan_level"]],
        zapret_was_active=was_active,
    )
    save(job)
    lines: list[str] = []
    try:
        if was_active:
            subprocess.run(["systemctl", "stop", "zapret2.service"], check=True)
        env = os.environ.copy()
        protocols = set(request["protocols"])
        env.update({
            "BATCH": "1", "TEST": request["test_set"], "DOMAINS": " ".join(request["domains"]),
            "IPVS": "4", "REPEATS": str(request["repeats"]), "SCANLEVEL": request["scan_level"],
            "PARALLEL": "0", "ENABLE_HTTP": str(int("http" in protocols)),
            "ENABLE_HTTPS_TLS12": str(int("https" in protocols)), "ENABLE_HTTPS_TLS13": "0",
            "ENABLE_HTTP3": str(int("quic" in protocols)), "SKIP_DNSCHECK": "1",
        })
        job.update(phase="testing", progress=0)
        save(job)
        candidate_stats: dict[tuple[str, str], dict] = {}
        current_candidate = None
        sequence = 0
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [str(BLOCKCHECK)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=env, bufsize=1, start_new_session=True,
            )
            assert process.stdout is not None
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + MAX_RUNTIME[request["scan_level"]]

            def consume(line):
                nonlocal current_candidate, sequence
                line = line.rstrip("\n")
                lines.append(line); log.write(line + "\n"); log.flush()
                match = TEST_LINE_RE.match(line)
                if match:
                    protocol = PROTOCOLS[match.group(1)]
                    strategy = clean_strategy(match.group(3))
                    if protocol not in request["protocols"] or strategy is None:
                        return
                    sequence += 1
                    key = (protocol, strategy)
                    current_candidate = candidate_stats.setdefault(key, {
                        "protocol": protocol, "strategy": strategy, "attempts": 0,
                        "successes": 0, "domains": {}, "last_seen": sequence,
                    })
                    current_candidate["last_seen"] = sequence
                    current_candidate["domain"] = match.group(2)
                    current_candidate["attempt_marker_seen"] = False
                    job["tested"] += 1
                    expected = job.get("expected_tests")
                    job["progress"] = min(94, round(job["tested"] / expected * 94)) if expected else None
                    job["current_test"] = {
                        "protocol": protocol, "domain": match.group(2),
                        "strategy": strategy, "number": job["tested"],
                    }
                    job["candidates"] = candidate_rows(candidate_stats, request)
                    job["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
                    save(job)
                    return
                if current_candidate is not None and line.startswith("[attempt "):
                    domain = current_candidate["domain"]
                    domain_stats = current_candidate["domains"].setdefault(domain, {"attempts": 0, "successes": 0})
                    current_candidate["attempts"] += 1
                    domain_stats["attempts"] += 1
                    current_candidate["attempt_marker_seen"] = True
                    if "AVAILABLE" in line:
                        current_candidate["successes"] += 1
                        domain_stats["successes"] += 1
                        job["successful"] += 1
                    return
                if current_candidate is not None and (line.startswith("UNAVAILABLE") or line == "AVAILABLE"):
                    domain = current_candidate["domain"]
                    domain_stats = current_candidate["domains"].setdefault(domain, {"attempts": 0, "successes": 0})
                    if not current_candidate["attempt_marker_seen"]:
                        current_candidate["attempts"] += 1
                        domain_stats["attempts"] += 1
                        if line == "AVAILABLE":
                            current_candidate["successes"] += 1
                            domain_stats["successes"] += 1
                            job["successful"] += 1
                    if line.startswith("UNAVAILABLE"):
                        current_candidate = None
                    return
                if current_candidate is not None and line.startswith("!!!!!"):
                    current_candidate = None
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                    raise RuntimeError(f"blockcheck2 exceeded the {MAX_RUNTIME[request['scan_level']] // 60}-minute limit")
                for key, _ in selector.select(timeout=1):
                    line = key.fileobj.readline()
                    if line:
                        consume(line)
            for line in process.stdout:
                consume(line)
            return_code = process.wait()
        if return_code:
            raise RuntimeError(f"blockcheck2 exited with status {return_code}")
        candidate_stats = parse_candidate_attempts(lines, request)
        job["successful"] = sum(item["successes"] for item in candidate_stats.values())
        job.update(phase="evaluating", progress=96, current_test=None, candidates=candidate_rows(candidate_stats, request))
        save(job)
        selected = select_from_candidates(candidate_stats, request)
        if not selected:
            selected = parse_results(lines, request)
        if not selected:
            job.update(
                status="completed",
                phase="completed",
                progress=100,
                completed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                best_profile=None,
                score=0,
                results=[],
                log=str(log_path),
                note=explain_empty_result(lines, job["tested"]),
            )
            return
        score = round(sum(item["score"] for item in selected) / len(request["protocols"]), 1)
        job.update(status="completed", phase="completed", progress=100, completed_at=dt.datetime.now(dt.timezone.utc).isoformat(), best_profile=AUTOTUNE_PROFILE, score=score, results=selected, log=str(log_path))
    except Exception as error:
        job.update(status="failed", phase="failed", completed_at=dt.datetime.now(dt.timezone.utc).isoformat(), error=str(error)[:500])
    finally:
        if was_active:
            subprocess.run(["systemctl", "start", "zapret2.service"], check=False)
        save(job)
    if job.get("auto_apply"):
        if job.get("status") == "completed" and job.get("results"):
            try:
                apply(job["id"])
            except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
                job = load_job(job["id"])
                job["auto_apply_error"] = str(error)[:500]
                save(job)
                mark_monitor_error(f"automatic profile activation failed: {error}", job["id"])
            else:
                try:
                    time.sleep(2)
                    capture_monitor_baseline(job["id"])
                except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
                    mark_monitor_error(f"profile applied, but baseline capture failed: {error}", job["id"])
        else:
            message = job.get("error") or job.get("note") or "automatic retune found no applicable strategy"
            mark_monitor_error(message, job["id"])


def apply(job_id: str, selections: list[dict] | None = None) -> dict:
    job = load_job(job_id)
    if job.get("status") != "completed" or not job.get("results"):
        raise ValueError("autotune run has no applicable result")
    selected = job.get("results", [])
    if selections is not None:
        if not isinstance(selections, list) or not 1 <= len(selections) <= 3:
            raise ValueError("select between one and three strategies")
        available = {
            (item["protocol"], item["strategy"]): item
            for item in job.get("candidates", [])
            if item.get("successes", 0) > 0
        }
        protocols = set()
        selected = []
        for choice in selections:
            if not isinstance(choice, dict):
                raise ValueError("invalid strategy selection")
            key = (choice.get("protocol"), choice.get("strategy"))
            if key[0] in protocols or key not in available:
                raise ValueError("selected strategy is unavailable or duplicated")
            protocols.add(key[0])
            candidate = available[key]
            selected.append({
                "protocol": candidate["protocol"],
                "strategy": candidate["strategy"],
                "domains": candidate["domains"],
                "coverage": candidate["coverage"],
                "suitability": candidate["suitability"],
                "score": round(candidate["coverage"] * 70 + candidate["suitability"] * 0.3, 1),
            })
        job["results"] = selected
        job["score"] = round(sum(item["score"] for item in selected) / len(job["request"]["protocols"]), 1)
    profile_name, content = render_profile(job, selected)
    profile_path = PROFILE_DIR / f"{profile_name}.conf"
    staged_path = PROFILE_DIR / f".{profile_name}.conf.new"
    previous_content = profile_path.read_bytes() if profile_path.is_file() else None
    staged_path.write_text(content, encoding="utf-8")
    os.chmod(staged_path, 0o600)
    subprocess.run(["bash", "-n", "/opt/zapret2/config", str(staged_path)], check=True)
    os.replace(staged_path, profile_path)
    result = subprocess.run([str(PROFILE_TOOL), "set", profile_name], text=True, capture_output=True)
    if result.returncode:
        if previous_content is None:
            profile_path.unlink(missing_ok=True)
        else:
            restore_path = PROFILE_DIR / f".{profile_name}.conf.restore"
            restore_path.write_bytes(previous_content)
            os.chmod(restore_path, 0o600)
            os.replace(restore_path, profile_path)
            subprocess.run(["systemctl", "restart", "zapret2.service"], check=False)
        raise RuntimeError(result.stderr.strip() or "profile activation failed")
    for legacy_profile in PROFILE_DIR.glob("auto-*.conf"):
        legacy_profile.unlink(missing_ok=True)
    job["best_profile"] = profile_name
    job["applied_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    save(job)
    invalidate_monitor_baseline(job)
    return {"profile": profile_name, "state": "active"}


def repair(job_id: str) -> dict:
    job = load_job(job_id)
    log_path = JOBS_DIR / f"{job_id}.log"
    if not log_path.is_file():
        raise ValueError("autotune log not found")
    stats = parse_candidate_attempts(log_path.read_text(encoding="utf-8").splitlines(), job["request"])
    job["candidates"] = candidate_rows(stats, job["request"])
    job["successful"] = sum(item["successes"] for item in stats.values())
    available = {(item["protocol"], item["strategy"]): item for item in job["candidates"]}
    repaired_results = []
    for old in job.get("results", []):
        item = available.get((old["protocol"], old["strategy"]))
        if item:
            repaired_results.append({
                "protocol": item["protocol"], "strategy": item["strategy"],
                "domains": item["domains"], "coverage": item["coverage"],
                "suitability": item["suitability"],
                "score": round(item["coverage"] * 70 + item["suitability"] * 0.3, 1),
            })
    if repaired_results:
        job["results"] = repaired_results
        job["score"] = round(sum(item["score"] for item in repaired_results) / len(job["request"]["protocols"]), 1)
    save(job)
    return job


def recover() -> None:
    """ExecStopPost safety net for interruption before Python can execute finally."""
    try:
        job = load_job()
    except ValueError:
        return
    if job.get("zapret_was_active"):
        subprocess.run(["systemctl", "start", "zapret2.service"], check=False)
    if job.get("status") in {"queued", "running"}:
        job.update(status="failed", phase="failed", completed_at=dt.datetime.now(dt.timezone.utc).isoformat(), error="autotune service is not running; the previous run was interrupted")
        save(job)


def fail_queued(message: str) -> None:
    job = load_job()
    if job.get("status") == "queued":
        job.update(status="failed", phase="failed", completed_at=dt.datetime.now(dt.timezone.utc).isoformat(), error=message[:500])
        save(job)


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("autotune must run as root")
    try:
        action = sys.argv[1] if len(sys.argv) > 1 else ""
        if action == "enqueue":
            result = enqueue(json.load(sys.stdin))
        elif action == "run":
            run_job(); result = None
        elif action == "recover":
            recover(); result = None
        elif action == "fail-queued":
            fail_queued("autotune service could not be started"); result = None
        elif action == "get":
            result = load_job(sys.argv[2] if len(sys.argv) > 2 else None)
        elif action == "apply" and len(sys.argv) == 3:
            raw = sys.stdin.read().strip()
            payload = json.loads(raw) if raw else {}
            result = apply(sys.argv[2], payload.get("selections"))
        elif action == "repair" and len(sys.argv) == 3:
            result = repair(sys.argv[2])
        elif action == "monitor-get":
            result = monitor_public()
        elif action == "monitor-set":
            result = monitor_set(json.load(sys.stdin))
        elif action == "monitor-check":
            result = monitor_check()
        else:
            raise ValueError("unknown autotune action")
        json.dump(result, sys.stdout, ensure_ascii=False)
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr); raise SystemExit(1)


if __name__ == "__main__":
    main()
