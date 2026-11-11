import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from eaaf.policies import Application, authenticate, load_applications
from eaaf.usage import usage_store

DEFAULT_APPLICATIONS = Path(__file__).resolve().parents[1] / "config" / "applications.yaml"
CHAT_ASSETS = Path(__file__).resolve().parents[1] / "chat"
OPENAI_ALIAS = "eaaf-openai-mini"
EMBEDDING_ALIAS = "eaaf-knowledge-embedding"
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


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)


class EmbeddingRequest(ModelRequest):
    input: list[Annotated[str, Field(min_length=1, max_length=10000)]] = Field(min_length=1, max_length=64)


class ChatRequest(ModelRequest):
    messages: list[Message] = Field(min_length=1)
    stream: Literal[False] = False
    use_knowledge: bool = False
    retrieval: Literal["keyword", "semantic"] = "keyword"


async def proxy_completion(client: httpx.AsyncClient, body: ChatRequest) -> dict:
    try:
        response = await client.post(
            "/v1/chat/completions", json=body.model_dump(exclude={"use_knowledge", "retrieval"})
        )
        response.raise_for_status()
        completion = response.json()
    except httpx.TimeoutException:
        raise HTTPException(504, "LiteLLM Proxy timed out") from None
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "LiteLLM Proxy request failed") from None

    if not isinstance(completion, dict) or completion.get("object") != "chat.completion":
        raise HTTPException(502, "Invalid completion from LiteLLM Proxy")
    return completion


async def authorize(request: Request, application: Application, model: str, *, embedding=False):
    usage = request.app.state.usage
    space = request.path_params.get("space")
    if space is not None and space.lower() != application.name:
        await run_in_threadpool(usage.record_denial, "chat_space_forbidden", 403, application)
        raise HTTPException(403, "Application key is not allowed in this chat space")
    allowed = application.policy.allowed_embedding_models if embedding else application.policy.allowed_models
    if model not in allowed:
        await run_in_threadpool(usage.record_denial, "model_forbidden", 403, application)
        raise HTTPException(403, "Model is not allowed for this application")
    if model in {OPENAI_ALIAS, EMBEDDING_ALIAS} and not request.app.state.openai_enabled:
        await run_in_threadpool(usage.record_denial, "model_disabled", 403, application)
        raise HTTPException(403, "OpenAI mode is disabled")


async def reserve(usage, application, model):
    event_id, decision = await run_in_threadpool(usage.reserve, application, model)
    if decision == "quota_exceeded":
        raise HTTPException(429, "Daily request quota exceeded")
    if decision == "budget_exceeded":
        raise HTTPException(429, "Monthly simulated budget exceeded")
    return event_id


def create_app(transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config_path = Path(os.environ.get("EAAF_APPLICATIONS_CONFIG", DEFAULT_APPLICATIONS))
        app.state.applications = load_applications(config_path)
        app.state.chat_spaces = {
            application.name: application.policy for application in app.state.applications.values()
        }
        app.state.openai_enabled = os.environ.get("EAAF_OPENAI_ENABLED") == "true"
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
        if canonical not in request.app.state.chat_spaces:
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

    @app.get("/chat/{space}/options", include_in_schema=False)
    async def chat_options(space: str, request: Request) -> JSONResponse:
        policy = request.app.state.chat_spaces.get(space.lower())
        if policy is None:
            raise HTTPException(404, "Chat space not found")
        models = [
            {"id": alias, "external": alias == OPENAI_ALIAS}
            for alias in sorted(policy.allowed_models)
            if alias != OPENAI_ALIAS or request.app.state.openai_enabled
        ]
        return JSONResponse(
            {"models": models, "knowledge": False, "semantic": False,
             "embeddings": EMBEDDING_ALIAS in policy.allowed_embedding_models and request.app.state.openai_enabled},
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/chat/{space}/authorize")
    async def authorize_chat(
        space: str, body: ModelRequest, request: Request,
        application: Annotated[Application, Security(require_application)],
    ) -> Response:
        await authorize(request, application, body.model)
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    @app.post("/chat/{space}/embeddings")
    async def embeddings(
        space: str, body: EmbeddingRequest, request: Request,
        application: Annotated[Application, Security(require_application)],
    ) -> dict:
        await authorize(request, application, body.model, embedding=True)
        usage = request.app.state.usage
        event_id = await reserve(usage, application, body.model)
        try:
            response = await request.app.state.proxy.post("/v1/embeddings", json=body.model_dump())
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                raise ValueError("Invalid embeddings")
        except httpx.TimeoutException:
            await run_in_threadpool(usage.finish, event_id, 504)
            raise HTTPException(504, "Embedding provider timed out") from None
        except (httpx.HTTPError, ValueError):
            await run_in_threadpool(usage.finish, event_id, 502)
            raise HTTPException(502, "Embedding provider request failed") from None
        await run_in_threadpool(usage.finish, event_id, 200)
        return result

    @app.post("/chat/{space}/completions", include_in_schema=False)
    @app.post("/v1/chat/completions")
    async def complete(
        body: ChatRequest,
        request: Request,
        application: Annotated[Application, Security(require_application)],
    ) -> dict:
        usage = request.app.state.usage
        await authorize(request, application, body.model)
        if body.use_knowledge:
            await run_in_threadpool(usage.record_denial, "knowledge_forbidden", 403, application)
            raise HTTPException(403, "Department knowledge is available through EAAF Work")

        event_id = await reserve(usage, application, body.model)
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
