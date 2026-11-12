# EAAF Gateway Architecture

## Governed request path

```mermaid
flowchart LR
    Work["AI for Work · separate application"] -->|"Application key and scoped Chat API"| Gateway

    subgraph Core["EAAF Core"]
        Profiles["YAML application policies"] --> Gateway["Governance Gateway"]
        Gateway <-->|"Limits and audit metadata"| Usage[("Local SQLite")]
        Gateway -->|"Authorized model alias"| Proxy["LiteLLM Proxy"]
    end

    Proxy --> Mock["Local mock provider"]
    Proxy -.-> OpenAI["OpenAI · optional"]
    Proxy -.-> Anthropic["Anthropic · optional"]

    style Core fill:#e8f6ee,stroke:#37805b,color:#14243a
```

- **EAAF Gateway** loads the HR, IT, and Marketing profiles from `config/applications.yaml` at startup. It authenticates the application key and checks the requested alias against that application's allowlist before routing. It forwards only supported request fields and uses its own proxy credential. It returns 502 for upstream failures and 504 for timeouts.
- **LiteLLM Proxy** routes the four space-specific mock aliases to one local provider using `config/litellm.yaml`. The optional OpenAI overlay adds a real model for Global, HR, IT, and Marketing. The independent Anthropic overlay adds Claude Haiku for the HR application. Core checks provider activation and the application allowlist for each request. Provider API keys are supplied only to LiteLLM. The Gateway and Proxy share a master key supplied through their runtime environment. Local keys are generated in the Git-ignored `.env` file.
- **Mock LLM Provider** generates a unique completion ID, fixed simulated token counts, and a reply that names its space and model alias. It does not need any model weights or external services.

Compose exposes only the gateway on `127.0.0.1:8000`. LiteLLM and the mock are reachable on an internal Compose network. The gateway health endpoint reports process availability. The end-to-end test verifies all three services together.

## Usage and audit

After authentication and model authorization, the gateway checks the daily quota and monthly budget together in a SQLite write transaction. It records the reservation before contacting the proxy, so concurrent requests cannot overspend the simulated budget. Admitted requests consume quota and fixed simulated cost even after an upstream failure. A reservation with no recorded outcome remains visible as `admitted`. This budget models local usage in integer cents.

SQLite stores timestamps, application identifiers, domain, cost center, permitted model aliases, decisions, status codes, simulated cost, and reported token totals when available. Authentication failures carry no application identity. Model denials omit the raw requested alias. Request bodies, response bodies, and credentials are excluded. Framework request-validation errors are outside the decision audit. A storage failure returns HTTP 503. If it occurs before dispatch, LiteLLM is never called.

## Sprint 2 Chat client

The standalone Chat client lives in [EAAF Work](https://github.com/tandrian06/eaaf-work). Work serves the browser assets and forwards scoped Chat requests to Core. Core still bundles a copy in `chat/` for the original local Compose demo. Work forwards the application key and Core verifies it. Department retrieval runs in Work. One frontend serves Global, HR, IT, and Marketing spaces. Chat submits to `POST /chat/{space}/completions`, which checks that the authenticated application matches the space and then runs the same completion handler as `POST /v1/chat/completions`. Global has its own key, allowlist, and limits. The key and accepted conversation turns are held in page memory and cleared when leaving the space. All authorization, quotas, budget checks, and audit decisions remain in the gateway.

## Department knowledge in EAAF Work

[EAAF Work](https://github.com/tandrian06/eaaf-work) owns the department documents, keyword retrieval, and in-memory semantic index. Before searching, Work sends the application key and selected model to `POST /chat/{space}/authorize`. Core authenticates the key and checks the application matches the department and is allowed to use the model. A denial stops retrieval.

Work adds matching excerpts to a scoped completion request and returns source metadata to the browser. For semantic retrieval, Work sends embedding requests to `POST /chat/{space}/embeddings`. Core separately authorizes and meters each embedding batch and each generation request. The authorization check alone consumes no quota. Core records usage metadata and excludes questions, excerpts, and vectors from audit.

## AI for DevOps integration

[AI for DevOps](https://github.com/tandrian06/eaaf-devops) is the AWS-first Architecture-to-Terraform module. It owns the versioned architecture schema, ECS Fargate pattern validation, and review output. A structured YAML manifest can be reviewed entirely within the DevOps CLI.

For natural-language intake, the CLI calls `POST /v1/chat/completions` with a dedicated application key. The DevOps repository supplies an optional Compose overlay that registers the DevOps application policy and its architecture model alias. The Gateway authenticates the request, enforces the allowlist and usage limits, and records audit metadata. LiteLLM routes the authorized request to OpenAI. The CLI validates the resulting draft and reports open decisions and pattern conflicts.

The DevOps CLI can inspect a prepared Terraform module and generate a module call and input file from reviewed YAML. The [deployment demo](https://github.com/tandrian06/eaaf-deployment-demo) exercises that path locally and through GitHub Actions. Its AWS workflow supports a reviewed plan and a manually triggered apply.

## AI for Ops integration

[AI for Ops](https://github.com/tandrian06/eaaf-ops) diagnoses ECS incidents from JSON exports or live service events and stopped-task data. AWS access uses the operator's read-only AWS CLI profile. The diagnostic rules run in the Ops CLI and cite matching evidence. The CLI can send the report to Core's completion endpoint for an optional model explanation under an authorized application policy.

```mermaid
flowchart LR
    ECS["ECS · read-only collection"] --> Ops["AI for Ops"]
    Capture["Saved ECS exports"] --> Ops
    Ops --> Report["Finding, evidence, and next check"]
    Ops -->|"Optional explanation"| Gateway["EAAF Core Gateway"]
```
