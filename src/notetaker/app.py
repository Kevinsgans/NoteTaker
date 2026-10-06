import hashlib
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field

from .auth import verify
from .runtime import Runtime


class CaptureRequest(BaseModel):
    force: bool = False
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=160)
    tags: list[str] = Field(max_length=5)
    description: str = Field(default="", max_length=4000)
    course: str = Field(min_length=1, max_length=100)


class CornerRequest(BaseModel):
    corners: list[tuple[float, float]] = Field(min_length=4, max_length=4)


class SettingsRequest(BaseModel):
    course: str = Field(min_length=1, max_length=100)
    auto_capture: bool


def create_app(config, runtime=None):
    runtime = runtime or Runtime(config)
    auth_path = config.root / "auth.json"
    basic = HTTPBasic()
    auth_cache = {}
    auth_lock = threading.Lock()

    def authenticated(request: Request, credentials: HTTPBasicCredentials = Depends(basic)):
        if not auth_path.exists():
            raise HTTPException(503, "Run notetaker setup before starting the service")
        # Cache successful proofs briefly; do not retain plaintext credentials.
        proof = hashlib.sha256(credentials.password.encode()).digest()
        revision = auth_path.stat().st_mtime_ns
        key = (credentials.username, proof, revision)
        with auth_lock:
            cached = auth_cache.get(key, 0) > time.monotonic()
        valid = credentials.username == "notetaker" and (
            cached or verify(auth_path, credentials.password)
        )
        if not valid:
            raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Basic"})
        with auth_lock:
            if len(auth_cache) > 32:
                auth_cache.clear()
            auth_cache[key] = time.monotonic() + 60
        if request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                raise HTTPException(403, "Cross-origin writes are not allowed")

    @asynccontextmanager
    async def lifespan(app):
        if not auth_path.exists():
            raise RuntimeError("Run notetaker setup first")
        runtime.start()
        try:
            yield
        finally:
            runtime.close()

    app = FastAPI(
        title="NoteTaker",
        lifespan=lifespan,
        dependencies=[Depends(authenticated)],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.runtime = runtime

    @app.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' blob:; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "Page not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(TimeoutError)
    async def timeout(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(exc)}, status_code=504)

    @app.get("/")
    def index():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/static/{name}")
    def static(name: Literal["app.js", "style.css"]):
        return FileResponse(Path(__file__).parent / "static" / name)

    @app.get("/api/status")
    def status():
        return runtime.status

    @app.get("/api/preview")
    def preview():
        content = runtime.preview()
        if not content:
            raise HTTPException(503, "Preview is not ready")
        return Response(content, media_type="image/jpeg")

    @app.post("/api/capture")
    def capture(body: CaptureRequest):
        with runtime.repo.connection() as db:
            row = db.execute(
                "SELECT id FROM pages WHERE event_id=?", (str(body.event_id),)
            ).fetchone()
        if row:
            return runtime.repo.get(row["id"])
        result = runtime.command("capture", request_id=str(body.event_id), force=body.force)
        return runtime.repo.get(result["page_id"])

    @app.put("/api/settings")
    def settings(body: SettingsRequest):
        return runtime.command("settings", **body.model_dump())

    @app.post("/api/replay/next")
    def next_replay():
        return runtime.command("next")

    @app.get("/api/pages")
    def pages(
        q: str = "",
        course: str | None = None,
        after: date | None = None,
        before: date | None = None,
    ):
        return runtime.repo.search(
            q,
            course=course,
            after=str(after) if after else None,
            before=str(before) if before else None,
        )

    @app.get("/api/pages/{page_id}")
    def page(page_id: uuid.UUID):
        return runtime.repo.get(str(page_id))

    @app.get("/api/pages/{page_id}/image/{kind}")
    def image(page_id: uuid.UUID, kind: Literal["original", "corrected"]):
        page = runtime.repo.get(str(page_id))
        return FileResponse(config.root / page[kind], media_type="image/png")

    @app.put("/api/pages/{page_id}/metadata")
    def metadata(page_id: uuid.UUID, body: EditRequest):
        return runtime.repo.edit(str(page_id), **body.model_dump())

    @app.put("/api/pages/{page_id}/corners")
    def corners(page_id: uuid.UUID, body: CornerRequest):
        return runtime.repo.recrop(str(page_id), body.corners)

    @app.post("/api/pages/{page_id}/retry")
    def retry(page_id: uuid.UUID):
        result = runtime.repo.retry(str(page_id))
        runtime.status["resume_model"] = True
        return result

    return app
