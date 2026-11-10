from time import time
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="EAAF Mock LLM Provider")


class MockRequest(BaseModel):
    model: Literal["eaaf-mock", "eaaf-mock-hr", "eaaf-mock-it", "eaaf-mock-marketing"]


MOCK_SPACES = {
    "eaaf-mock": "Global",
    "eaaf-mock-hr": "HR",
    "eaaf-mock-it": "IT",
    "eaaf-mock-marketing": "Marketing",
}


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def complete(body: MockRequest) -> dict:
    space = MOCK_SPACES[body.model]
    return {
        "id": f"chatcmpl-eaaf-mock-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time()),
        "model": body.model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": f"Hello from the EAAF {space} mock ({body.model}). This response is simulated.",
            },
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13},
    }
