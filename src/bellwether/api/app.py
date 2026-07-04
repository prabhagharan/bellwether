from contextlib import asynccontextmanager
from fastapi import FastAPI
from bellwether.api.auth import router as auth_router
from bellwether.api.watchlist import router as watchlist_router
from bellwether.db import SessionLocal
from bellwether.seed import seed_admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    with SessionLocal() as session:
        seed_admin(session)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="bellwether", lifespan=lifespan)
    app.include_router(auth_router)
    app.include_router(watchlist_router)
    return app
