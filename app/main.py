from fastapi import FastAPI

from app.api.routers.tasks import router as tasks_router
from app.api.routers.routine import router as routine_router
from app.api.routers.autoplan import router as autoplan_router


def create_app() -> FastAPI:
    app = FastAPI(title="Day Planner Agent API")

    app.include_router(tasks_router)
    app.include_router(routine_router)
    app.include_router(autoplan_router)

    @app.get("/health")
    def health():
        return {"ok": True}

    return app
