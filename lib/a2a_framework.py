"""
Production-grade A2A (Agent-to-Agent) framework for educational labs.

Implements the A2A protocol with:
  - AgentCard with security schemes (API-key & OAuth client-credentials)
  - Task lifecycle with full history
  - Authenticated client / remote agents
  - Agent registry with discovery

Security model follows the A2A spec:
  - Agent Cards declare their security scheme (apiKey or oauth2)
  - Client agents obtain tokens before submitting tasks
  - Remote agents validate tokens on every request

In production, use Google's official A2A SDK or similar.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger("a2a_framework")


# ═══════════════════════════════════════════════════════════════════
# Task Status Lifecycle
# ═══════════════════════════════════════════════════════════════════

class TaskStatus(str, Enum):
    SUBMITTED      = "submitted"
    WORKING        = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED      = "completed"
    FAILED         = "failed"


# ═══════════════════════════════════════════════════════════════════
# Security Scheme (declared in Agent Card)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SecurityScheme:
    """OpenAPI-style security scheme attached to an Agent Card."""
    scheme_type: str = "apiKey"         # "apiKey" | "oauth2"
    # For apiKey
    api_key_header: str = "X-API-Key"
    # For oauth2 (client-credentials)
    token_url: str = ""
    scopes: list[str] = field(default_factory=list)


class A2AAuthProvider:
    """Validates credentials for A2A communication.

    Supports two modes matching the A2A spec:
      1. API key  — simple shared secret
      2. OAuth 2.0 client-credentials — client_id + secret -> JWT-like token
    """

    def __init__(self):
        self._api_keys: dict[str, dict] = {}          # key -> {"client": ..., "roles": ...}
        self._oauth_clients: dict[str, str] = {}       # client_id -> secret
        self._tokens: dict[str, dict] = {}             # token -> {"client_id", "expires_at", "scopes"}

    # ── API-key management ─────────────────────────────────────────
    def register_api_key(self, key: str, client_name: str, roles: set[str] | None = None):
        self._api_keys[key] = {"client": client_name, "roles": roles or {"agent"}}

    def validate_api_key(self, key: str) -> dict | None:
        return self._api_keys.get(key)

    # ── OAuth client-credentials ───────────────────────────────────
    def register_oauth_client(self, client_id: str, client_secret: str):
        self._oauth_clients[client_id] = client_secret

    def issue_oauth_token(self, client_id: str, client_secret: str,
                          scopes: list[str] | None = None,
                          ttl: int = 3600) -> dict:
        stored = self._oauth_clients.get(client_id)
        if stored is None or stored != client_secret:
            return {"error": "invalid_client"}
        token = secrets.token_urlsafe(32)
        self._tokens[token] = {
            "client_id": client_id,
            "scopes": scopes or [],
            "expires_at": time.time() + ttl,
        }
        return {"access_token": token, "token_type": "Bearer",
                "expires_in": ttl, "scopes": scopes or []}

    def validate_oauth_token(self, token: str) -> dict | None:
        info = self._tokens.get(token)
        if info is None or time.time() > info["expires_at"]:
            return None
        return info


# ═══════════════════════════════════════════════════════════════════
# Agent Card
# ═══════════════════════════════════════════════════════════════════

@dataclass
class AgentCard:
    """A2A Agent Card — declares identity, capabilities, and auth requirements."""
    name: str
    description: str
    endpoint: str
    capabilities: list[str]
    security: SecurityScheme = field(default_factory=SecurityScheme)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "endpoint": self.endpoint,
            "capabilities": self.capabilities,
            "security": {
                "type": self.security.scheme_type,
                "header": self.security.api_key_header,
                "token_url": self.security.token_url,
                "scopes": self.security.scopes,
            },
            "metadata": self.metadata,
        }

    def matches(self, task_type: str) -> bool:
        return task_type in self.capabilities


# ═══════════════════════════════════════════════════════════════════
# Task
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Task:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    task_type: str = ""
    status: TaskStatus = TaskStatus.SUBMITTED
    input_data: dict = field(default_factory=dict)
    output_data: dict = field(default_factory=dict)
    error: str | None = None
    history: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    authenticated_client: str = ""

    def update_status(self, new_status: TaskStatus, detail: str = ""):
        self.history.append({
            "from": self.status.value, "to": new_status.value,
            "detail": detail, "timestamp": time.time(),
        })
        self.status = new_status
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id, "task_type": self.task_type,
            "status": self.status.value,
            "input_data": self.input_data, "output_data": self.output_data,
            "error": self.error, "history": self.history,
            "authenticated_client": self.authenticated_client,
        }


# ═══════════════════════════════════════════════════════════════════
# Remote Agent (receives and executes tasks)
# ═══════════════════════════════════════════════════════════════════

class RemoteAgent:
    """A2A Remote Agent with authentication enforcement."""

    def __init__(self, card: AgentCard,
                 auth_provider: A2AAuthProvider | None = None):
        self.card = card
        self.auth = auth_provider
        self._handlers: dict[str, Callable] = {}
        self._tasks: dict[str, Task] = {}

    def register_handler(self, task_type: str, handler: Callable):
        self._handlers[task_type] = handler

    def _authenticate(self, credentials: dict) -> str | dict:
        """Validate credentials per the card's security scheme. Returns client name or error."""
        if self.auth is None:
            return "anonymous"
        scheme = self.card.security

        if scheme.scheme_type == "apiKey":
            key = credentials.get("api_key", "")
            info = self.auth.validate_api_key(key)
            if info is None:
                return {"error": "unauthorized", "message": "Invalid API key"}
            return info["client"]

        elif scheme.scheme_type == "oauth2":
            token = credentials.get("token", "")
            info = self.auth.validate_oauth_token(token)
            if info is None:
                return {"error": "unauthorized",
                        "message": "Invalid or expired OAuth token"}
            return info["client_id"]

        return "anonymous"

    def receive_task(self, task: Task,
                     credentials: dict | None = None) -> Task:
        creds = credentials or {}
        self._tasks[task.id] = task

        # Authenticate
        client = self._authenticate(creds)
        if isinstance(client, dict):
            task.update_status(TaskStatus.FAILED, client.get("message", "Auth failed"))
            task.error = client.get("message", "Authentication failed")
            return task
        task.authenticated_client = client
        logger.info("a2a | agent=%s | task=%s | client=%s",
                     self.card.name, task.id, client)

        if task.task_type not in self._handlers:
            task.update_status(TaskStatus.FAILED,
                               f"No handler for '{task.task_type}'")
            task.error = f"Unsupported task type: {task.task_type}"
            return task

        task.update_status(TaskStatus.WORKING, f"Processing by {self.card.name}")
        try:
            result = self._handlers[task.task_type](task.input_data)
            task.output_data = result
            task.update_status(TaskStatus.COMPLETED, "Done")
        except Exception as exc:
            task.update_status(TaskStatus.FAILED, str(exc))
            task.error = str(exc)
        return task

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)


# ═══════════════════════════════════════════════════════════════════
# Client Agent (discovers and delegates)
# ═══════════════════════════════════════════════════════════════════

class ClientAgent:
    """A2A Client Agent — discovers remote agents and delegates tasks with auth."""

    def __init__(self, name: str = "supervisor"):
        self.name = name
        self._registry: dict[str, RemoteAgent] = {}
        self._credentials: dict[str, dict] = {}    # endpoint -> creds

    def register_remote(self, agent: RemoteAgent):
        self._registry[agent.card.endpoint] = agent

    def set_credentials(self, endpoint: str, credentials: dict):
        """Store credentials for a remote agent endpoint."""
        self._credentials[endpoint] = credentials

    def discover(self, task_type: str) -> list[AgentCard]:
        return [a.card for a in self._registry.values()
                if a.card.matches(task_type)]

    def submit_task(self, endpoint: str, task_type: str,
                    input_data: dict) -> Task:
        if endpoint not in self._registry:
            raise ValueError(f"No agent at endpoint: {endpoint}")
        task = Task(task_type=task_type, input_data=input_data)
        creds = self._credentials.get(endpoint, {})
        return self._registry[endpoint].receive_task(task, credentials=creds)

    def delegate(self, task_type: str, input_data: dict,
                 verbose: bool = True) -> Task:
        candidates = self.discover(task_type)
        if not candidates:
            task = Task(task_type=task_type, input_data=input_data)
            task.update_status(TaskStatus.FAILED,
                               f"No agent found for '{task_type}'")
            task.error = f"No capable agent for: {task_type}"
            return task
        card = candidates[0]
        if verbose:
            print(f"  [A2A] Delegating '{task_type}' -> {card.name} ({card.endpoint})")
        return self.submit_task(card.endpoint, task_type, input_data)


# ═══════════════════════════════════════════════════════════════════
# Agent Registry (network discovery)
# ═══════════════════════════════════════════════════════════════════

class AgentRegistry:
    def __init__(self):
        self._agents: dict[str, AgentCard] = {}

    def register(self, card: AgentCard):
        self._agents[card.endpoint] = card

    def search(self, task_type: str) -> list[AgentCard]:
        return [c for c in self._agents.values() if c.matches(task_type)]

    def list_all(self) -> list[AgentCard]:
        return list(self._agents.values())
