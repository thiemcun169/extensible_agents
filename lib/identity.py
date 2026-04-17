"""
Identity, Grants, Sessions & Credential Factory
================================================

This module implements the user-owned credential model used throughout the labs.

Core idea (matches how Claude "Login with GitHub" works):

    1.  The user logs in ONCE with their account (user_id + password in the lab,
        OAuth in production).
    2.  The Identity Provider issues a short-lived ``SessionToken``.
    3.  A ``GrantRegistry`` knows what this user is allowed to access — which
        MCP servers with which scopes, and which A2A agents.
    4.  When the user's agent needs to call an MCP server or another A2A agent,
        the ``CredentialFactory`` derives a scoped token FROM the session token.
        Nothing is hardcoded; revoking the session revokes everything.

Think of it as:

    UserLogin ─► SessionToken ─► CredentialFactory ─► [MCP token | A2A creds]
                      │
                      ▼
                GrantRegistry decides what is allowed

No raw secrets are ever held by the user, and the labs fake the login step so
students can focus on the delegation flow rather than on UI / password UX.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("identity")


# ═══════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════

@dataclass
class User:
    user_id: str
    display_name: str
    roles: set[str]          # coarse-grained roles: admin / analyst / viewer


@dataclass
class UserGrants:
    """What a user is permitted to do.  Produced by IT / admin approval."""
    user_id: str
    # mcp_server_name -> set of scope strings (e.g. {"revenue:read","sql:execute"})
    mcp_scopes: dict[str, set[str]] = field(default_factory=dict)
    # which A2A agent_ids the user may talk to via the supervisor
    agent_access: set[str] = field(default_factory=set)


@dataclass
class SessionToken:
    session_id: str
    user_id: str
    display_name: str
    roles: set[str]
    created_at: float
    expires_at: float
    signature: str           # HMAC so tampering is detectable

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "display_name": self.display_name,
            "roles": sorted(self.roles),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


# ═══════════════════════════════════════════════════════════════════
# Identity Provider
# ═══════════════════════════════════════════════════════════════════

class IdentityProvider:
    """Fake identity provider for the labs.

    In production this would be Auth0 / Keycloak / Okta / a real OAuth 2.1
    server with PKCE, MFA, etc.  The students' focus is the downstream
    credential derivation, not the UX of the login itself.
    """

    def __init__(self, signing_key: str | None = None, session_ttl: int = 3600):
        self._users: dict[str, User] = {}
        self._password_hashes: dict[str, str] = {}
        self._sessions: dict[str, SessionToken] = {}
        self._signing_key = signing_key or secrets.token_urlsafe(32)
        self.session_ttl = session_ttl

    # ── user registration ─────────────────────────────────────────
    def register_user(self, user: User, password: str):
        self._users[user.user_id] = user
        self._password_hashes[user.user_id] = self._hash(password)
        logger.info("idp: registered user %s roles=%s", user.user_id, user.roles)

    def _hash(self, pw: str) -> str:
        return hashlib.sha256((pw + "|salt").encode()).hexdigest()

    # ── login ─────────────────────────────────────────────────────
    def login(self, user_id: str, password: str) -> SessionToken:
        """Authenticate the user and return a signed SessionToken."""
        user = self._users.get(user_id)
        if user is None or self._password_hashes.get(user_id) != self._hash(password):
            raise PermissionError(f"Invalid credentials for '{user_id}'")

        now = time.time()
        session_id = secrets.token_urlsafe(24)
        payload = f"{session_id}|{user_id}|{int(now)}"
        sig = hmac.new(self._signing_key.encode(), payload.encode(),
                       hashlib.sha256).hexdigest()

        token = SessionToken(
            session_id=session_id,
            user_id=user_id,
            display_name=user.display_name,
            roles=set(user.roles),
            created_at=now,
            expires_at=now + self.session_ttl,
            signature=sig,
        )
        self._sessions[session_id] = token
        logger.info("idp: login success user=%s session=%s",
                     user_id, session_id[:8])
        return token

    # ── session validation ────────────────────────────────────────
    def validate(self, session_id: str) -> Optional[SessionToken]:
        tk = self._sessions.get(session_id)
        if tk is None or tk.is_expired:
            return None
        return tk

    def revoke(self, session_id: str):
        self._sessions.pop(session_id, None)

# login("analyst_duc", "pass")
#         │
#         ├─ hash(pass) == stored_hash? ✓
#         │
#         ├─ sinh session_id (32 chars random)
#         │
#         ├─ HMAC(signing_key, "session_id|analyst_duc|timestamp")
#         │
#         ├─ build SessionToken {id, roles, exp, sig}
#         │
#         ├─ lưu vào _sessions[session_id]
#         │
#         └─ trả token về client

# ═══════════════════════════════════════════════════════════════════
# Grant Registry
# ═══════════════════════════════════════════════════════════════════

class GrantRegistry:
    """Maps user_id -> UserGrants.

    Grants are assigned by admins (think: "IT ticket approved") and determine
    exactly what resources the user's agent can touch.  A login with no grants
    produces a session that cannot do anything useful.
    """

    def __init__(self):
        self._grants: dict[str, UserGrants] = {}

    def grant_mcp(self, user_id: str, mcp_server: str, scopes: set[str]):
        grants = self._grants.setdefault(user_id, UserGrants(user_id=user_id))
        grants.mcp_scopes.setdefault(mcp_server, set()).update(scopes)

    def grant_agent(self, user_id: str, agent_id: str):
        grants = self._grants.setdefault(user_id, UserGrants(user_id=user_id))
        grants.agent_access.add(agent_id)

    def get(self, user_id: str) -> UserGrants:
        return self._grants.get(user_id, UserGrants(user_id=user_id))


# ═══════════════════════════════════════════════════════════════════
# Credential Factory
# ═══════════════════════════════════════════════════════════════════

class CredentialFactory:
    """Exchanges a valid SessionToken for scoped downstream credentials.

    Use cases inside the labs:

        * ``derive_mcp_token(session, "datatech-mcp")`` — returns a Bearer
          token that the MCP server will accept, carrying exactly the scopes
          the user has been granted.
        * ``derive_a2a_credentials(session, "analytics_agent")`` — returns
          either an API-key or an OAuth access-token matching the agent's
          declared security scheme in its Agent Card.
    """

    def __init__(self, idp: IdentityProvider, grants: GrantRegistry):
        self.idp = idp
        self.grants = grants
        self._mcp_providers: dict[str, "_MCPAuthBridge"] = {}
        self._a2a_bridge: Optional["_A2AAuthBridge"] = None

    # --- registration (done once at app boot) -----------------------
    def register_mcp_provider(self, mcp_name: str, provider):
        """Bind an MCPAuthProvider so we can mint tokens for this server."""
        self._mcp_providers[mcp_name] = _MCPAuthBridge(mcp_name, provider)

    def register_a2a_provider(self, provider):
        self._a2a_bridge = _A2AAuthBridge(provider)

    # --- session -> MCP token ---------------------------------------
    def derive_mcp_token(self, session: SessionToken, mcp_name: str) -> str:
        """Return a Bearer-token string valid for the given MCP server."""
        if self.idp.validate(session.session_id) is None:
            raise PermissionError("Session invalid or expired")
        grants = self.grants.get(session.user_id)
        scopes = grants.mcp_scopes.get(mcp_name, set())
        if not scopes:
            raise PermissionError(
                f"User '{session.user_id}' has no grants for MCP '{mcp_name}'"
            )
        bridge = self._mcp_providers.get(mcp_name)
        if bridge is None:
            raise RuntimeError(f"No MCP provider registered for '{mcp_name}'")
        return bridge.mint(session, scopes)

    # --- session -> A2A credentials ---------------------------------
    def derive_a2a_credentials(self, session: SessionToken, agent_id: str,
                                scheme: str = "apiKey") -> dict:
        """Return credentials dict for the agent ("api_key" or "token")."""
        if self.idp.validate(session.session_id) is None:
            raise PermissionError("Session invalid or expired")
        grants = self.grants.get(session.user_id)
        if agent_id not in grants.agent_access:
            raise PermissionError(
                f"User '{session.user_id}' has no grant for agent '{agent_id}'"
            )
        if self._a2a_bridge is None:
            raise RuntimeError("A2A provider not registered")
        return self._a2a_bridge.mint(session, agent_id, scheme)

    # --- visibility helpers used by UIs / logging -------------------
    def available_agents(self, session: SessionToken) -> set[str]:
        return set(self.grants.get(session.user_id).agent_access)

    def available_mcp_scopes(self, session: SessionToken,
                              mcp_name: str) -> set[str]:
        return set(self.grants.get(session.user_id).mcp_scopes.get(mcp_name, set()))


# ═══════════════════════════════════════════════════════════════════
# Internal bridges — keep identity layer decoupled from the frameworks
# ═══════════════════════════════════════════════════════════════════

class _MCPAuthBridge:
    def __init__(self, mcp_name: str, provider):
        self.mcp_name = mcp_name
        self.provider = provider          # MCPAuthProvider

    def mint(self, session: SessionToken, scopes: set[str]) -> str:
        """Convert (session, scopes) -> MCP Bearer token string.

        Each ``mint`` call re-registers the (user, mcp) client pair with the
        *current* session signature as the secret so that re-logins do not
        collide with stale credentials.
        """
        client_id = f"{session.user_id}@{self.mcp_name}"
        secret = session.signature[:16]
        # register_client is idempotent (dict assignment) — safe to call each time
        self.provider.register_client(client_id, secret)
        tk = self.provider.issue_token(
            client_id, secret,
            requested_scopes=scopes,
            roles=session.roles | {"authenticated"},
        )
        if isinstance(tk, dict):
            raise RuntimeError(f"MCP token issue failed: {tk}")
        return tk.token


class _A2AAuthBridge:
    def __init__(self, provider):
        self.provider = provider          # A2AAuthProvider

    def mint(self, session: SessionToken, agent_id: str, scheme: str) -> dict:
        if scheme == "apiKey":
            # per-session API key: prefix with user so logs show WHO did it
            key = f"sk.{session.user_id}.{agent_id}.{session.session_id[:12]}"
            self.provider.register_api_key(key, session.user_id,
                                            roles=session.roles)
            return {"api_key": key}

        elif scheme == "oauth2":
            client_id = f"{session.user_id}@{agent_id}"
            secret = session.signature[:16]
            # re-register each call so the client_secret matches the current session
            self.provider.register_oauth_client(client_id, secret)
            tk = self.provider.issue_oauth_token(client_id, secret,
                                                  scopes=[f"use:{agent_id}"])
            if "error" in tk:
                raise RuntimeError(f"A2A OAuth issue failed: {tk}")
            return {"token": tk["access_token"]}

        raise ValueError(f"Unsupported A2A security scheme: {scheme}")


# ═══════════════════════════════════════════════════════════════════
# Convenience: a lab-ready "seed" for the DataTech demo
# ═══════════════════════════════════════════════════════════════════

def seed_lab_users(idp: IdentityProvider, grants: GrantRegistry):
    """Register the fake users and grants used across Labs 1-3."""
    # Users --------------------------------------------------------
    idp.register_user(
        User(user_id="admin_thiem",  display_name="Nguyen Ba Thiem (Admin)",
             roles={"admin", "analyst"}), password="admin456")
    idp.register_user(
        User(user_id="analyst_duc",  display_name="Nguyen Minh Duc (Analyst)",
             roles={"analyst"}), password="duc123")
    idp.register_user(
        User(user_id="analyst_mai",  display_name="Tran Thi Mai (Analyst)",
             roles={"analyst"}), password="mai123")
    idp.register_user(
        User(user_id="viewer_nam",   display_name="Le Hoang Nam (Viewer)",
             roles={"viewer"}), password="nam789")

    # Admin: everything on both MCP servers, every agent -----------
    MCP_A = "datatech-analytics-mcp"
    MCP_I = "datatech-inventory-mcp"
    all_scopes_a = {"revenue:read", "products:read", "sql:execute", "customers:read"}
    all_scopes_i = {"products:read", "orders:read", "sql:execute"}
    for agent in ["analytics_agent", "writer_agent", "inventory_agent"]:
        grants.grant_agent("admin_thiem", agent)
    grants.grant_mcp("admin_thiem", MCP_A, all_scopes_a)
    grants.grant_mcp("admin_thiem", MCP_I, all_scopes_i)

    # Analyst Duc: analytics + writer, revenue + SQL on analytics MCP
    grants.grant_mcp("analyst_duc", MCP_A,
                      {"revenue:read", "products:read", "sql:execute"})
    grants.grant_agent("analyst_duc", "analytics_agent")
    grants.grant_agent("analyst_duc", "writer_agent")

    # Analyst Mai: analytics only (no writer — has to call supervisor)
    grants.grant_mcp("analyst_mai", MCP_A,
                      {"revenue:read", "products:read"})
    grants.grant_agent("analyst_mai", "analytics_agent")

    # Viewer Nam: products-only read, no agents
    grants.grant_mcp("viewer_nam", MCP_A, {"products:read"})
