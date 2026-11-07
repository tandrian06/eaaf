import os

import httpx
import pytest


@pytest.mark.skipif(not os.getenv("EAAF_E2E_URL"), reason="Requires the running three-service stack")
@pytest.mark.parametrize("application, model, status", [
    ("hr", "eaaf-mock", 200),
    ("hr", "eaaf-mock-extended", 403),
    ("it", "eaaf-mock", 200),
    ("it", "eaaf-mock-extended", 200),
    ("marketing", "eaaf-mock-extended", 200),
    ("marketing", "eaaf-mock", 403),
    (None, "eaaf-mock", 401),
])
def test_application_access(application, model, status):
    headers = {}
    if application:
        key = os.getenv(f"EAAF_{application.upper()}_API_KEY") or f"eaaf-local-{application}"
        headers["Authorization"] = f"Bearer {key}"
    response = httpx.post(
        f"{os.environ['EAAF_E2E_URL'].rstrip('/')}/v1/chat/completions",
        headers=headers,
        json={"model": model, "messages": [{"role": "user", "content": "Hello"}]},
        timeout=20,
        trust_env=False,
    )
    assert response.status_code == status, response.text
    if status != 200:
        return
    result = response.json()
    assert result["model"] == model
    assert result["object"] == "chat.completion"
    assert result["id"].startswith("chatcmpl-eaaf-mock-")
    assert result["choices"][0]["message"]["content"] == "Hello from the EAAF mock provider."
    assert result["usage"]["total_tokens"] == 13
