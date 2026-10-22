import logging
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.config import Settings
from backend.dependencies import User
from backend.routes.files import router

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    app = FastAPI(title="CloudVault", version="0.1.0")
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    async def aws_error(request: Request, exc: Exception):
        # Avoid logging SDK exception bodies, which can contain resource identifiers or URLs.
        logger.error("AWS operation failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=503, content={"detail": "Storage service unavailable; retry"}
        )

    app.add_exception_handler(ClientError, aws_error)
    app.add_exception_handler(BotoCoreError, aws_error)

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path == "/":
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self' https://*.amazonaws.com; "
                "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
            )
        return response

    @app.get("/health", tags=["health"])
    def health():
        return {"status": "ok"}

    @app.get("/api/session", tags=["session"])
    def session(user_id: User):
        return {
            "user_id": user_id,
            "region": settings.aws_region,
            "max_upload_bytes": settings.max_upload_bytes,
        }

    static = Path(__file__).parent / "static"
    if static.is_dir():
        app.mount("/static", StaticFiles(directory=static), name="static")

        @app.get("/", include_in_schema=False)
        def dashboard():
            return FileResponse(static / "index.html")

    app.include_router(router)
    return app


app = create_app()
