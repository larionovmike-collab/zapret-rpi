from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    static_dir: Path = Path("/usr/local/lib/zapret-rpi/web/static")
    helper: Path = Path("/usr/local/sbin/zapret-rpi-web-helper")
