import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class RepositoryTests(unittest.TestCase):
    def test_release_metadata(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        upstream = (ROOT / "UPSTREAM_COMMIT").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+(?:[.-][A-Za-z0-9.-]+)?$")
        self.assertRegex(upstream, r"^[0-9a-f]{40}$")

    def test_frontend_dist_is_complete(self):
        index = (ROOT / "web/frontend/dist/index.html").read_text(encoding="utf-8")
        assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index)
        self.assertGreaterEqual(len(assets), 2)
        for asset in assets:
            self.assertTrue((ROOT / "web/frontend/dist" / asset.removeprefix("/")).is_file(), asset)

    def test_public_bootstrap_urls_use_repository_name(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for script in ("install.sh", "update.sh", "rollback.sh"):
            self.assertIn(
                f"https://raw.githubusercontent.com/larionovmike-collab/zapret-rpi/refs/heads/main/{script}",
                readme,
            )

    def test_installers_do_not_gate_on_caller_connection(self):
        for name in ("install.sh", "update.sh", "scripts/install.sh", "scripts/update-system.sh"):
            script = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("SSH_CONNECTION", script)
            self.assertNotIn("--ssh-client", script)
            self.assertNotIn("detect_ssh_client", script)

    def test_upstream_runtime_permissions_are_restored_after_clone(self):
        installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        validator = (ROOT / "scripts/validate.sh").read_text(encoding="utf-8")
        self.assertIn("chmod -R a+rX /opt/zapret2", installer)
        self.assertIn("runuser -u tpws -- test -r /opt/zapret2/lua/zapret-lib.lua", validator)

    def test_web_runtime_permissions_are_restored_after_private_umask(self):
        installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        validator = (ROOT / "scripts/validate.sh").read_text(encoding="utf-8")
        self.assertIn("chmod 755 /usr/local/lib/zapret-rpi /usr/local/lib/zapret-rpi/web", installer)
        self.assertIn("chmod -R u=rwX,go=rX /usr/local/lib/zapret-rpi/web", installer)
        self.assertIn(
            "runuser -u zapret-web -- test -x /usr/local/lib/zapret-rpi/web/venv/bin/uvicorn",
            validator,
        )

    def test_availability_monitor_units_are_managed(self):
        installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        updater = (ROOT / "scripts/update-system.sh").read_text(encoding="utf-8")
        rollback = (ROOT / "scripts/rollback.sh").read_text(encoding="utf-8")
        self.assertIn("zapret-rpi-autocheck.timer", installer)
        self.assertIn("/etc/systemd/system/zapret-rpi-*.timer", updater)
        self.assertIn("zapret-rpi-autocheck.timer", rollback)
        self.assertTrue((ROOT / "systemd/zapret-rpi-autocheck.service").is_file())
        self.assertTrue((ROOT / "systemd/zapret-rpi-autocheck.timer").is_file())
        monitor_unit = (ROOT / "systemd/zapret-rpi-autocheck.service").read_text(encoding="utf-8")
        self.assertIn("RuntimeDirectory=netns", monitor_unit)
        self.assertIn("ReadWritePaths=/var/lib/zapret-rpi/autotune /run/netns", monitor_unit)
        for name in ("install.sh", "update.sh"):
            self.assertIn("-name '*.timer'", (ROOT / name).read_text(encoding="utf-8"))
        firewall = (ROOT / "configs/nftables/zapret-rpi.nft.in").read_text(encoding="utf-8")
        runner = (ROOT / "scripts/autotune.py").read_text(encoding="utf-8")
        self.assertIn('iifname "zapret-mon" oifname "eth0"', firewall)
        self.assertIn('"forward_lan_filter"', runner)

    def test_runtime_text_files_use_lf(self):
        suffixes = {
            ".sh", ".py", ".service", ".timer", ".conf", ".in", ".network", ".md",
            ".yml", ".yaml", ".txt", ".json", ".js", ".jsx", ".css", ".html",
        }
        for path in ROOT.rglob("*"):
            if path.is_file() and "node_modules" not in path.parts:
                if path.suffix in suffixes or path.name in {"VERSION", "UPSTREAM_COMMIT", "config"}:
                    self.assertNotIn(b"\r", path.read_bytes(), str(path))

    def test_local_secrets_and_build_dependencies_are_ignored(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", ignored)
        self.assertIn("codex-state/", ignored)
        self.assertIn("web/frontend/node_modules/", ignored)


if __name__ == "__main__":
    unittest.main()
