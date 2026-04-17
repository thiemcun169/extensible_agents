#!/usr/bin/env python3
"""Supervisor with auto-discovery + per-user credential derivation.

Usage: python scripts/supervisor_flow.py
"""
import os, sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "lib"))

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from data import DB_PATH
from identity import IdentityProvider, GrantRegistry, CredentialFactory, seed_lab_users
from a2a_framework import (AgentCard, SecurityScheme, A2AAuthProvider,
                            RemoteAgent, ClientAgent, AgentRegistry, TaskStatus)
from agent_builder import (build_analytics_agent, build_inventory_agent,
                            build_writer_agent, reset_builder_state)
from tracing import get_openai_client, get_langchain_handler, flush


def bind(cf, a2a_auth, lh, session):
    """Build one Remote Agent per card, bound to the user session."""
    remotes = {}

    lg_an, _, _ = build_analytics_agent(cf, session, apply_skill=True,
                                         langfuse_handler=lh)
    c_an = AgentCard(name="analytics_agent", description="KPI + SQL",
                      endpoint="agent://analytics", capabilities=["kpi_report"],
                      security=SecurityScheme(scheme_type="apiKey"))
    ra_an = RemoteAgent(card=c_an, auth_provider=a2a_auth)
    ra_an.register_handler("kpi_report", lambda d: {
        "report": lg_an.invoke({"messages":[{"role":"user","content":d["request"]}]})
                     ["messages"][-1].content})
    remotes["analytics_agent"] = (ra_an, c_an)

    try:
        lg_in, _, _ = build_inventory_agent(cf, session, langfuse_handler=lh)
        c_in = AgentCard(name="inventory_agent", description="stock analysis",
                          endpoint="agent://inventory",
                          capabilities=["inventory_check"],
                          security=SecurityScheme(scheme_type="apiKey"))
        ra_in = RemoteAgent(card=c_in, auth_provider=a2a_auth)
        ra_in.register_handler("inventory_check", lambda d: {
            "report": lg_in.invoke({"messages":[{"role":"user","content":d["request"]}]})
                         ["messages"][-1].content})
        remotes["inventory_agent"] = (ra_in, c_in)
    except PermissionError:
        pass

    try:
        lg_wr = build_writer_agent(cf, session, langfuse_handler=lh)
        c_wr = AgentCard(name="writer_agent", description="exec summary",
                          endpoint="agent://writer",
                          capabilities=["executive_summary"],
                          security=SecurityScheme(scheme_type="oauth2"))
        ra_wr = RemoteAgent(card=c_wr, auth_provider=a2a_auth)
        ra_wr.register_handler("executive_summary", lambda d: {
            "summary": lg_wr.invoke({"messages":[{"role":"user",
                "content":"Summarise:\n" + d.get("data","")}]})
                           ["messages"][-1].content})
        remotes["writer_agent"] = (ra_wr, c_wr)
    except PermissionError:
        pass

    return remotes


def supervisor(cf, remotes, session, request, oai, model):
    """Classify intent, discover agents, check grants, derive creds, delegate."""
    labels = {"kpi_report","inventory_check","executive_summary","direct"}
    r = oai.chat.completions.create(model=model, messages=[
        {"role":"system","content":"Pick ONE label: " + ", ".join(labels)
                                    + ". Respond with the label only."},
        {"role":"user","content":request}])
    cap = (r.choices[0].message.content or "").strip().lower()
    cap = cap if cap in labels else "direct"
    print(f"  classified -> {cap}")

    if cap == "direct":
        return "I can help with KPI reports, inventory checks, or exec summaries."

    for name, (ra, card) in remotes.items():
        if cap in card.capabilities and name in cf.available_agents(session):
            creds = cf.derive_a2a_credentials(session, name, card.security.scheme_type)
            client = ClientAgent(f"sup_{session.user_id}")
            client.register_remote(ra)
            client.set_credentials(card.endpoint, creds)
            t = client.submit_task(card.endpoint, cap, {"request": request})
            if t.status == TaskStatus.COMPLETED:
                return t.output_data.get("report") or t.output_data.get("summary") or str(t.output_data)
            return f"[{t.status.value}] {t.error}"
    return f"No agent authorised for '{cap}'."


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
    oai = get_openai_client()
    lh = get_langchain_handler()
    MODEL = os.getenv("OPENAI_MODEL","gpt-4.1-nano")

    session = idp.login("admin_thiem", "admin456")
    remotes = bind(cf, a2a_auth, lh, session)

    for q in [
        "Produce a KPI report: March 2025 vs February 2025.",
        "Which products are running low on stock?",
        "Hello!",
    ]:
        print(f"\n========== {q} ==========")
        print(supervisor(cf, remotes, session, q, oai, MODEL))

    flush()


if __name__ == "__main__":
    main()
