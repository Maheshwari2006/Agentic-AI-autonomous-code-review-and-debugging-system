from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import os

from .config import get_settings
from .logging_config import configure_logging, get_logger
from .database.session import init_db
from .api.routes import router
from .api.implementation_routes import router as implementation_router

configure_logging()
logger = get_logger(__name__)

app = FastAPI(
    title="Agentic AI Code Review & Debugging System",
    description="Autonomous, commit-based, symbol-level code review powered by Claude.",
    version="1.0.0",
)

settings = get_settings()
origins = ["*"] if settings.cors_origins == "*" else [o.strip() for o in settings.cors_origins.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("Database initialized")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(f"unhandled_error path={request.url.path}")
    return JSONResponse(status_code=500, content={"detail": "Internal server error. See server logs."})


app.include_router(router, prefix="/api")
app.include_router(implementation_router, prefix="/api")

_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
