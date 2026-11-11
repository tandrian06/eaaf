import os
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values


MOCKS = {
    "global": ("eaaf-mock", "Global"),
    "hr": ("eaaf-mock-hr", "HR"),
    "it": ("eaaf-mock-it", "IT"),
    "marketing": ("eaaf-mock-marketing", "Marketing"),
}


def assert_mock_completion(result, model, space_name):
    assert result["model"] == model
    assert result["object"] == "chat.completion"
    assert result["id"].startswith("chatcmpl-eaaf-mock-")
    assert result["choices"][0]["message"]["content"] == (
        f"Hello from the EAAF {space_name} mock ({model}). This response is simulated."
    )
    assert result["usage"]["total_tokens"] == 13


def application_key(application):
    name = f"EAAF_{application.upper()}_API_KEY"
    env_file = Path(__file__).resolve().parents[1] / ".env"
    key = os.getenv(name) if name in os.environ else dotenv_values(env_file).get(name)
    if not key:
        pytest.fail(f"Set {name} for end-to-end tests")
    return key


@pytest.mark.skipif(not os.getenv("EAAF_E2E_URL"), reason="Requires the running three-service stack")
def test_chat_page_and_assets_are_served():
    with httpx.Client(base_url=os.environ["EAAF_E2E_URL"], trust_env=False) as client:
        assert client.get("/").headers["location"] == "/chat/global"
        assert client.get("/chat/IT").headers["location"] == "/chat/it"
        assert client.get("/chat/unknown").status_code == 404
        keys = [application_key(space) for space in ("global", "hr", "it", "marketing")]
        for space, (alias, _) in MOCKS.items():
            page = client.get(f"/chat/{space}")
            assert page.status_code == 200
            assert "<title>EAAF Chat" in page.text
            assert "connect-src 'self'" in page.headers["Content-Security-Policy"]
            assert not any(key in page.text for key in keys)
            configuration = client.get(f"/chat/{space}/options").json()
            assert configuration["knowledge"] is False
            options = configuration["models"]
            assert options[0] == {"id": alias, "external": False}
            assert [entry["id"] for entry in options if not entry["external"]] == [alias]
        for path in ("/chat-assets/chat.css", "/chat-assets/chat.js"):
            assert path in page.text
            asset = client.get(path)
            assert asset.status_code == 200
            assert not any(key in asset.text for key in keys)


@pytest.mark.skipif(not os.getenv("EAAF_E2E_URL"), reason="Requires the running three-service stack")
@pytest.mark.parametrize("space, application, model, status", [
    ("global", "global", "eaaf-mock", 200),
    ("hr", "hr", "eaaf-mock-hr", 200),
    ("it", "it", "eaaf-mock-it", 200),
    ("marketing", "marketing", "eaaf-mock-marketing", 200),
    ("hr", "global", "eaaf-mock", 403),
    ("global", "hr", "eaaf-mock", 403),
])
def test_scoped_chat_access(space, application, model, status):
    with httpx.Client(base_url=os.environ["EAAF_E2E_URL"], trust_env=False, timeout=20) as client:
        response = client.post(
            f"/chat/{space}/completions",
            headers={"Authorization": f"Bearer {application_key(application)}"},
            json={"model": model, "messages": [{"role": "user", "content": "Hello"}]},
        )
    assert response.status_code == status
    if status == 200:
        assert_mock_completion(response.json(), model, MOCKS[space][1])
    else:
        assert response.json()["detail"] == "Application key is not allowed in this chat space"


@pytest.mark.skipif(not os.getenv("EAAF_E2E_URL"), reason="Requires the running three-service stack")
@pytest.mark.parametrize("application, model, status", [
    ("hr", "eaaf-mock-hr", 200),
    ("hr", "eaaf-mock-it", 403),
    ("it", "eaaf-mock-it", 200),
    ("it", "eaaf-mock-marketing", 403),
    ("marketing", "eaaf-mock-marketing", 200),
    ("marketing", "eaaf-mock", 403),
    (None, "eaaf-mock", 401),
])
def test_application_access(application, model, status):
    headers = {}
    if application:
        key = application_key(application)
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
    assert_mock_completion(response.json(), model, MOCKS[application][1])
