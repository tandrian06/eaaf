# EAAF Gateway Architecture

## Sprint 1 request path

```mermaid
flowchart LR
    Client[HR / IT / Marketing] -->|Chat request and application key| Gateway[FastAPI EAAF Gateway / Trust Layer]
    Profiles[YAML application profiles] -->|Policies| Gateway
    Gateway <-->|Limits and audit metadata| Usage[(Local SQLite)]
    Gateway -->|HTTP with local proxy credential| Proxy[LiteLLM Proxy]
    Proxy -->|OpenAI-compatible HTTP| Mock[Mock LLM Provider]
```

- **EAAF Gateway** loads the HR, IT, and Marketing profiles from `config/applications.yaml` at startup. It authenticates the application key and checks the requested alias against that application's allowlist before routing. It forwards only supported request fields and uses its own proxy credential. It returns 502 for upstream failures and 504 for timeouts.
- **LiteLLM Proxy** maps `eaaf-mock` and `eaaf-mock-extended` to the same mock deployment using `config/litellm.yaml`. It runs as a separate service. The checked-in credentials are fixed local placeholders for the demo.
- **Mock LLM Provider** generates a unique completion ID, fixed text, and fixed simulated token counts. It does not need any model weights or external services.

Compose exposes only the gateway on `127.0.0.1:8000`. LiteLLM and the mock are reachable on an internal Compose network. The gateway health endpoint reports process availability. The end-to-end test verifies all three services together.

## Usage and audit

After authentication and model authorization, the gateway checks the daily quota and monthly budget together in a SQLite write transaction. It records the reservation before contacting the proxy, so concurrent requests cannot overspend the simulated budget. Admitted requests consume quota and fixed simulated cost even after an upstream failure. A reservation with no recorded outcome remains visible as `admitted`. This budget models local usage in integer cents.

SQLite stores timestamps, application identifiers, domain, cost center, permitted model aliases, decisions, status codes, simulated cost, and reported token totals when available. Authentication failures carry no application identity. Model denials omit the raw requested alias. Request bodies, response bodies, and credentials are excluded. Framework request-validation errors are outside the decision audit. A storage failure returns HTTP 503. If it occurs before dispatch, LiteLLM is never called.

## Sprint 2 Chat client

The Chat client lives in the top-level `chat/` directory beside the `eaaf/` gateway package. The gateway serves the Chat page and its static assets. The browser calls the same `POST /v1/chat/completions` endpoint as other clients. The application key and accepted conversation turns are held in page memory. A new request sends the accepted turns and the new message. Changing credentials clears the conversation. All authorization, quotas, budget checks, and audit decisions remain in the gateway.

RAG and agent clients can share the same gateway boundary. Each new capability can extend the client contract and governance policies independently of the provider deployment.
