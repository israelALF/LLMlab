import os
import json
import random
import time
from typing import Optional, List, Literal, Dict, Any

# APM instrumentation (creates the HTTP request span so you can correlate with LLM Obs)
from ddtrace import patch_all, tracer

# Patch common libraries (FastAPI/Starlette will be instrumented when you run uvicorn)
patch_all()

from fastapi import FastAPI
from pydantic import BaseModel

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs.decorators import workflow, llm, retrieval

# -----------------------------------------------------------------------------
# LLM Mock API (lab)
#
# Goals:
# 1) Generate LLM Observability spans (workflow, retrieval, llm)
# 2) Support evaluator testing by forcing text outputs OR tool calls
# 3) Correlate with APM by having a real HTTP span around /chat
#
# How correlation works:
# - The /chat endpoint creates an APM span (chat.request)
# - Inside it we create LLM Obs spans (chat_workflow -> mock_retrieval -> mock_llm_call)
# - Because everything happens in the same traced context, Datadog can link them
# -----------------------------------------------------------------------------

app = FastAPI(title="LLM Mock API", version=os.getenv("DD_VERSION", "0.1.0"))

# These become tags on LLM spans
PROVIDER = os.getenv("LLM_PROVIDER", "mock")
MODEL = os.getenv("LLM_MODEL", "mock-1")

# Service name for the APM span (also used by ddtrace-run if you use it)
DD_SERVICE = os.getenv("DD_SERVICE", "llm-mock-api")

# -----------------------------------------------------------------------------
# Tool schemas (needed for Tool Argument Correctness evaluators)
# Keep this minimal, but accurate.
# -----------------------------------------------------------------------------
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
                "required": ["location"],
                "additionalProperties": True,
            },
        },
    }
]

# -----------------------------------------------------------------------------
# Request/Response models
# -----------------------------------------------------------------------------
class ChatMessage(BaseModel):
    # One message in a conversation.
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    # Conversation input (required).
    messages: List[ChatMessage]

    # Force a plain text assistant response.
    forced_output: Optional[str] = None

    # Force tool calls (for Tool Argument Correctness).
    # You can send either:
    #   [{"name":"get_weather","arguments":{"location":"Paris","unit":"celsius"}}]
    # or OpenAI-like:
    #   [{"type":"function","function":{"name":"get_weather","arguments":"{\"location\":\"Paris\"}"}}]
    forced_tool_calls: Optional[List[Dict[str, Any]]] = None

    # Raise an exception to simulate an errored LLM call/span.
    simulate_error: bool = False

    # Optional identifiers (useful for debugging/correlation)
    user_id: Optional[str] = None
    session_id: Optional[str] = None

    # Latency simulation to make traces look realistic.
    min_latency_ms: int = 200
    max_latency_ms: int = 800


class ChatResponse(BaseModel):
    output: str
    provider: str
    model: str
    latency_ms: int


@app.get("/health")
def health():
    return {"ok": True}


# -----------------------------------------------------------------------------
# Helpers: tool-call normalization
# -----------------------------------------------------------------------------
def _normalize_tool_calls(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize into:
      [{"name": <str>, "arguments": <dict or string>}, ...]
    """
    normalized: List[Dict[str, Any]] = []

    for item in raw:
        name = None
        args: Any = None

        # Shape A: {"name": "...", "arguments": ...}
        if isinstance(item, dict) and "name" in item:
            name = item.get("name")
            args = item.get("arguments")

        # Shape B: {"function": {"name": "...", "arguments": ...}}
        elif isinstance(item, dict) and "function" in item and isinstance(item["function"], dict):
            name = item["function"].get("name")
            args = item["function"].get("arguments")

        # If args is a JSON string, parse it into a dict when possible
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                # keep it as a string if not valid JSON
                pass

        if args is None:
            args = {}

        normalized.append({"name": name, "arguments": args})

    return normalized


def _to_openai_tool_calls(normalized: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert to OpenAI-style tool_calls:
      [
        {
          "id": "call_0",
          "type": "function",
          "function": {"name": "...", "arguments": "{\"k\":\"v\"}"}
        }
      ]
    """
    out: List[Dict[str, Any]] = []
    for i, tc in enumerate(normalized):
        name = tc.get("name")
        args = tc.get("arguments", {})

        # OpenAI expects "arguments" as a JSON string
        if isinstance(args, dict):
            args_str = json.dumps(args)
        else:
            args_str = str(args)

        out.append(
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": name, "arguments": args_str},
            }
        )
    return out


# -----------------------------------------------------------------------------
# Retrieval span (simulates a RAG lookup)
# -----------------------------------------------------------------------------
@retrieval(name="mock_retrieval")
def do_retrieval(input_messages: List[dict]):
    """
    Simulates retrieval and creates a retrieval span.
    """
    time.sleep(0.03)

    docs = [{"id": "doc-1", "text": "mock doc", "score": 0.99}]
    LLMObs.annotate(input_data={"messages": input_messages}, output_data=docs)
    return docs


# -----------------------------------------------------------------------------
# LLM span (simulates a model call)
# -----------------------------------------------------------------------------
@llm(model_name=MODEL, model_provider=PROVIDER, name="mock_llm_call")
def do_llm_call(
    input_messages: List[dict],
    forced_output: Optional[str],
    forced_tool_calls: Optional[List[Dict[str, Any]]],
    simulate_error: bool,
    min_ms: int,
    max_ms: int,
):
    """
    Creates the LLM span.
    Evaluators typically look at:
      - span input: messages (and sometimes tools)
      - span output: assistant message content OR tool_calls
    """
    latency_ms = random.randint(max(0, min_ms), max(min_ms, max_ms))
    time.sleep(latency_ms / 1000.0)

    # Simulate a provider error (span will be errored)
    if simulate_error:
        raise RuntimeError("Simulated LLM provider error (mock)")

    # If tool calls are forced, emit a tool-call turn
    if forced_tool_calls is not None:
        normalized = _normalize_tool_calls(forced_tool_calls)
        openai_tool_calls = _to_openai_tool_calls(normalized)

        # Keep content non-empty so other evaluators do not treat it as "no content"
        assistant_msg: Dict[str, Any] = {
            "role": "assistant",
            "content": "Calling tool.",
            "tool_calls": openai_tool_calls,
        }

        # Important: put schemas in the SPAN INPUT so templates that use {{span_input}} can see them
        LLMObs.annotate(
            input_data={"messages": input_messages, "tools": TOOL_SCHEMAS},
            output_data=[assistant_msg],
            metadata={"mock": True},
            tags={"llm.provider": PROVIDER, "llm.model": MODEL},
        )

        # The HTTP response still returns a string
        return "Calling tool.", latency_ms

    # Otherwise emit a normal text turn
    output_text = forced_output if forced_output is not None else "MOCK RESPONSE"
    assistant_msg = {"role": "assistant", "content": output_text}

    LLMObs.annotate(
        input_data={"messages": input_messages, "tools": TOOL_SCHEMAS},
        output_data=[assistant_msg],
        metadata={"mock": True},
        tags={"llm.provider": PROVIDER, "llm.model": MODEL},
    )

    return output_text, latency_ms


# -----------------------------------------------------------------------------
# Workflow span (wraps the whole request)
# -----------------------------------------------------------------------------
@workflow(name="chat_workflow")
def handle_chat(req: ChatRequest):
    """
    Creates a workflow span and child spans for retrieval + llm.
    """
    input_messages = [m.model_dump() for m in req.messages]

    do_retrieval(input_messages)

    output, latency_ms = do_llm_call(
        input_messages=input_messages,
        forced_output=req.forced_output,
        forced_tool_calls=req.forced_tool_calls,
        simulate_error=req.simulate_error,
        min_ms=req.min_latency_ms,
        max_ms=req.max_latency_ms,
    )

    # Helpful summary attached to the workflow span
    LLMObs.annotate(
        input_data={
            "user_id": req.user_id,
            "session_id": req.session_id,
            "messages": input_messages,
            "simulate_error": req.simulate_error,
            "forced_output": req.forced_output,
            "forced_tool_calls": req.forced_tool_calls,
        },
        output_data={"output": output, "latency_ms": latency_ms},
    )

    return output, latency_ms


# -----------------------------------------------------------------------------
# HTTP endpoint (APM span lives here)
# -----------------------------------------------------------------------------
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """
    This creates an APM span around the request so you can correlate:
      APM span (chat.request) -> LLM Obs spans inside it
    """
    with tracer.trace("chat.request", service=DD_SERVICE):
        output, latency_ms = handle_chat(req)
        return ChatResponse(output=output, provider=PROVIDER, model=MODEL, latency_ms=latency_ms)
