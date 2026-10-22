import re

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    aws_region: str = "ap-south-1"
    raw_bucket: str = ""
    processed_bucket: str = ""
    queue_url: str = ""
    files_table: str = "CloudVault_Files"
    user_files_table: str = "CloudVault_UserFiles"
    tenant_tokens: dict[str, str] = Field(default_factory=dict)
    cors_origins: list[str] = ["http://localhost:5173"]
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, gt=0)

    @model_validator(mode="after")
    def validate_tokens(self):
        for tenant, token in self.tenant_tokens.items():
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", tenant):
                raise ValueError("Tenant IDs must contain 1–64 letters, digits, '_' or '-'")
            if len(token) < 32 or token != token.strip():
                raise ValueError(
                    "Tenant tokens must have at least 32 characters and no edge spaces"
                )
        if len(set(self.tenant_tokens.values())) != len(self.tenant_tokens):
            raise ValueError("Each tenant must have a unique token")
        return self
