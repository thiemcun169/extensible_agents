"""
Production-grade MCP (Model Context Protocol) framework for educational labs.

Implements the three MCP server primitives (Tools, Resources, Prompts) with:
  - Bearer-token authentication (simulates OAuth 2.1 per MCP spec)
  - Role-based access control per tool / resource
  - SQL query tool with table/column allow-lists
  - Input validation and output sanitization
  - Rate limiting, call logging, correlation IDs
  - A client that bridges MCP tool definitions to OpenAI function-calling

In production, use the official ``mcp`` Python SDK from Anthropic.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from openai import OpenAI

logger = logging.getLogger("mcp_framework")


# ═══════════════════════════════════════════════════════════════════
# Authentication — simplified OAuth 2.1 / Bearer token layer
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MCPToken:
    """A bearer token with scoped roles and expiry."""
    token: str
    client_id: str
    roles: set[str]           # e.g. {"analyst", "admin"}
    scopes: set[str]          # e.g. {"tools:read", "resources:read"}
    expires_at: float         # unix timestamp

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at


class MCPAuthProvider:
    """Token issuer / validator.  Simulates an OAuth 2.1 authorization server.

    In production this would be a real OAuth server (Auth0, Keycloak, etc.)
    implementing the MCP authorization spec with PKCE, dynamic client
    registration, and scoped resource indicators (RFC 8707).
    """

    def __init__(self, token_ttl: int = 3600):
        self._tokens: dict[str, MCPToken] = {}
        self._client_secrets: dict[str, str] = {}   # client_id -> secret
        self.token_ttl = token_ttl

    # ── client registration (simulates RFC 7591 dynamic registration) ──
    def register_client(self, client_id: str, client_secret: str,
                        roles: set[str] | None = None):
        self._client_secrets[client_id] = client_secret
        logger.info("auth | registered client %s", client_id)

    # ── issue token (client-credentials grant) ─────────────────────────
    def issue_token(self, client_id: str, client_secret: str,
                    requested_scopes: set[str] | None = None,
                    roles: set[str] | None = None) -> MCPToken | dict:
        stored = self._client_secrets.get(client_id)
        if stored is None or stored != client_secret:
            return {"error": "invalid_client", "status": 401}

        token_str = secrets.token_urlsafe(32)
        tk = MCPToken(
            token=token_str,
            client_id=client_id,
            roles=roles or {"public"},
            scopes=requested_scopes or {"tools:read", "tools:call", "resources:read"},
            expires_at=time.time() + self.token_ttl,
        )
        self._tokens[token_str] = tk
        return tk

    # ── validate ───────────────────────────────────────────────────────
    def validate(self, token_str: str) -> MCPToken | None:
        tk = self._tokens.get(token_str)
        if tk is None or tk.is_expired:
            return None
        return tk


# ═══════════════════════════════════════════════════════════════════
# MCP Server Primitives
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MCPTool:
    name: str
    description: str
    parameters: dict          # JSON-Schema
    handler: Callable
    allowed_roles: set[str] = field(default_factory=lambda: {"public"})
    required_scope: str | None = None       # OAuth-style scope (e.g. "sql:execute")

@dataclass
class MCPResource:
    uri: str
    name: str
    description: str
    mime_type: str
    handler: Callable
    allowed_roles: set[str] = field(default_factory=lambda: {"public"})
    required_scope: str | None = None

@dataclass
class MCPPrompt:
    name: str
    description: str
    template: str


class MCPServer:
    """Production-grade MCP Server with auth, ACL, rate-limiting, logging."""

    def __init__(self, name: str, version: str = "1.0",
                 auth_provider: MCPAuthProvider | None = None):
        self.name = name
        self.version = version
        self.auth = auth_provider
        self.tools: dict[str, MCPTool] = {}
        self.resources: dict[str, MCPResource] = {}
        self.prompts: dict[str, MCPPrompt] = {}
        self._call_log: list[dict] = []
        self._rate_limits: dict[str, list[float]] = {}
        self._rate_limit_max: int = 0
        self._rate_limit_window: float = 60.0
        self._input_validators: dict[str, Callable] = {}

    # ── helpers ────────────────────────────────────────────────────

    def _check_auth(self, token_str: str | None,
                    required_roles: set[str]) -> MCPToken | dict:
        """Validate bearer token and check role membership."""
        if self.auth is None:
            # Auth disabled — return a synthetic admin token
            return MCPToken(token="__noauth__", client_id="local",
                            roles={"admin", "analyst", "public"},
                            scopes={"tools:call", "resources:read"},
                            expires_at=time.time() + 9999)
        if not token_str:
            return {"error": "unauthorized", "message": "Bearer token required",
                    "status": 401}
        tk = self.auth.validate(token_str)
        if tk is None:
            return {"error": "unauthorized",
                    "message": "Invalid or expired token", "status": 401}
        if "public" not in required_roles and not tk.roles & required_roles:
            return {"error": "forbidden",
                    "message": f"Requires roles: {required_roles}",
                    "status": 403}
        return tk

    # ── registration decorators ────────────────────────────────────

    def tool(self, name: str, description: str, parameters: dict,
             allowed_roles: set[str] | None = None,
             required_scope: str | None = None):
        def decorator(func: Callable):
            self.tools[name] = MCPTool(
                name=name, description=description,
                parameters=parameters, handler=func,
                allowed_roles=allowed_roles or {"public"},
                required_scope=required_scope,
            )
            return func
        return decorator

    def resource(self, uri: str, name: str, description: str,
                 mime_type: str = "text/plain",
                 allowed_roles: set[str] | None = None,
                 required_scope: str | None = None):
        def decorator(func: Callable):
            self.resources[uri] = MCPResource(
                uri=uri, name=name, description=description,
                mime_type=mime_type, handler=func,
                allowed_roles=allowed_roles or {"public"},
                required_scope=required_scope,
            )
            return func
        return decorator

    def prompt(self, name: str, description: str, template: str):
        self.prompts[name] = MCPPrompt(name=name, description=description,
                                       template=template)

    # ── discovery ──────────────────────────────────────────────────

    def _visible(self, primitive, auth: "MCPToken") -> bool:
        """Return True if the caller's token has both role + scope access."""
        role_ok = ("public" in primitive.allowed_roles
                    or bool(auth.roles & primitive.allowed_roles))
        scope_ok = (primitive.required_scope is None
                     or primitive.required_scope in auth.scopes)
        return role_ok and scope_ok

    def list_tools(self, token: str | None = None) -> list[dict]:
        """Return tools visible to the token's roles AND scopes."""
        auth = self._check_auth(token, {"public"})
        if isinstance(auth, dict):
            return []
        return [
            {"name": t.name, "description": t.description,
             "parameters": t.parameters}
            for t in self.tools.values() if self._visible(t, auth)
        ]

    def list_resources(self, token: str | None = None) -> list[dict]:
        auth = self._check_auth(token, {"public"})
        if isinstance(auth, dict):
            return []
        return [
            {"uri": r.uri, "name": r.name, "description": r.description,
             "mime_type": r.mime_type}
            for r in self.resources.values() if self._visible(r, auth)
        ]

    def list_prompts(self) -> list[dict]:
        return [{"name": p.name, "description": p.description}
                for p in self.prompts.values()]

    # ── execution ──────────────────────────────────────────────────

    def call_tool(self, name: str, arguments: dict,
                  token: str | None = None,
                  correlation_id: str | None = None) -> dict:
        cid = correlation_id or str(uuid.uuid4())[:8]

        if name not in self.tools:
            return {"error": f"Unknown tool: {name}"}

        tool = self.tools[name]

        # Auth — roles
        auth = self._check_auth(token, tool.allowed_roles)
        if isinstance(auth, dict):
            return auth

        # Auth — scope (if tool declares one it must be in the token)
        if tool.required_scope and tool.required_scope not in auth.scopes:
            return {"error": "forbidden",
                    "message": f"Tool '{name}' requires scope "
                               f"'{tool.required_scope}' (granted: {sorted(auth.scopes)})",
                    "status": 403}

        # Rate limiting
        if self._rate_limit_max > 0:
            now = time.time()
            ts = self._rate_limits.setdefault(name, [])
            ts[:] = [t for t in ts if now - t < self._rate_limit_window]
            if len(ts) >= self._rate_limit_max:
                return {"error": "rate_limit_exceeded",
                        "message": f"Max {self._rate_limit_max} calls per "
                                   f"{self._rate_limit_window}s for '{name}'"}
            ts.append(now)

        # Input validation
        if name in self._input_validators:
            err = self._input_validators[name](arguments)
            if err:
                return {"error": "validation_failed", "message": err}

        # Execute
        t0 = time.time()
        try:
            result = tool.handler(**arguments)
        except Exception as exc:
            result = {"error": str(exc)}
        duration_ms = (time.time() - t0) * 1000

        entry = {"tool": name, "arguments": arguments,
                 "result_preview": str(result)[:300],
                 "correlation_id": cid,
                 "client_id": auth.client_id if isinstance(auth, MCPToken) else "?",
                 "duration_ms": round(duration_ms, 2),
                 "timestamp": time.time()}
        self._call_log.append(entry)
        logger.info("tool_call | cid=%s | %s | %s", cid, name,
                     json.dumps(arguments))
        return result

    def read_resource(self, uri: str, token: str | None = None) -> str | dict:
        if uri not in self.resources:
            return {"error": f"Unknown resource: {uri}"}
        res = self.resources[uri]
        auth = self._check_auth(token, res.allowed_roles)
        if isinstance(auth, dict):
            return auth
        if res.required_scope and res.required_scope not in auth.scopes:
            return {"error": "forbidden",
                    "message": f"Resource '{uri}' requires scope "
                               f"'{res.required_scope}' (granted: {sorted(auth.scopes)})",
                    "status": 403}
        return res.handler()

    def render_prompt(self, prompt_name: str, **kwargs: str) -> str | dict:
        if prompt_name not in self.prompts:
            return {"error": f"Unknown prompt: {prompt_name}"}
        return self.prompts[prompt_name].template.format(**kwargs)

    # ── security helpers ──────────────────────────────────────────

    def set_rate_limit(self, max_calls: int, window_seconds: float = 60.0):
        self._rate_limit_max = max_calls
        self._rate_limit_window = window_seconds

    def add_input_validator(self, tool_name: str, validator: Callable):
        self._input_validators[tool_name] = validator

    def get_call_log(self) -> list[dict]:
        return list(self._call_log)


# ═══════════════════════════════════════════════════════════════════
# SQL Query Tool Builder — creates a safe, sandboxed SQL tool
# ═══════════════════════════════════════════════════════════════════

# Patterns that must never appear in user-supplied SQL
_DANGEROUS_SQL = re.compile(
    r"""(?ix)
    \b(DROP|ALTER|CREATE|INSERT|UPDATE|DELETE|TRUNCATE|REPLACE|ATTACH|DETACH)\b
    | --           # comment
    | /\*          # block comment
    | ;(?!\s*$)    # multi-statement (semicolon not at end)
    """,
)


def build_sql_tool(
    db_path: str,
    allowed_tables: set[str],
    blocked_tables: set[str] | None = None,
    pii_columns: set[str] | None = None,
    max_rows: int = 50,
) -> Callable:
    """Return a handler function for a safe read-only SQL tool.

    Security:
      - Only SELECT allowed
      - Table allow-list enforced
      - PII columns masked
      - Row limit enforced
      - Dangerous patterns rejected
    """
    blocked = blocked_tables or set()
    pii = pii_columns or set()

    def execute_sql(query: str) -> dict:
        q = query.strip().rstrip(";")

        # Block dangerous patterns
        if _DANGEROUS_SQL.search(q):
            return {"error": "Only SELECT queries are allowed. "
                            "DDL/DML statements are blocked."}

        if not q.upper().startswith("SELECT"):
            return {"error": "Only SELECT queries are allowed."}

        # Check table references against allow/block lists
        q_upper = q.upper()
        for tbl in blocked:
            if tbl.upper() in q_upper:
                return {"error": f"Access to table '{tbl}' is restricted."}

        found_allowed = False
        for tbl in allowed_tables:
            if tbl.upper() in q_upper:
                found_allowed = True
                break
        if not found_allowed:
            return {"error": f"Query must reference one of: "
                            f"{', '.join(sorted(allowed_tables))}"}

        # Execute with row limit
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(q)
            rows = cursor.fetchmany(max_rows)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            results = []
            for row in rows:
                d = dict(row)
                # Mask PII columns
                for col in pii:
                    if col in d and d[col]:
                        val = str(d[col])
                        d[col] = val[:2] + "***" + val[-2:] if len(val) > 4 else "***"
                results.append(d)
            total = len(results)
            conn.close()
            return {"columns": columns, "rows": results, "row_count": total,
                    "truncated": total >= max_rows}
        except sqlite3.Error as e:
            return {"error": f"SQL error: {e}"}

    return execute_sql


# ═══════════════════════════════════════════════════════════════════
# Input Sanitisation Helpers
# ═══════════════════════════════════════════════════════════════════

INJECTION_PATTERNS = [
    (re.compile(r"(?i)ignore\s+(all\s+)?previous\s+instructions"), "prompt_injection"),
    (re.compile(r"(?i)you\s+are\s+now\s+a"), "role_hijack"),
    (re.compile(r"(?i)override[s]?\s+(all\s+)?safety"), "safety_override"),
    (re.compile(r"(?i)reveal\s+(all\s+)?(system\s+)?prompts?"), "prompt_leak"),
    (re.compile(r"(?i)<script[\s>]"), "xss"),
    (re.compile(r"\.\./"), "path_traversal"),
]


def check_injection(text: str) -> list[str]:
    """Return list of detected threat labels (empty = clean)."""
    return [label for pat, label in INJECTION_PATTERNS if pat.search(text)]


def sanitize_tool_output(text: str) -> str:
    """Strip injection patterns from tool output before sending to LLM."""
    result = text
    for pat, label in INJECTION_PATTERNS:
        result = pat.sub(f"[REDACTED:{label}]", result)
    return result


# ═══════════════════════════════════════════════════════════════════
# MCP Client — bridges MCPServer <-> OpenAI function-calling
# ═══════════════════════════════════════════════════════════════════

class MCPClient:
    """Connects to an MCPServer and drives an OpenAI agent loop."""

    def __init__(self, server: MCPServer, openai_client: OpenAI,
                 model: str = "gpt-4.1-nano",
                 system_prompt: str | None = None,
                 token: str | None = None,
                 sanitize: bool = True):
        self.server = server
        self.openai = openai_client
        self.model = model
        self.token = token
        self.sanitize = sanitize
        self.system_prompt = system_prompt or (
            f"You are a helpful assistant connected to the '{server.name}' "
            f"MCP server. Use the available tools to answer accurately."
        )

    def _openai_tools(self) -> list[dict]:
        return [
            {"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["parameters"]}}
            for t in self.server.list_tools(self.token)
        ]

    def chat(self, user_message: str, max_rounds: int = 8,
             verbose: bool = True) -> str:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]
        tools = self._openai_tools()
        cid = str(uuid.uuid4())[:8]

        for _ in range(max_rounds):
            resp = self.openai.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools if tools else None,
            )
            choice = resp.choices[0]

            if choice.finish_reason != "tool_calls" or not choice.message.tool_calls:
                return choice.message.content or ""

            messages.append(choice.message)
            for tc in choice.message.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments)
                if verbose:
                    print(f"  [MCP] {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:120]})")
                result = self.server.call_tool(fn_name, fn_args,
                                               token=self.token,
                                               correlation_id=cid)
                result_str = json.dumps(result, ensure_ascii=False)
                if self.sanitize:
                    result_str = sanitize_tool_output(result_str)
                if verbose:
                    preview = result_str[:200] + ("..." if len(result_str) > 200 else "")
                    print(f"  [MCP] -> {preview}")
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": result_str})
        return "(max tool-call rounds reached)"


# ═══════════════════════════════════════════════════════════════════
# Utility helpers
# ═══════════════════════════════════════════════════════════════════

def print_tool_definitions(server: MCPServer, token: str | None = None):
    for t in server.list_tools(token):
        print(f"\n  Tool: {t['name']}")
        print(f"  Desc: {t['description']}")
        props = t["parameters"].get("properties", {})
        for pn, pd in props.items():
            req = pn in t["parameters"].get("required", [])
            print(f"    - {pn}: {pd.get('type','?')} "
                  f"{'(req)' if req else '(opt)'} "
                  f"— {pd.get('description','')}")
