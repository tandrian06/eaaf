import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from eaaf.gateway import create_app

BODY = {"model": "eaaf-mock-hr", "messages": [{"role": "user", "content": "Hello"}]}
HR_HEADERS = {"Authorization": "Bearer test-hr"}


@pytest.fixture(autouse=True)
def application_keys(monkeypatch, tmp_path):
    monkeypatch.delenv("EAAF_OPENAI_ENABLED", raising=False)
    monkeypatch.delenv("EAAF_ANTHROPIC_ENABLED", raising=False)
    monkeypatch.setenv("EAAF_GLOBAL_API_KEY", "test-global")
    monkeypatch.setenv("EAAF_HR_API_KEY", "test-hr")
    monkeypatch.setenv("EAAF_IT_API_KEY", "test-it")
    monkeypatch.setenv("EAAF_MARKETING_API_KEY", "test-marketing")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test-proxy")
    monkeypatch.setenv("EAAF_USAGE_DB", str(tmp_path / "test.sqlite3"))


def test_chat_space_rejects_another_app_key_before_routing():
    def proxy(request):
        pytest.fail("Cross-space request reached the proxy")

    with TestClient(create_app(httpx.MockTransport(proxy))) as client:
        response = client.post("/chat/global/completions", json=BODY, headers=HR_HEADERS)
    assert response.status_code == 403
    assert client.app.state.usage.report()[0]["decision"] == "chat_space_forbidden"


@pytest.mark.parametrize("path, headers, model, status", [
    ("/chat/hr/authorize", {}, "eaaf-mock-hr", 401),
    ("/chat/hr/authorize", {"Authorization": "Bearer test-it"}, "eaaf-mock-hr", 403),
    ("/chat/hr/authorize", HR_HEADERS, "eaaf-mock-it", 403),
    ("/chat/hr/authorize", HR_HEADERS, "eaaf-mock-hr", 204),
])
def test_authorizes_work_before_retrieval(path, headers, model, status):
    def proxy(request):
        pytest.fail("Authorization reached LiteLLM")

    with TestClient(create_app(httpx.MockTransport(proxy))) as client:
        response = client.post(path, json={"model": model}, headers=headers)
    assert response.status_code == status
    assert all(row["simulated_cost_cents"] == 0 for row in client.app.state.usage.report())


def test_embeddings_enforce_scope_policy_and_quota(monkeypatch):
    monkeypatch.setenv("EAAF_OPENAI_ENABLED", "true")
    calls = []
    payload = {"model": "eaaf-knowledge-embedding", "input": ["private excerpt"]}

    def proxy(request):
        assert request.url.path == "/v1/embeddings"
        assert json.loads(request.content) == payload
        calls.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1]}]})

    app = create_app(httpx.MockTransport(proxy))
    with TestClient(app) as client:
        app.state.applications[b"test-hr"].policy.requests_per_day = 1
        assert client.post("/chat/hr/embeddings", json=payload).status_code == 401
        assert client.post("/chat/it/embeddings", json=payload, headers=HR_HEADERS).status_code == 403
        assert client.post("/chat/global/embeddings", json=payload,
                           headers={"Authorization": "Bearer test-global"}).status_code == 403
        assert client.post("/chat/hr/embeddings", json=payload, headers=HR_HEADERS).status_code == 200
        assert client.post("/chat/hr/embeddings", json=payload, headers=HR_HEADERS).status_code == 429
    assert len(calls) == 1
    assert b"private excerpt" not in app.state.usage.path.read_bytes()


def test_legacy_core_chat_points_document_search_to_work():
    with TestClient(create_app()) as client:
        assert client.get("/chat/hr/options").json()["knowledge"] is False
        response = client.post("/chat/hr/completions", json={**BODY, "use_knowledge": True}, headers=HR_HEADERS)
    assert response.status_code == 403
    assert "EAAF Work" in response.json()["detail"]


@pytest.mark.parametrize("enabled, application, expected", [
    (False, "it", 403),
    (False, "global", 403),
    (True, "global", 200),
    (True, "hr", 200),
    (True, "marketing", 200),
    (True, "it", 200),
])
def test_openai_alias_requires_opt_in_and_application_policy(monkeypatch, enabled, application, expected):
    if enabled:
        monkeypatch.setenv("EAAF_OPENAI_ENABLED", "true")
    alias = "eaaf-openai-mini"
    calls = []

    def proxy(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"object": "chat.completion"})

    with TestClient(create_app(httpx.MockTransport(proxy))) as client:
        models = client.get(f"/chat/{application}/options").json()["models"]
        mock = "eaaf-mock" if application == "global" else f"eaaf-mock-{application}"
        assert models[0] == {"id": mock, "external": False}
        assert any(entry["id"] == alias for entry in models) == enabled
        response = client.post(
            f"/chat/{application}/completions",
            json={**BODY, "model": alias},
            headers={"Authorization": f"Bearer test-{application}"},
        )
    assert response.status_code == expected
    assert calls == ([alias] if expected == 200 else [])


@pytest.mark.parametrize("anthropic, openai", [(False, False), (True, False), (False, True), (True, True)])
def test_anthropic_is_opt_in_and_scoped_to_hr(monkeypatch, anthropic, openai):
    monkeypatch.setenv("EAAF_ANTHROPIC_ENABLED", str(anthropic).lower())
    monkeypatch.setenv("EAAF_OPENAI_ENABLED", str(openai).lower())
    alias = "eaaf-claude-haiku"
    calls = []

    def proxy(request):
        assert request.url.path == "/v1/chat/completions"
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"object": "chat.completion"})

    with TestClient(create_app(httpx.MockTransport(proxy))) as client:
        options = client.get("/chat/hr/options").json()
        assert options["models"][0] == {"id": "eaaf-mock-hr", "external": False}
        assert ({"id": alias, "external": True} in options["models"]) == anthropic
        assert any(model["id"] == "eaaf-openai-mini" for model in options["models"]) == openai
        assert options["embeddings"] == openai
        assert all(model["id"] != alias for model in client.get("/chat/it/options").json()["models"])
        body = {**BODY, "model": alias}
        it_headers = {"Authorization": "Bearer test-it"}
        assert client.post("/chat/it/completions", json=body, headers=it_headers).status_code == 403
        response = client.post("/chat/hr/authorize", json={"model": alias}, headers=HR_HEADERS)
        assert response.status_code == (204 if anthropic else 403)
        response = client.post("/chat/hr/completions", json=body, headers=HR_HEADERS)
        assert response.status_code == (200 if anthropic else 403)
        if not anthropic:
            assert response.json()["detail"] == "Anthropic mode is disabled"
    assert calls == ([alias] if anthropic else [])


def test_forwards_completion_to_proxy():
    def proxy(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer sk-test-proxy"
        assert json.loads(request.content) == {**BODY, "stream": False}
        return httpx.Response(200, json={"object": "chat.completion", "id": "chatcmpl-test"})

    with TestClient(create_app(httpx.MockTransport(proxy))) as client:
        response = client.post("/v1/chat/completions", json=BODY, headers=HR_HEADERS)
    assert response.status_code == 200
    assert response.json()["id"] == "chatcmpl-test"


@pytest.mark.parametrize("extra", [{"stream": True}, {"model": ""}, {"api_base": "http://other"}])
def test_rejects_unsupported_input(extra):
    def proxy(request):
        pytest.fail("Invalid request reached the proxy")

    with TestClient(create_app(httpx.MockTransport(proxy))) as client:
        response = client.post("/v1/chat/completions", json={**BODY, **extra}, headers=HR_HEADERS)
    assert response.status_code == 422


@pytest.mark.parametrize("failure, expected", [
    (httpx.Response(500, text="private upstream detail"), 502),
    (httpx.Response(200, text="private upstream detail"), 502),
    (httpx.ReadTimeout("private upstream detail"), 504),
])
def test_upstream_failures_are_sanitized(failure, expected):
    def proxy(request):
        if isinstance(failure, Exception):
            raise failure
        return failure

    with TestClient(create_app(httpx.MockTransport(proxy))) as client:
        response = client.post("/v1/chat/completions", json=BODY, headers=HR_HEADERS)
    assert response.status_code == expected
    assert "private upstream detail" not in response.text
    assert client.app.state.usage.report()[0]["decision"] == "upstream_failure"
    assert client.app.state.usage.report()[0]["simulated_cost_cents"] == 2


@pytest.mark.parametrize("authorization", ["", "Basic test-hr", "Bearer unknown", "Bearer sk-test-proxy"])
def test_rejects_unauthenticated_requests_before_routing(authorization):
    def proxy(request):
        pytest.fail("Unauthenticated request reached the proxy")

    with TestClient(create_app(httpx.MockTransport(proxy))) as client:
        headers = {"Authorization": authorization} if authorization else {}
        response = client.post("/v1/chat/completions", json=BODY, headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("model", ["eaaf-mock-it", "unknown"])
def test_rejects_forbidden_models_before_routing(model):
    def proxy(request):
        pytest.fail("Forbidden request reached the proxy")

    with TestClient(create_app(httpx.MockTransport(proxy))) as client:
        response = client.post("/v1/chat/completions", json={**BODY, "model": model}, headers=HR_HEADERS)
    assert response.status_code == 403


@pytest.mark.parametrize("key", ["", "test-hr"])
def test_startup_rejects_missing_or_duplicate_keys(monkeypatch, key):
    monkeypatch.setenv("EAAF_IT_API_KEY", key)
    with pytest.raises(ValueError, match="Set a valid bearer token|Application keys must be unique"):
        with TestClient(create_app()):
            pytest.fail("Gateway started with invalid credentials")


@pytest.mark.parametrize("limits, detail", [
    ({"requests_per_day": 1}, "Daily request quota exceeded"),
    ({"monthly_budget_cents": 2}, "Monthly simulated budget exceeded"),
])
def test_limit_blocks_before_routing_and_survives_restart(limits, detail):
    calls = []

    def proxy(request):
        calls.append(request)
        return httpx.Response(200, json={"object": "chat.completion"})

    app = create_app(httpx.MockTransport(proxy))
    for expected in (200, 429):
        with TestClient(app) as client:
            policy = app.state.applications[b"test-hr"].policy
            for field, value in limits.items():
                setattr(policy, field, value)
            response = client.post("/v1/chat/completions", json=BODY, headers=HR_HEADERS)
            assert response.status_code == expected
    assert response.json()["detail"] == detail
    assert len(calls) == 1


def test_audit_retains_metadata_and_excludes_request_and_response_content():
    body = {"model": "eaaf-mock-hr", "messages": [{"role": "user", "content": "private-prompt-marker"}]}

    def proxy(request):
        return httpx.Response(200, json={
            "object": "chat.completion",
            "choices": [{"message": {"content": "private-completion-marker"}}],
            "usage": {"total_tokens": 13},
        })

    app = create_app(httpx.MockTransport(proxy))
    with TestClient(app) as client:
        assert client.post("/v1/chat/completions", json=body, headers=HR_HEADERS).status_code == 200
        assert client.post("/v1/chat/completions", json=body).status_code == 401
        forbidden = {**body, "model": "private-model-marker"}
        assert client.post("/v1/chat/completions", json=forbidden, headers=HR_HEADERS).status_code == 403

    with sqlite3.connect(app.state.usage.path) as db:
        rows = db.execute("""
            SELECT application, cost_center, model, decision, status_code, cost_cents, total_tokens
            FROM events ORDER BY id
        """).fetchall()
    assert rows == [
        ("hr", "DEMO-HR", "eaaf-mock-hr", "completed", 200, 2, 13),
        (None, None, None, "unauthenticated", 401, 0, None),
        ("hr", "DEMO-HR", None, "model_forbidden", 403, 0, None),
    ]
    contents = app.state.usage.path.read_bytes()
    for secret in (b"private-prompt-marker", b"private-completion-marker", b"private-model-marker", b"test-hr"):
        assert secret not in contents
