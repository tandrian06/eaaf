from time import time
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="EAAF Mock LLM Provider")


class MockRequest(BaseModel):
    model: Literal["eaaf-mock-backend"]


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def complete(body: MockRequest) -> dict:
    return {
        "id": f"chatcmpl-eaaf-mock-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time()),
        "model": body.model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "Hello from the EAAF mock provider."},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13},
    }
