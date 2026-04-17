"""Tests for the identity / grants / session / credential-factory layer."""
import os, sys, pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "lib"))

from identity import (IdentityProvider, GrantRegistry, CredentialFactory,
                       SessionToken, User, seed_lab_users)
from mcp_framework import MCPAuthProvider, MCPServer
from a2a_framework import A2AAuthProvider


# ── IdentityProvider ──────────────────────────────────────────────────
class TestIdentity:
    def setup_method(self):
        self.idp = IdentityProvider()
        self.idp.register_user(User("alice", "Alice", {"analyst"}), "pw")

    def test_login_ok(self):
        tk = self.idp.login("alice", "pw")
        assert isinstance(tk, SessionToken)
        assert tk.user_id == "alice"
        assert "analyst" in tk.roles

    def test_login_bad_password(self):
        with pytest.raises(PermissionError):
            self.idp.login("alice", "wrong")

    def test_login_unknown_user(self):
        with pytest.raises(PermissionError):
            self.idp.login("ghost", "pw")

    def test_validate(self):
        tk = self.idp.login("alice", "pw")
        assert self.idp.validate(tk.session_id) is not None
        self.idp.revoke(tk.session_id)
        assert self.idp.validate(tk.session_id) is None


# ── GrantRegistry ─────────────────────────────────────────────────────
class TestGrants:
    def test_mcp_grant_merges(self):
        g = GrantRegistry()
        g.grant_mcp("alice", "s1", {"a:read"})
        g.grant_mcp("alice", "s1", {"b:read"})
        assert g.get("alice").mcp_scopes["s1"] == {"a:read", "b:read"}

    def test_agent_grant(self):
        g = GrantRegistry()
        g.grant_agent("alice", "analytics_agent")
        assert "analytics_agent" in g.get("alice").agent_access

    def test_unknown_user_returns_empty(self):
        g = GrantRegistry()
        assert g.get("nobody").agent_access == set()


# ── CredentialFactory ─────────────────────────────────────────────────
class TestCredentialFactory:
    def setup_method(self):
        self.idp = IdentityProvider()
        self.grants = GrantRegistry()
        self.idp.register_user(User("a", "Alice", {"analyst"}), "pw")
        self.grants.grant_mcp("a", "s1", {"read:x"})
        self.grants.grant_agent("a", "agent_x")

        self.mcp_auth = MCPAuthProvider()
        self.a2a_auth = A2AAuthProvider()
        self.cf = CredentialFactory(self.idp, self.grants)
        self.cf.register_mcp_provider("s1", self.mcp_auth)
        self.cf.register_a2a_provider(self.a2a_auth)

    def test_mcp_token_works(self):
        sess = self.idp.login("a", "pw")
        tk = self.cf.derive_mcp_token(sess, "s1")
        assert isinstance(tk, str) and len(tk) > 10
        mtk = self.mcp_auth.validate(tk)
        assert mtk is not None
        assert "read:x" in mtk.scopes

    def test_mcp_token_denied_without_grant(self):
        sess = self.idp.login("a", "pw")
        with pytest.raises(PermissionError):
            self.cf.derive_mcp_token(sess, "s2")

    def test_a2a_apikey(self):
        sess = self.idp.login("a", "pw")
        c = self.cf.derive_a2a_credentials(sess, "agent_x", scheme="apiKey")
        assert "api_key" in c
        assert c["api_key"].startswith(f"sk.a.agent_x.")

    def test_a2a_oauth(self):
        sess = self.idp.login("a", "pw")
        c = self.cf.derive_a2a_credentials(sess, "agent_x", scheme="oauth2")
        assert "token" in c

    def test_a2a_denied_without_grant(self):
        sess = self.idp.login("a", "pw")
        with pytest.raises(PermissionError):
            self.cf.derive_a2a_credentials(sess, "agent_not_granted")

    def test_available_agents(self):
        sess = self.idp.login("a", "pw")
        assert self.cf.available_agents(sess) == {"agent_x"}


# ── End-to-end: seed users -> MCP scope visibility ────────────────────
class TestEndToEnd:
    def test_scope_visibility(self):
        idp = IdentityProvider()
        grants = GrantRegistry()
        seed_lab_users(idp, grants)
        a2a = A2AAuthProvider()
        cf = CredentialFactory(idp, grants)
        cf.register_a2a_provider(a2a)

        mcp_auth = MCPAuthProvider()
        cf.register_mcp_provider("datatech-analytics-mcp", mcp_auth)
        server = MCPServer("datatech-analytics-mcp", auth_provider=mcp_auth)

        @server.tool(
            name="query_revenue", description="revenue",
            parameters={"type":"object","properties":{},"required":[]},
            allowed_roles={"analyst","admin"}, required_scope="revenue:read",
        )
        def _(): return {}

        @server.tool(
            name="list_products", description="products",
            parameters={"type":"object","properties":{},"required":[]},
            allowed_roles={"viewer","analyst","admin"}, required_scope="products:read",
        )
        def _(): return {}

        viewer = idp.login("viewer_nam", "nam789")
        analyst = idp.login("analyst_duc", "duc123")
        admin = idp.login("admin_thiem", "admin456")

        v_tk = cf.derive_mcp_token(viewer, "datatech-analytics-mcp")
        a_tk = cf.derive_mcp_token(analyst, "datatech-analytics-mcp")
        d_tk = cf.derive_mcp_token(admin, "datatech-analytics-mcp")

        v_tools = {t["name"] for t in server.list_tools(v_tk)}
        a_tools = {t["name"] for t in server.list_tools(a_tk)}
        d_tools = {t["name"] for t in server.list_tools(d_tk)}

        assert v_tools == {"list_products"}
        assert "query_revenue" in a_tools
        assert "query_revenue" in d_tools
