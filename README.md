# Datadog LLM Mock Lab (Docker Compose)

A deterministic LLM Observability debugging lab for Datadog.

This project creates a fully traced FastAPI application instrumented with:

- APM (ddtrace)  
- LLM Observability (LLMObs SDK)  
- Datadog Agent (sidecar)

It does not call a real LLM provider.  
Instead, it generates controlled spans so you can validate:

- Trace propagation  
- APM to LLM Observability correlation  
- Evaluation behavior (goal-completeness, toxicity, topic relevancy, sentiment, prompt injection, tool argument correctness)  
- Layer isolation (Tracer vs Agent vs UI)

---

## What you get

### Application (`llm-mock-api`)

A FastAPI service that generates:

- `chat.request` (APM span)  
- `chat_workflow` (LLM workflow span)  
- `mock_retrieval` (LLM retrieval span)  
- `mock_llm_call` (LLM model span)  

Because everything runs in the same traced context, APM and LLM Observability spans are automatically correlated.

### Datadog Agent

A sidecar container with:

- APM intake enabled  
- Log collection enabled  
- Healthcheck validation  

---

## Requirements

- Docker  
- Docker Compose  
- A valid Datadog API key  

---

## Run

### 1) Rename the environment file

Rename:

```
example
```

to:

```
.env
```

---

### 2) Configure your API key

Open `.env` and set:

```
DD_API_KEY=your_datadog_api_key_here
```

Optional configuration:

```
DD_SITE=datadoghq.com
DD_ENV=dev
DD_VERSION=0.1.0
```

---

### 3) Start the lab

```bash
docker compose up --build
```

---

## Verify health

```bash
curl http://localhost:8080/health
```

---

## Test scenarios

All tests hit:

```
http://localhost:8080/chat
```

---

### Basic chat

```bash
curl -X POST http://localhost:8080/chat \
-H "Content-Type: application/json" \
-d '{"messages":[{"role":"user","content":"hello from mock"}],"user_id":"u1","session_id":"s1"}'
```

---

### Simulate error (LLM span failure)

```bash
curl -X POST http://localhost:8080/chat \
-H "Content-Type: application/json" \
-d '{"messages":[{"role":"user","content":"fail please"}],"user_id":"u1","session_id":"s1","simulate_error":true}'
```

---

### Failure-to-answer (refusal)

```bash
curl -X POST http://localhost:8080/chat \
-H "Content-Type: application/json" \
-d '{"messages":[{"role":"user","content":"hello"}],"user_id":"u1","session_id":"s1","forced_output":"I cannot help with that."}'
```

---

### Goal-completeness (resolved)

```bash
curl -X POST http://localhost:8080/chat \
-H "Content-Type: application/json" \
-d '{"messages":[{"role":"system","content":"You are a helpful assistant."},{"role":"user","content":"Help me plan a short weekend trip and tell me the weather."}],"user_id":"u1","session_id":"s1","forced_output":"Plan: Big Sur. Day 1 coastal drive + viewpoints. Day 2 short hike + lunch. Weather this weekend: sunny and mild; bring a light jacket."}'
```

---

### Tool Argument Correctness (correct call)

```bash
curl -X POST http://localhost:8080/chat \
-H "Content-Type: application/json" \
-d '{"messages":[{"role":"user","content":"What is the weather in Paris?"}],"user_id":"u1","session_id":"s1","forced_tool_calls":[{"name":"get_weather","arguments":{"location":"Paris","unit":"celsius"}}]}'
```

---

## Where to look in Datadog

### APM

APM → Traces  

- Service: `llm-mock-api`  
- Operation: `chat.request`

Expected structure:

```
chat.request
└── chat_workflow
    ├── mock_retrieval
    └── mock_llm_call
```

---

### LLM Observability

LLM Observability → Explorer  

Application: `llm-mock`

You should see workflow, retrieval, and LLM spans with evaluations attached to the LLM span.

---

## What this lab helps you debug

### Tracer layer
- Are spans created?
- Is span input/output structured correctly?
- Are tool calls properly formatted?

### Agent layer
- Is APM intake reachable?
- Are traces being received?
- Are traces dropped?

### UI layer
- Are spans visible?
- Are APM and LLM spans correlated?
- Are evaluations running?
