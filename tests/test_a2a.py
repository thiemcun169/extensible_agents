"""Tests for A2A framework v2 — auth, agent cards, task lifecycle."""
import os, sys, pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "lib"))

from a2a_framework import (AgentCard, SecurityScheme, A2AAuthProvider,
                           Task, TaskStatus, RemoteAgent, ClientAgent,
                           AgentRegistry)


class TestA2AAuth:
    def setup_method(self):
        self.auth = A2AAuthProvider()

    def test_api_key(self):
        self.auth.register_api_key("key123", "client-a")
        assert self.auth.validate_api_key("key123") is not None
        assert self.auth.validate_api_key("wrong") is None

    def test_oauth_token(self):
        self.auth.register_oauth_client("cli", "sec")
        r = self.auth.issue_oauth_token("cli", "sec", scopes=["read"])
        assert "access_token" in r
        assert self.auth.validate_oauth_token(r["access_token"]) is not None

    def test_oauth_bad_secret(self):
        self.auth.register_oauth_client("cli", "sec")
        r = self.auth.issue_oauth_token("cli", "wrong")
        assert "error" in r

    def test_oauth_invalid_token(self):
        assert self.auth.validate_oauth_token("nonexistent") is None


class TestAgentCard:
    def test_create_and_match(self):
        card = AgentCard(name="A", description="", endpoint="a://a",
                         capabilities=["x", "y"])
        assert card.matches("x")
        assert not card.matches("z")

    def test_security_scheme(self):
        card = AgentCard(name="A", description="", endpoint="a://a",
                         capabilities=["x"],
                         security=SecurityScheme(scheme_type="oauth2",
                                                 token_url="/token"))
        d = card.to_dict()
        assert d["security"]["type"] == "oauth2"


class TestTask:
    def test_lifecycle(self):
        t = Task(task_type="test")
        assert t.status == TaskStatus.SUBMITTED
        t.update_status(TaskStatus.WORKING, "start")
        t.update_status(TaskStatus.COMPLETED, "done")
        assert t.status == TaskStatus.COMPLETED
        assert len(t.history) == 2


class TestRemoteAgentAuth:
    def setup_method(self):
        self.auth = A2AAuthProvider()
        self.auth.register_api_key("valid-key", "supervisor")
        card = AgentCard(name="A", description="", endpoint="a://a",
                         capabilities=["add"],
                         security=SecurityScheme(scheme_type="apiKey"))
        self.agent = RemoteAgent(card=card, auth_provider=self.auth)
        self.agent.register_handler("add", lambda d: {"sum": d["a"] + d["b"]})

    def test_authenticated_success(self):
        t = Task(task_type="add", input_data={"a": 3, "b": 4})
        r = self.agent.receive_task(t, credentials={"api_key": "valid-key"})
        assert r.status == TaskStatus.COMPLETED
        assert r.output_data == {"sum": 7}
        assert r.authenticated_client == "supervisor"

    def test_bad_key_rejected(self):
        t = Task(task_type="add", input_data={"a": 1, "b": 2})
        r = self.agent.receive_task(t, credentials={"api_key": "wrong"})
        assert r.status == TaskStatus.FAILED
        assert "unauthorized" in (r.error or "").lower() or "Invalid" in (r.error or "")

    def test_no_creds_rejected(self):
        t = Task(task_type="add", input_data={"a": 1, "b": 2})
        r = self.agent.receive_task(t, credentials={})
        assert r.status == TaskStatus.FAILED


class TestRemoteAgentOAuth:
    def setup_method(self):
        self.auth = A2AAuthProvider()
        self.auth.register_oauth_client("cli", "sec")
        self.token_resp = self.auth.issue_oauth_token("cli", "sec")
        card = AgentCard(name="W", description="", endpoint="a://w",
                         capabilities=["write"],
                         security=SecurityScheme(scheme_type="oauth2"))
        self.agent = RemoteAgent(card=card, auth_provider=self.auth)
        self.agent.register_handler("write", lambda d: {"text": "ok"})

    def test_oauth_success(self):
        t = Task(task_type="write", input_data={})
        r = self.agent.receive_task(t,
            credentials={"token": self.token_resp["access_token"]})
        assert r.status == TaskStatus.COMPLETED

    def test_oauth_expired_rejected(self):
        t = Task(task_type="write", input_data={})
        r = self.agent.receive_task(t, credentials={"token": "expired-abc"})
        assert r.status == TaskStatus.FAILED


class TestClientAgent:
    def setup_method(self):
        self.auth = A2AAuthProvider()
        self.auth.register_api_key("k1", "sup")
        card = AgentCard(name="Math", description="", endpoint="a://math",
                         capabilities=["add"],
                         security=SecurityScheme(scheme_type="apiKey"))
        self.remote = RemoteAgent(card=card, auth_provider=self.auth)
        self.remote.register_handler("add", lambda d: {"r": d["a"] + d["b"]})
        self.client = ClientAgent("sup")
        self.client.register_remote(self.remote)
        self.client.set_credentials("a://math", {"api_key": "k1"})

    def test_delegate(self):
        t = self.client.delegate("add", {"a": 5, "b": 3}, verbose=False)
        assert t.status == TaskStatus.COMPLETED
        assert t.output_data["r"] == 8

    def test_discover(self):
        assert len(self.client.discover("add")) == 1
        assert len(self.client.discover("nope")) == 0

    def test_delegate_no_agent(self):
        t = self.client.delegate("nope", {}, verbose=False)
        assert t.status == TaskStatus.FAILED


class TestAgentRegistry:
    def test_registry(self):
        reg = AgentRegistry()
        reg.register(AgentCard(name="A", description="", endpoint="a://a",
                                capabilities=["x"]))
        assert len(reg.search("x")) == 1
        assert len(reg.search("y")) == 0
