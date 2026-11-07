import os
from dataclasses import dataclass
from pathlib import Path
from secrets import compare_digest

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ApplicationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=1)
    cost_center: str = Field(min_length=1)
    key_env: str = Field(pattern=r"^[A-Z_][A-Z0-9_]*$")
    allowed_models: frozenset[str] = Field(min_length=1)
    requests_per_day: int = Field(gt=0, strict=True)
    monthly_budget_cents: int = Field(gt=0, strict=True)
    request_cost_cents: int = Field(gt=0, strict=True)


class ApplicationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applications: dict[str, ApplicationPolicy] = Field(min_length=1)


@dataclass(frozen=True)
class Application:
    name: str
    policy: ApplicationPolicy


def load_applications(path: Path) -> dict[bytes, Application]:
    config = ApplicationConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    policies = {}
    for application, policy in config.applications.items():
        key = os.environ.get(policy.key_env, "")
        if not key or not key.isascii() or not key.isprintable() or any(char.isspace() for char in key):
            raise ValueError(f"Set a valid bearer token in {policy.key_env} for {application}")
        encoded_key = key.encode("ascii")
        if encoded_key in policies:
            raise ValueError(f"Application keys must be unique ({application})")
        policies[encoded_key] = Application(application, policy)
    return policies


def authenticate(token: str, policies: dict[bytes, Application]) -> Application | None:
    candidate = token.encode("utf-8")
    return next((policy for key, policy in policies.items() if compare_digest(key, candidate)), None)
