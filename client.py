"""
client.py — FastAPI + OpenAI + MCP Client
==========================================
Exposes REST endpoints that:
  1. Accept natural-language prompts OR structured requests.
  2. Use OpenAI (function-calling) to decide which MCP tool to call.
  3. Forward the call to the FastMCP server over SSE / JSON-RPC.
  4. Return a clean JSON response.

Run:
    pip install fastapi uvicorn httpx openai
    uvicorn client:app --reload --port 9000

Endpoints:
    POST /ask                  — Natural language prompt → OpenAI decides tool
    POST /employee/lookup      — Direct: look up an employee
    POST /ticket/create        — Direct: create a ticket
    GET  /ticket/list          — Direct: list all tickets
    GET  /health               — Health check
"""

import json
import threading
import os

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from openai import OpenAI
from pydantic import BaseModel

# ── Config ─────────────────────────────────────────────────────
MCP_BASE_URL = "http://127.0.0.1:8000"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "your-openai-api-key-here")

openai_client = OpenAI(api_key=OPENAI_API_KEY)
app = FastAPI(
    title="Ticket System API",
    description="Natural-language & direct REST API powered by OpenAI + MCP",
    version="1.0.0",
)

# ── Pydantic request models ────────────────────────────────────
class AskRequest(BaseModel):
    prompt: str

class EmployeeLookupRequest(BaseModel):
    name: str = ""
    employee_id: int = 0

class CreateTicketRequest(BaseModel):
    employee_name: str
    issue: str


# ══════════════════════════════════════════════════════════════
#  MCP CLIENT LAYER  (SSE + JSON-RPC)
# ══════════════════════════════════════════════════════════════

messages_endpoint:  str | None  = None
endpoint_ready                  = threading.Event()
received_responses: dict        = {}
response_events:    dict        = {}
_msg_id_counter                 = 100          # start high to avoid collisions


def _next_id() -> int:
    global _msg_id_counter
    _msg_id_counter += 1
    return _msg_id_counter


def _listen_sse():
    """Background thread: opens SSE stream, grabs session endpoint."""
    global messages_endpoint
    with httpx.Client(timeout=None) as client:
        with client.stream("GET", f"{MCP_BASE_URL}/sse") as stream:
            for line in stream.iter_lines():
                if not line:
                    continue
                if line.startswith("event:") and "endpoint" in line:
                    continue
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if messages_endpoint is None and data.startswith("/"):
                        messages_endpoint = MCP_BASE_URL + data
                        print(f"📡 MCP session endpoint: {messages_endpoint}")
                        endpoint_ready.set()
                        continue
                    try:
                        parsed = json.loads(data)
                        msg_id = parsed.get("id")
                        if msg_id is not None:
                            received_responses[msg_id] = parsed
                            if msg_id in response_events:
                                response_events[msg_id].set()
                    except json.JSONDecodeError:
                        pass


def _send(msg_id: int, method: str, params: dict = None) -> dict:
    """Send JSON-RPC request; block until SSE response arrives."""
    event = threading.Event()
    response_events[msg_id] = event
    httpx.post(
        messages_endpoint,
        json={
            "jsonrpc": "2.0",
            "id":      msg_id,
            "method":  method,
            "params":  params or {},
        },
        headers={"Content-Type": "application/json"},
    )
    event.wait(timeout=10)
    return received_responses.get(msg_id, {})


def _notify(method: str, params: dict = None):
    """Send JSON-RPC notification (no response expected)."""
    httpx.post(
        messages_endpoint,
        json={"jsonrpc": "2.0", "method": method, "params": params or {}},
        headers={"Content-Type": "application/json"},
    )


def _parse_tool_result(response: dict):
    """Unwrap FastMCP's { result: { content: [ { type, text } ] } } envelope."""
    try:
        content = response["result"]["content"]
        text    = next(item["text"] for item in content if item.get("type") == "text")
        return json.loads(text)
    except (KeyError, StopIteration, json.JSONDecodeError):
        return response.get("result")


def _call_mcp_tool(tool_name: str, arguments: dict):
    """High-level helper: call any MCP tool and return parsed result."""
    mid    = _next_id()
    result = _send(mid, "tools/call", {"name": tool_name, "arguments": arguments})
    return _parse_tool_result(result)


# ── Boot MCP connection on startup ─────────────────────────────
@app.on_event("startup")
def startup():
    thread = threading.Thread(target=_listen_sse, daemon=True)
    thread.start()
    ready = endpoint_ready.wait(timeout=10)
    if not ready:
        raise RuntimeError("Could not connect to MCP server at startup.")

    # MCP handshake
    init_result = _send(_next_id(), "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities":    {},
        "clientInfo":      {"name": "ticket-api-client", "version": "2.0"},
    })
    print("✅ MCP initialized:", init_result.get("result", {}).get("serverInfo"))
    _notify("notifications/initialized")


# ══════════════════════════════════════════════════════════════
#  OPENAI FUNCTION-CALLING DEFINITIONS
# ══════════════════════════════════════════════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_employee",
            "description": "Look up an employee by name or ID from the HR database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name":        {"type": "string",  "description": "Employee full name"},
                    "employee_id": {"type": "integer", "description": "Employee numeric ID"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_ticket",
            "description": "Create a support ticket for an employee with a given issue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "employee_name": {"type": "string", "description": "Full name of the employee"},
                    "issue":         {"type": "string", "description": "Description of the problem"},
                },
                "required": ["employee_name", "issue"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tickets",
            "description": "Retrieve all support tickets in the system.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

SYSTEM_PROMPT = """
You are a helpful IT support assistant for a company ticket system.
You have access to three tools:
- get_employee: look up employee details
- create_ticket: raise a support ticket
- list_tickets: show all tickets

Always use a tool to answer the user. After calling a tool and seeing its result,
provide a friendly, concise natural-language summary of the outcome.
"""


def _run_openai_agent(prompt: str) -> dict:
    """
    Send prompt to OpenAI, let it pick and call the right MCP tool,
    then return both the tool result and the AI's human-readable summary.
    """
    messages = [
        {"role": "system",  "content": SYSTEM_PROMPT},
        {"role": "user",    "content": prompt},
    ]

    # Round 1 — ask OpenAI which tool to use
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        tools=TOOLS,
        tool_choice="auto",
        messages=messages,
    )

    message      = response.choices[0].message
    tool_calls   = message.tool_calls or []
    tool_results = []

    if not tool_calls:
        return {"answer": message.content, "tool_calls": [], "tool_results": []}

    # Execute each tool call via MCP
    for tc in tool_calls:
        fn_name = tc.function.name
        fn_args = json.loads(tc.function.arguments)

        mcp_result = _call_mcp_tool(fn_name, fn_args)
        tool_results.append({"tool": fn_name, "arguments": fn_args, "result": mcp_result})

        # Feed result back into the conversation
        messages.append(message)          # assistant turn with tool_calls
        messages.append({
            "role":         "tool",
            "tool_call_id": tc.id,
            "content":      json.dumps(mcp_result),
        })

    # Round 2 — ask OpenAI to summarise the tool results
    followup = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
    )

    return {
        "answer":       followup.choices[0].message.content,
        "tool_calls":   [{"tool": t["tool"], "arguments": t["arguments"]} for t in tool_results],
        "tool_results": tool_results,
    }


# ══════════════════════════════════════════════════════════════
#  REST ENDPOINTS
# ══════════════════════════════════════════════════════════════

# ── Health check ───────────────────────────────────────────────
@app.get("/health", tags=["System"])
def health():
    """Check that the API and MCP connection are alive."""
    return {
        "status":           "ok",
        "mcp_connected":    messages_endpoint is not None,
        "mcp_endpoint":     messages_endpoint,
    }


# ── Natural-language endpoint ──────────────────────────────────
@app.post("/ask", tags=["AI"])
def ask(req: AskRequest):
    """
    Send a natural-language prompt.
    OpenAI decides which tool to call; result + summary are returned.

    Example prompts:
    - "Who is Rajesh?"
    - "Create a ticket for Ana, she can't access VPN"
    - "Show me all open tickets"
    """
    try:
        result = _run_openai_agent(req.prompt)
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Direct: employee lookup ────────────────────────────────────
@app.post("/employee/lookup", tags=["Employees"])
def employee_lookup(req: EmployeeLookupRequest):
    """
    Look up an employee directly by name or ID (bypasses OpenAI).

    Body (at least one field required):
    ```json
    { "name": "Rajesh" }
    { "employee_id": 2 }
    ```
    """
    if not req.name and not req.employee_id:
        raise HTTPException(status_code=400, detail="Provide 'name' or 'employee_id'.")
    result = _call_mcp_tool("get_employee", {
        "name":        req.name,
        "employee_id": req.employee_id,
    })
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return JSONResponse(content=result)


# ── Direct: create ticket ──────────────────────────────────────
@app.post("/ticket/create", tags=["Tickets"])
def ticket_create(req: CreateTicketRequest):
    """
    Create a support ticket directly (bypasses OpenAI).

    Body:
    ```json
    { "employee_name": "Rajesh", "issue": "cannot install Python" }
    ```
    """
    result = _call_mcp_tool("create_ticket", {
        "employee_name": req.employee_name,
        "issue":         req.issue,
    })
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return JSONResponse(content=result, status_code=201)


# ── Direct: list all tickets ───────────────────────────────────
@app.get("/ticket/list", tags=["Tickets"])
def ticket_list():
    """
    Return all support tickets directly (bypasses OpenAI).
    """
    result = _call_mcp_tool("list_tickets", {})
    return JSONResponse(content=result)


# ── Entry point ────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("client:app", host="127.0.0.1", port=9000)
