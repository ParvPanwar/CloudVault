import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.config import Settings
from backend.main import create_app


@pytest.mark.parametrize(
    "tokens",
    [
        {"../alice": "a" * 32},
        {"alice": "short"},
        {"alice": "a" * 32, "bob": "a" * 32},
    ],
)
def test_invalid_tenant_settings_fail_closed(tokens):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, tenant_tokens=tokens)


def test_missing_aws_config_does_not_contact_aws():
    token = "a" * 32
    app = create_app(
        Settings(
            _env_file=None,
            raw_bucket="",
            processed_bucket="",
            queue_url="",
            tenant_tokens={"alice": token},
        )
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert (
            client.get("/api/files", headers={"Authorization": f"Bearer {token}"}).status_code
            == 503
        )
