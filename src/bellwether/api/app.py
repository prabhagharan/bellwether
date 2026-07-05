from contextlib import asynccontextmanager
from fastapi import FastAPI
from bellwether.api.auth import router as auth_router
from bellwether.api.watchlist import router as watchlist_router
from bellwether.api.statements import router as statements_router
from bellwether.api.review import router as review_router
from bellwether.api.programs import router as programs_router
from bellwether.api.optimize_api import router as optimize_router
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
    app.include_router(statements_router)
    app.include_router(review_router)
    app.include_router(programs_router)
    app.include_router(optimize_router)
    return app
