import secrets
from dataclasses import dataclass
from typing import Annotated

import boto3
from botocore.config import Config
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.services.database import Database
from backend.services.queue import JobQueue
from backend.services.storage import Storage

bearer = HTTPBearer(auto_error=False)


def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials:
        for user_id, token in request.app.state.settings.tenant_tokens.items():
            if secrets.compare_digest(credentials.credentials.encode(), token.encode()):
                return user_id
    raise HTTPException(
        401, "Invalid or missing tenant token", headers={"WWW-Authenticate": "Bearer"}
    )


@dataclass
class Services:
    database: Database
    storage: Storage
    queue: JobQueue


def get_services(request: Request) -> Services:
    if not hasattr(request.app.state, "services"):
        settings = request.app.state.settings
        if not all([settings.raw_bucket, settings.processed_bucket, settings.queue_url]):
            raise HTTPException(503, "Configure RAW_BUCKET, PROCESSED_BUCKET and QUEUE_URL")
        session = boto3.Session(region_name=settings.aws_region, profile_name=settings.aws_profile)
        config = Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"mode": "standard", "total_max_attempts": 3},
        )
        request.app.state.services = Services(
            database=Database(
                session.client("dynamodb", config=config),
                settings.files_table,
                settings.user_files_table,
            ),
            storage=Storage(
                session.client("s3", config=config.merge(Config(signature_version="s3v4"))),
                settings.raw_bucket,
                settings.processed_bucket,
            ),
            queue=JobQueue(session.client("sqs", config=config), settings.queue_url),
        )
    return request.app.state.services


User = Annotated[str, Depends(current_user)]
AWS = Annotated[Services, Depends(get_services)]
