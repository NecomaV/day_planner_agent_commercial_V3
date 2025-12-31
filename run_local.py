from app.main import create_app
from app.settings import settings

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "run_local:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=False,
    )
