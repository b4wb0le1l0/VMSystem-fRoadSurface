from fastapi import FastAPI
from .endpoints import router

def create_app() -> FastAPI:
    app = FastAPI(title="VibroRoad API", version="0.3.0")
    app.include_router(router)
    return app

app = create_app()