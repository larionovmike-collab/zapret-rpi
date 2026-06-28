from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings
from .helper import Helper


class WifiUpdate(BaseModel):
    ssid: str = Field(pattern=r"^[A-Za-z0-9._-]{1,32}$")
    password: str | None = Field(default=None, min_length=8, max_length=63, pattern=r"^[A-Za-z0-9.!_-]+$")
    channel: int


class ProfileUpdate(BaseModel):
    profile: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")


class EnabledUpdate(BaseModel):
    enabled: bool


class AutotuneStart(BaseModel):
    domains: list[str] = Field(default_factory=lambda: ["rutracker.org"], min_length=1, max_length=30)
    protocols: list[str] = Field(default_factory=lambda: ["http", "https", "quic"], min_length=1, max_length=3)
    repeats: int = Field(default=2, ge=1, le=5)
    scan_level: str = Field(default="quick", pattern=r"^(quick|standard|force)$")
    test_set: str = Field(default="auto", pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")


class StrategySelection(BaseModel):
    protocol: str = Field(pattern=r"^(http|https|quic)$")
    strategy: str = Field(min_length=1, max_length=1000)


class AutotuneApply(BaseModel):
    selections: list[StrategySelection] = Field(min_length=1, max_length=3)


class AutotuneMonitorUpdate(AutotuneStart):
    enabled: bool
    interval_minutes: int = Field(default=60, ge=15, le=1440)


def create_app(settings: Settings | None = None, helper: Helper | None = None) -> FastAPI:
    settings = settings or Settings()
    helper = helper or Helper(settings.helper)
    app = FastAPI(title="zapret-rpi UI", version="1.0.0", docs_url=None, redoc_url=None)

    @app.get("/api/v1/status")
    async def system_status():
        return await helper.call("status")

    @app.get("/api/v1/wifi")
    async def get_wifi():
        return await helper.call("wifi-get")

    @app.put("/api/v1/wifi")
    async def set_wifi(body: WifiUpdate):
        if body.channel not in (1, 6, 11):
            raise HTTPException(422, "channel must be 1, 6 or 11")
        return await helper.call("wifi-set", body.model_dump(exclude_none=True))

    @app.get("/api/v1/zapret/profiles")
    async def profiles():
        return await helper.call("zapret-profiles")

    @app.get("/api/v1/zapret/profile")
    async def current_profile():
        return await helper.call("zapret-profile")

    @app.put("/api/v1/zapret/profile")
    async def profile(body: ProfileUpdate):
        return await helper.call("zapret-set", body.model_dump())

    @app.put("/api/v1/zapret/enabled")
    async def enabled(body: EnabledUpdate):
        return await helper.call("zapret-enable", body.model_dump())

    @app.post("/api/v1/zapret/restart", status_code=204)
    async def restart():
        await helper.call("zapret-restart")

    @app.get("/api/v1/zapret/logs")
    async def logs(lines: int = 100):
        return await helper.call("zapret-logs", {"lines": max(1, min(lines, 500))})

    @app.post("/api/v1/autotune/runs", status_code=202)
    async def autotune_start(body: AutotuneStart):
        return await helper.call("autotune-start", body.model_dump())

    @app.get("/api/v1/autotune/runs/current")
    async def autotune_current():
        return await helper.call("autotune-get")

    @app.get("/api/v1/autotune/runs/{run_id}")
    async def autotune_result(run_id: str):
        return await helper.call("autotune-get", {"id": run_id})

    @app.post("/api/v1/autotune/runs/{run_id}/cancel")
    async def autotune_cancel(run_id: str):
        return await helper.call("autotune-cancel", {"id": run_id})

    @app.post("/api/v1/autotune/runs/{run_id}/apply")
    async def autotune_apply(run_id: str, body: AutotuneApply):
        return await helper.call("autotune-apply", {"id": run_id, "selections": [item.model_dump() for item in body.selections]})

    @app.get("/api/v1/autotune/monitor")
    async def autotune_monitor():
        return await helper.call("autotune-monitor-get")

    @app.put("/api/v1/autotune/monitor")
    async def autotune_monitor_update(body: AutotuneMonitorUpdate):
        return await helper.call("autotune-monitor-set", body.model_dump())

    static = settings.static_dir
    if static.is_dir():
        assets = static / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def frontend(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "API route not found")
            candidate = (static / path).resolve()
            if candidate.is_file() and static.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(static / "index.html")

    return app
