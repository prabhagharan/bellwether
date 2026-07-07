from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from bellwether.api.auth import router as auth_router
from bellwether.api.watchlist import router as watchlist_router
from bellwether.api.statements import router as statements_router
from bellwether.api.review import router as review_router
from bellwether.api.discovery import router as discovery_router
from bellwether.api.alert_rules import router as alert_rules_router
from bellwether.api.stream import router as stream_router
from bellwether.api.feed import router as feed_router
from bellwether.config import get_settings
from bellwether.db import SessionLocal
from bellwether.seed import seed_admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    with SessionLocal() as session:
        seed_admin(session)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="bellwether", lifespan=lifespan)
    origins = get_settings().cors_origins
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])
    app.include_router(auth_router)
    app.include_router(watchlist_router)
    app.include_router(statements_router)
    app.include_router(review_router)
    app.include_router(discovery_router)
    app.include_router(alert_rules_router)
    app.include_router(stream_router)
    app.include_router(feed_router)
    return app
