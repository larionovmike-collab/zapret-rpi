#!/usr/bin/python3
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOSTAPD = Path("/etc/zapret-rpi/hostapd.conf")
PROFILE_DIR = Path("/etc/zapret-rpi/zapret2/profiles")
RELEASE_FILE = Path("/etc/zapret-rpi/release.env")
AUTOTUNE = "/usr/local/sbin/zapret-rpi-autotune"
AUTOTUNE_CURRENT = Path("/var/lib/zapret-rpi/autotune/current.json")
AUTOTUNE_JOBS = Path("/var/lib/zapret-rpi/autotune/jobs")
RUN_ID_RE = re.compile(r"[0-9]{8}T[0-9]{6}-[a-f0-9]{6}")


def release_value(name, default="unknown"):
    try:
        for line in RELEASE_FILE.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key == name:
                return value
    except OSError:
        pass
    return default


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def run(*args, check=True, input=None, timeout=None):
    return subprocess.run(args, text=True, capture_output=True, check=check, input=input, timeout=timeout)


def service_active(unit):
    return run("systemctl", "is-active", "--quiet", unit, check=False).returncode == 0


def service_state(unit):
    return run("systemctl", "show", unit, "-p", "ActiveState", "--value", check=False).stdout.strip()


def safe_output(*args, timeout=1.5):
    try:
        result = run(*args, check=False, timeout=timeout)
        return result.returncode == 0, result.stdout
    except subprocess.TimeoutExpired:
        return False, ""


def config_values():
    values = {}
    for line in HOSTAPD.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def wifi_get():
    cfg = config_values()
    return {"ssid": cfg.get("ssid", ""), "channel": int(cfg.get("channel", 0)), "password_set": bool(cfg.get("wpa_passphrase"))}


def wifi_set(body):
    ssid, channel = body.get("ssid"), body.get("channel")
    password = body.get("password")
    if not isinstance(ssid, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", ssid): die("invalid SSID")
    if channel not in (1, 6, 11): die("invalid channel")
    if password is not None and (not isinstance(password, str) or not re.fullmatch(r"[A-Za-z0-9.!_-]{8,63}", password)): die("invalid Wi-Fi password")
    old = HOSTAPD.read_text()
    cfg = config_values()
    cfg["ssid"], cfg["channel"] = ssid, str(channel)
    if password is not None: cfg["wpa_passphrase"] = password
    new = old
    for key in ("ssid", "channel", "wpa_passphrase"):
        if key in cfg:
            new = re.sub(rf"(?m)^{key}=.*$", f"{key}={cfg[key]}", new)
    fd, staged = tempfile.mkstemp(prefix="hostapd.", dir=str(HOSTAPD.parent))
    try:
        os.write(fd, new.encode()); os.close(fd); os.chmod(staged, 0o600)
        os.replace(staged, HOSTAPD)
        if run("systemctl", "restart", "zapret-rpi-hostapd.service", check=False).returncode or not service_active("zapret-rpi-hostapd.service"):
            HOSTAPD.write_text(old); os.chmod(HOSTAPD, 0o600)
            run("systemctl", "restart", "zapret-rpi-hostapd.service", check=False)
            die("Wi-Fi activation failed; previous configuration restored")
    finally:
        if os.path.exists(staged): os.unlink(staged)
    return wifi_get()


def profiles():
    active = run("/usr/local/sbin/zapret-rpi-profile", "get", check=False).stdout.strip()
    items = []
    for path in sorted(PROFILE_DIR.glob("*.conf")):
        content = path.read_text()
        match = re.search(r'^PROFILE_DESCRIPTION="(.*)"$', content, re.M)
        rules = [line.strip() for line in content.splitlines() if line.strip().startswith("--filter-")]
        items.append({"name": path.stem, "description": match.group(1) if match else "", "rules": rules})
    return {"active": active, "enabled": service_active("zapret2.service"), "profiles": items}


def status():
    def cpu_sample():
        values = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        return sum(values), values[3] + values[4]
    total1, idle1 = cpu_sample()
    time.sleep(0.1)
    total2, idle2 = cpu_sample()
    cpu_percent = (1 - (idle2 - idle1) / max(1, total2 - total1)) * 100
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1); mem[key] = int(value.strip().split()[0])
    leases, stations = [], set()
    station_ok, station_dump = safe_output("iw", "dev", "wlan0", "station", "dump")
    for match in re.finditer(r"(?m)^Station ([0-9a-f:]{17}) ", station_dump):
        stations.add(match.group(1).lower())
    if not station_ok:
        _, hostapd_dump = safe_output("hostapd_cli", "-i", "wlan0", "all_sta")
        for match in re.finditer(r"(?m)^([0-9a-f:]{17})$", hostapd_dump):
            stations.add(match.group(1).lower())
    lease_map = {}
    lease_paths = (Path("/var/lib/misc/dnsmasq.leases"), Path("/run/dnsmasq/dnsmasq.leases"), Path("/var/lib/dnsmasq/dnsmasq.leases"))
    for lease_file in lease_paths:
        if not lease_file.exists():
            continue
        for line in lease_file.read_text(errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 4:
                mac = parts[1].lower()
                lease_map[mac] = {"expires": int(parts[0]), "mac": mac, "ip": parts[2], "hostname": "" if parts[3] == "*" else parts[3], "active": mac in stations}
        break
    leases = [lease_map[mac] if mac in lease_map else {"expires": 0, "mac": mac, "ip": "—", "hostname": "", "active": True} for mac in sorted(stations)]
    p = profiles()
    return {"cpu_percent": round(max(0, min(100, cpu_percent)), 1), "memory_percent": round((mem["MemTotal"] - mem.get("MemAvailable", 0)) / mem["MemTotal"] * 100, 1), "clients": leases, "active_strategy": p["active"], "zapret_enabled": p["enabled"], "revision": release_value("ZAPRET2_COMMIT"), "project_version": release_value("PROJECT_VERSION")}


def autotune_job(job_id=None):
    if job_id is not None and (not isinstance(job_id, str) or not RUN_ID_RE.fullmatch(job_id)):
        die("invalid run id")
    path = AUTOTUNE_JOBS / f"{job_id}.json" if job_id else AUTOTUNE_CURRENT
    if not path.is_file():
        return None
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        die("cannot read autotune state")
    if job.get("status") in ("queued", "running") and service_state("zapret-rpi-autotune.service") not in ("active", "activating"):
        run(AUTOTUNE, "recover", check=False)
        job = json.loads(path.read_text(encoding="utf-8"))
    return job


def main():
    if os.geteuid() != 0: die("helper must run as root")
    action = sys.argv[1] if len(sys.argv) == 2 else ""
    raw_body = sys.stdin.read().strip()
    if raw_body:
        try: body = json.loads(raw_body)
        except json.JSONDecodeError: die("invalid JSON")
    else:
        body = {}
    if action in {"zapret-set", "zapret-enable", "zapret-restart", "autotune-start", "autotune-apply"} \
            and service_state("zapret-rpi-autocheck.service") in ("active", "activating"):
        die("an availability check is already in progress")
    if action == "status": result = status()
    elif action == "wifi-get": result = wifi_get()
    elif action == "wifi-set": result = wifi_set(body)
    elif action == "zapret-profiles": result = profiles()
    elif action == "zapret-profile":
        current = profiles()
        result = {"profile": current["active"], "state": "active" if current["enabled"] else "degraded", "revision": release_value("ZAPRET2_COMMIT"), "project_version": release_value("PROJECT_VERSION")}
    elif action == "zapret-set":
        name = body.get("profile", "")
        result = {"profile": run("/usr/local/sbin/zapret-rpi-profile", "set", name).stdout.strip(), "state": "active"}
    elif action == "zapret-enable":
        enabled = body.get("enabled")
        if not isinstance(enabled, bool): die("enabled must be boolean")
        run("systemctl", "enable" if enabled else "disable", "--now", "zapret2.service")
        result = {"enabled": service_active("zapret2.service")}
    elif action == "zapret-restart":
        run("systemctl", "restart", "zapret2.service")
        if not service_active("zapret2.service"): die("zapret2 restart failed")
        result = None
    elif action == "zapret-logs":
        lines = body.get("lines", 100)
        if not isinstance(lines, int) or not 1 <= lines <= 500: die("invalid line count")
        output = run("journalctl", "-u", "zapret2.service", "-n", str(lines), "--no-pager", "-o", "short-iso", check=False).stdout
        result = {"lines": output.splitlines()}
    elif action == "autotune-start":
        state = service_state("zapret-rpi-autotune.service")
        if state not in ("", "inactive", "failed"): die("an autotune run is already in progress")
        queued = run(AUTOTUNE, "enqueue", check=False, input=json.dumps(body))
        if queued.returncode: die(queued.stderr.strip() or "cannot queue autotune run")
        if run("systemctl", "start", "--no-block", "zapret-rpi-autotune.service", check=False).returncode:
            run(AUTOTUNE, "fail-queued", check=False)
            die("cannot start autotune service")
        result = json.loads(queued.stdout)
    elif action == "autotune-get":
        job_id = body.get("id")
        result = autotune_job(job_id)
        if job_id is not None and result is None: die("autotune run not found")
    elif action == "autotune-cancel":
        job_id = body.get("id")
        current = autotune_job(job_id)
        if current is None: die("autotune run not found")
        if current.get("status") not in ("queued", "running"): die("autotune run is not active")
        stopped = run("systemctl", "stop", "zapret-rpi-autotune.service", check=False, timeout=15)
        if stopped.returncode: die(stopped.stderr.strip() or "cannot stop autotune service")
        run(AUTOTUNE, "recover", check=False)
        result = autotune_job(job_id)
    elif action == "autotune-apply":
        job_id = body.get("id")
        if not isinstance(job_id, str) or not RUN_ID_RE.fullmatch(job_id): die("invalid run id")
        selections = body.get("selections")
        if not isinstance(selections, list): die("strategy selections are required")
        applied = run(AUTOTUNE, "apply", job_id, check=False, input=json.dumps({"selections": selections}))
        if applied.returncode: die(applied.stderr.strip() or "cannot apply autotune result")
        run("systemctl", "start", "--no-block", "zapret-rpi-autocheck.service", check=False)
        result = json.loads(applied.stdout)
    elif action == "autotune-monitor-get":
        monitor = run(AUTOTUNE, "monitor-get", check=False)
        if monitor.returncode: die(monitor.stderr.strip() or "cannot read availability monitor")
        result = json.loads(monitor.stdout)
    elif action == "autotune-monitor-set":
        changed = run(AUTOTUNE, "monitor-set", check=False, input=json.dumps(body))
        if changed.returncode: die(changed.stderr.strip() or "cannot configure availability monitor")
        result = json.loads(changed.stdout)
        if result.get("enabled"):
            run("systemctl", "start", "--no-block", "zapret-rpi-autocheck.service", check=False)
    else: die("unknown action")
    json.dump(result, sys.stdout)


if __name__ == "__main__": main()
