import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.api.routers.profile import router as profile_router
from app.api.routers.tasks import router as tasks_router
from app.api.routers.routine import router as routine_router
from app.api.routers.autoplan import router as autoplan_router
from app.api.routers.health import router as health_router
from app.api.routers.habits import router as habits_router
from app.api.routers.debug import router as debug_router
from app.logging_utils import RedactFilter


def create_app() -> FastAPI:
    app = FastAPI(title="Day Planner Agent API")

    logger = logging.getLogger("app.api")
    logger.addFilter(RedactFilter())

    app.include_router(tasks_router)
    app.include_router(routine_router)
    app.include_router(autoplan_router)
    app.include_router(profile_router)
    app.include_router(health_router)
    app.include_router(habits_router)
    app.include_router(debug_router)

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = int((time.perf_counter() - start) * 1000)

        response.headers["X-Request-Id"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers[
            "Content-Security-Policy"
        ] = "default-src 'self'; style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; font-src 'self' https://fonts.gstatic.com; script-src 'self'; img-src 'self' data:;"

        user_id = getattr(request.state, "user_id", None)
        logger.info(
            "request_id=%s path=%s status=%s user_id=%s duration_ms=%s",
            request_id,
            request.url.path,
            response.status_code,
            user_id,
            duration_ms,
        )
        return response

    @app.get("/health")
    def health():
        return {"ok": True}

    app.mount("/web", StaticFiles(directory="app/web", html=True), name="web")

    return app
