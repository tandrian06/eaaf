# EAAF Governance Gateway

EAAF (Enterprise Agentic AI Framework) is a local-first gateway for enterprise AI applications. The gateway provides a common entry point intended for future Chat, RAG, and agent workloads.

## Sprint 1 · Increment 3

A non-streaming text request goes through the FastAPI gateway and a separate LiteLLM Proxy to a deterministic OpenAI-compatible mock provider. Both model aliases use that provider and return fixed content and simulated token counts.

The gateway is bound to loopback in Compose. Use synthetic input and the local demo credentials below. Runtime inference is local and uses no cloud credentials. The initial image pulls and dependency installation require registry access.

| Application | Demo bearer token | Allowed models | Requests per UTC day | Simulated USD budget per UTC month |
| --- | --- | --- | ---: | ---: |
| HR | `eaaf-local-hr` | `eaaf-mock` | 20 | $1.00 |
| IT | `eaaf-local-it` | `eaaf-mock`, `eaaf-mock-extended` | 100 | $10.00 |
| Marketing | `eaaf-local-marketing` | `eaaf-mock-extended` | 50 | $2.00 |

The profiles in [applications.yaml](config/applications.yaml) reference environment variables for their keys. Override the Compose defaults with `EAAF_HR_API_KEY`, `EAAF_IT_API_KEY`, and `EAAF_MARKETING_API_KEY`. The gateway loads policies on startup. Recreate the gateway after changing keys or policies.

## Run locally

With Docker Compose v2, run from the project root:

```sh
docker compose up --build --wait
```

In PowerShell:

```powershell
$body = @{
    model = 'eaaf-mock'
    messages = @(@{role = 'user'; content = 'Hello'})
} | ConvertTo-Json -Depth 4
$headers = @{Authorization = 'Bearer eaaf-local-hr'}
$result = Invoke-RestMethod http://127.0.0.1:8000/v1/chat/completions -Method Post -ContentType 'application/json' -Headers $headers -Body $body
$result.choices[0].message.content
```

Expected output:

```text
Hello from the EAAF mock provider.
```

In [Swagger UI](http://127.0.0.1:8000/docs), click **Authorize** and enter only the token value, for example `eaaf-local-hr`. A missing or invalid key returns HTTP 401. A valid key requesting a model outside its allowlist returns HTTP 403. An exhausted quota or insufficient simulated budget returns HTTP 429 with a distinct explanation. The `/healthz` endpoint is public.

## Usage and audit

The gateway reserves one request and a simulated $0.02 per admitted dispatch before calling LiteLLM. The reservation counts even if the upstream call fails or the gateway stops before recording the result. Denied calls cost zero. Daily quotas reset at midnight UTC and monthly budgets reset on the first day of the month in UTC.

Read the local JSON usage report from the project root.

```sh
docker compose exec -T gateway python -m eaaf.usage
```

The report groups requests, simulated costs, and reported token totals by UTC month, application, and decision. Compose stores the SQLite database in a named volume that persists across restarts and `docker compose down`. The audit stores metadata only. It omits prompts, completions, application keys, and unrecognized model strings.

## Tests

With Python 3.12 in a virtual environment:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

To include the live end-to-end test, start Compose, then set the gateway URL. In PowerShell:

```powershell
$env:EAAF_E2E_URL = 'http://127.0.0.1:8000'
python -m pytest -q
Remove-Item Env:EAAF_E2E_URL
docker compose down
```

The end-to-end tests check each application's model access, rejection of an unauthenticated call, and the mock response through the gateway. The GitHub Actions workflow builds the Compose stack and runs the same suite.

See [Architecture](docs/architecture.md) for the service boundaries and storage behavior. EAAF code is licensed under the [MIT License](LICENSE). Third-party components are distributed under their own terms.
