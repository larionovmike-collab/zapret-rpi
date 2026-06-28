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

    def test_shell_scripts_use_lf(self):
        for script in ROOT.rglob("*.sh"):
            if "node_modules" not in script.parts:
                self.assertNotIn(b"\r", script.read_bytes(), str(script))

    def test_local_secrets_and_build_dependencies_are_ignored(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", ignored)
        self.assertIn("codex-state/", ignored)
        self.assertIn("web/frontend/node_modules/", ignored)


if __name__ == "__main__":
    unittest.main()
