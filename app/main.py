from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routers.profile import router as profile_router
from app.api.routers.tasks import router as tasks_router
from app.api.routers.routine import router as routine_router
from app.api.routers.autoplan import router as autoplan_router
from app.api.routers.health import router as health_router
from app.api.routers.habits import router as habits_router


def create_app() -> FastAPI:
    app = FastAPI(title="Day Planner Agent API")

    app.include_router(tasks_router)
    app.include_router(routine_router)
    app.include_router(autoplan_router)
    app.include_router(profile_router)
    app.include_router(health_router)
    app.include_router(habits_router)

    @app.get("/health")
    def health():
        return {"ok": True}

    app.mount("/web", StaticFiles(directory="app/web", html=True), name="web")

    return app
