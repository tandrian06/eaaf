import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from eaaf.policies import Application, authenticate, load_applications
from eaaf.usage import usage_store

DEFAULT_APPLICATIONS = Path(__file__).resolve().parents[1] / "config" / "applications.yaml"
CHAT_ASSETS = Path(__file__).resolve().parents[1] / "chat"
application_bearer = HTTPBearer(auto_error=False, scheme_name="Application key")


def require_application(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(application_bearer)],
) -> Application:
    if credentials:
        policy = authenticate(credentials.credentials, request.app.state.applications)
        if policy is not None:
            return policy
    request.app.state.usage.record_denial("unauthenticated", 401)
    raise HTTPException(401, "Invalid or missing application key", headers={"WWW-Authenticate": "Bearer"})


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    messages: list[Message] = Field(min_length=1)
    stream: Literal[False] = False


async def proxy_completion(client: httpx.AsyncClient, body: ChatRequest) -> dict:
    try:
        response = await client.post("/v1/chat/completions", json=body.model_dump())
        response.raise_for_status()
        completion = response.json()
    except httpx.TimeoutException:
        raise HTTPException(504, "LiteLLM Proxy timed out") from None
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "LiteLLM Proxy request failed") from None

    if not isinstance(completion, dict) or completion.get("object") != "chat.completion":
        raise HTTPException(502, "Invalid completion from LiteLLM Proxy")
    return completion


def create_app(transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config_path = Path(os.environ.get("EAAF_APPLICATIONS_CONFIG", DEFAULT_APPLICATIONS))
        app.state.applications = load_applications(config_path)
        proxy_key = os.environ["LITELLM_MASTER_KEY"]
        if not proxy_key.startswith("sk-") or len(proxy_key) <= 3:
            raise ValueError("LITELLM_MASTER_KEY must start with sk- and contain a key")
        app.state.usage = usage_store()
        app.state.usage.initialize()
        async with httpx.AsyncClient(
            base_url=os.environ.get("LITELLM_BASE_URL", "http://127.0.0.1:4000"),
            headers={"Authorization": f"Bearer {proxy_key}"},
            timeout=15,
            trust_env=False,
            transport=transport,
        ) as client:
            app.state.proxy = client
            yield

    app = FastAPI(title="EAAF Governance Gateway", lifespan=lifespan)
    app.mount("/chat-assets", StaticFiles(directory=CHAT_ASSETS), name="chat-assets")

    @app.get("/", include_in_schema=False)
    async def home() -> RedirectResponse:
        return RedirectResponse("/chat/global")

    @app.get("/chat/{space}", include_in_schema=False, response_model=None)
    async def chat(space: str, request: Request) -> FileResponse | RedirectResponse:
        canonical = space.lower()
        if not any(profile.name == canonical for profile in request.app.state.applications.values()):
            raise HTTPException(404, "Chat space not found")
        if space != canonical:
            return RedirectResponse(f"/chat/{canonical}")
        return FileResponse(
            CHAT_ASSETS / "index.html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'none'; script-src 'self'; style-src 'self'; "
                    "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
                ),
                "Referrer-Policy": "no-referrer",
            },
        )

    @app.exception_handler(sqlite3.Error)
    async def usage_unavailable(request: Request, exc: sqlite3.Error) -> JSONResponse:
        return JSONResponse({"detail": "Usage store unavailable"}, status_code=503)

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/chat/{space}/completions", include_in_schema=False)
    @app.post("/v1/chat/completions")
    async def complete(
        body: ChatRequest,
        request: Request,
        application: Annotated[Application, Security(require_application)],
    ) -> dict:
        usage = request.app.state.usage
        space = request.path_params.get("space")
        if space is not None and space.lower() != application.name:
            await run_in_threadpool(usage.record_denial, "chat_space_forbidden", 403, application)
            raise HTTPException(403, "Application key is not allowed in this chat space")
        if body.model not in application.policy.allowed_models:
            await run_in_threadpool(usage.record_denial, "model_forbidden", 403, application)
            raise HTTPException(403, "Model is not allowed for this application")

        event_id, decision = await run_in_threadpool(usage.reserve, application, body.model)
        if decision == "quota_exceeded":
            raise HTTPException(429, "Daily request quota exceeded")
        if decision == "budget_exceeded":
            raise HTTPException(429, "Monthly simulated budget exceeded")

        try:
            completion = await proxy_completion(request.app.state.proxy, body)
        except HTTPException as exc:
            await run_in_threadpool(usage.finish, event_id, exc.status_code)
            raise

        reported_usage = completion.get("usage")
        total_tokens = reported_usage.get("total_tokens") if isinstance(reported_usage, dict) else None
        if type(total_tokens) is not int or not 0 <= total_tokens < 2**63:
            total_tokens = None
        await run_in_threadpool(usage.finish, event_id, 200, total_tokens)
        return completion

    return app


app = create_app()
