#!/usr/bin/env python3
"""Demo: identity-driven MCP agent built with LangGraph create_react_agent.

Usage: python scripts/mcp_server_demo.py
"""
import os, sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "lib"))

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from data import DB_PATH
from identity import IdentityProvider, GrantRegistry, CredentialFactory, seed_lab_users
from a2a_framework import A2AAuthProvider
from agent_builder import build_analytics_agent, get_or_build_analytics_mcp, reset_builder_state
from tracing import get_langchain_handler, flush


def main():
    if not os.path.exists(DB_PATH):
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "db"))
        from setup_database import create_database
        create_database()

    reset_builder_state()
    idp, grants = IdentityProvider(), GrantRegistry()
    seed_lab_users(idp, grants)
    cf = CredentialFactory(idp, grants)
    cf.register_a2a_provider(A2AAuthProvider())
    get_or_build_analytics_mcp(cf)

    lh = get_langchain_handler()

    print("=" * 60, "\nMCP + LangGraph create_react_agent demo\n", "=" * 60)
    for uid, pwd in [("admin_thiem", "admin456"),
                      ("analyst_mai", "mai123"),
                      ("viewer_nam", "nam789")]:
        print(f"\n---- logged in as: {uid} ----")
        sess = idp.login(uid, pwd)
        try:
            agent, server, tk = build_analytics_agent(cf, sess, langfuse_handler=lh)
        except PermissionError as e:
            print(f"  build denied: {e}")
            continue
        tools = [t["name"] for t in server.list_tools(tk)]
        print(f"  tools available: {tools}")
        q = "What was Hanoi revenue in January 2025? If you can't, say so."
        r = agent.invoke({"messages": [{"role": "user", "content": q}]})
        print(f"  answer: {r['messages'][-1].content[:180]}")

    flush()


if __name__ == "__main__":
    main()
