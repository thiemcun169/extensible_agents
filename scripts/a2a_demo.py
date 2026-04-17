#!/usr/bin/env python3
"""Demo: A2A multi-agent with session-derived credentials.

Usage: python scripts/a2a_demo.py
"""
import os, sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "lib"))

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from data import DB_PATH
from identity import IdentityProvider, GrantRegistry, CredentialFactory, seed_lab_users
from a2a_framework import (AgentCard, SecurityScheme, A2AAuthProvider,
                            RemoteAgent, ClientAgent, TaskStatus)
from agent_builder import (build_analytics_agent, build_writer_agent,
                            reset_builder_state)
from tracing import get_langchain_handler, flush


def main():
    if not os.path.exists(DB_PATH):
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "db"))
        from setup_database import create_database
        create_database()

    reset_builder_state()
    idp, grants = IdentityProvider(), GrantRegistry()
    seed_lab_users(idp, grants)
    a2a_auth = A2AAuthProvider()
    cf = CredentialFactory(idp, grants)
    cf.register_a2a_provider(a2a_auth)
    lh = get_langchain_handler()

    session = idp.login("admin_thiem", "admin456")

    # Build the analytics agent (Lab 1+2 product) and wrap as a Remote Agent
    lg_an, _, _ = build_analytics_agent(cf, session, apply_skill=True,
                                         langfuse_handler=lh)

    analytics_card = AgentCard(
        name="analytics_agent", description="KPI reports",
        endpoint="agent://analytics", capabilities=["kpi_report"],
        security=SecurityScheme(scheme_type="apiKey"),
    )
    ra_an = RemoteAgent(card=analytics_card, auth_provider=a2a_auth)
    ra_an.register_handler("kpi_report", lambda d: {
        "report": lg_an.invoke({"messages":[{"role":"user","content":d["request"]}]})
                     ["messages"][-1].content})

    # Writer agent
    lg_wr = build_writer_agent(cf, session, langfuse_handler=lh)
    writer_card = AgentCard(
        name="writer_agent", description="Executive summaries",
        endpoint="agent://writer", capabilities=["executive_summary"],
        security=SecurityScheme(scheme_type="oauth2"),
    )
    ra_wr = RemoteAgent(card=writer_card, auth_provider=a2a_auth)
    ra_wr.register_handler("executive_summary", lambda d: {
        "summary": lg_wr.invoke({"messages":[{"role":"user",
            "content":"Summarise:\n" + d.get("data","")}]})
                       ["messages"][-1].content})

    # Derive credentials from session — NOT hardcoded
    an_creds = cf.derive_a2a_credentials(session, "analytics_agent", "apiKey")
    wr_creds = cf.derive_a2a_credentials(session, "writer_agent", "oauth2")
    print("Derived A2A creds:")
    print(f"  analytics -> api_key=...{an_creds['api_key'][-12:]}")
    print(f"  writer    -> oauth   =...{wr_creds['token'][-8:]}")

    sup = ClientAgent(f"supervisor_{session.user_id}")
    sup.register_remote(ra_an); sup.register_remote(ra_wr)
    sup.set_credentials("agent://analytics", an_creds)
    sup.set_credentials("agent://writer", wr_creds)

    print("\n--- delegate KPI report ---")
    t1 = sup.submit_task("agent://analytics", "kpi_report",
        {"request": "KPI report March 2025 vs February 2025, all regions."})
    print(f"  status: {t1.status.value}  client: {t1.authenticated_client}")
    print(t1.output_data.get("report","")[:400])

    print("\n--- delegate executive summary ---")
    t2 = sup.submit_task("agent://writer", "executive_summary",
        {"data": t1.output_data.get("report","")})
    print(f"  status: {t2.status.value}  client: {t2.authenticated_client}")
    print(t2.output_data.get("summary",""))

    print("\n--- bad credentials ---")
    bad_sup = ClientAgent("bad")
    bad_sup.register_remote(ra_an)
    bad_sup.set_credentials("agent://analytics", {"api_key": "forged"})
    t3 = bad_sup.submit_task("agent://analytics", "kpi_report", {"request":"x"})
    print(f"  status: {t3.status.value}  error: {t3.error}")

    flush()


if __name__ == "__main__":
    main()
