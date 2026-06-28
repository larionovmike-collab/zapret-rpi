import asyncio
import json
from pathlib import Path

from fastapi import HTTPException


class Helper:
    def __init__(self, executable: Path):
        self.executable = executable
        self._apply_lock = asyncio.Lock()

    async def call(self, action: str, payload: dict | None = None) -> object:
        mutations = {"wifi-set", "zapret-set", "zapret-enable", "autotune-start", "autotune-cancel", "autotune-apply", "autotune-monitor-set"}
        if self._apply_lock.locked() and action in mutations:
            raise HTTPException(409, "another configuration change is in progress")
        locked = action in mutations
        if locked:
            await self._apply_lock.acquire()
        try:
            process = await asyncio.create_subprocess_exec(
                "sudo", str(self.executable), action,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(payload or {}).encode()), timeout=20
            )
            if process.returncode:
                detail = stderr.decode(errors="replace").strip() or "helper failed"
                code = 409 if "already in progress" in detail else 503
                raise HTTPException(code, detail[:500])
            return json.loads(stdout or b"null")
        except asyncio.TimeoutError:
            process.kill()
            raise HTTPException(504, "helper timed out") from None
        finally:
            if locked:
                self._apply_lock.release()
