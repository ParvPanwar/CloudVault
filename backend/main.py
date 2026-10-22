import logging

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import Settings
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
        return response

    @app.get("/health", tags=["health"])
    def health():
        return {"status": "ok"}

    app.include_router(router)
    return app


app = create_app()
