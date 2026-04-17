"""
Agent Builder — factory functions used across Labs 1, 2, and 3.

Every factory takes a ``SessionToken`` and a ``CredentialFactory`` and returns
a ready-to-run LangGraph agent whose internal MCP tokens are derived from the
user's session.  No credentials are hardcoded; a user with insufficient grants
gets an agent with fewer tools (or a PermissionError if they lack access).

Public factories:

    build_datatech_analytics_mcp(cred_factory, session)
        -> MCPServer + mcp_token  ready for tools/resources/prompts

    build_datatech_inventory_mcp(cred_factory, session)
        -> separate MCP server focused on products + orders

    build_langchain_tools_from_mcp(server, token)
        -> list[StructuredTool]  (for create_react_agent)

    build_analytics_agent(cred_factory, session, apply_skill=False)
        -> CompiledStateGraph      (LangGraph create_react_agent)

    build_inventory_agent(cred_factory, session)
        -> CompiledStateGraph

    build_writer_agent(cred_factory, session)
        -> CompiledStateGraph

All factories accept an optional ``langfuse_handler`` which, if provided, is
attached to the ``.with_config(callbacks=...)`` of the returned agent so every
run shows up in Langfuse.
"""
from __future__ import annotations

import json
import os
from typing import Callable

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from mcp_framework import MCPServer, MCPAuthProvider, build_sql_tool
from data import get_db, get_schema, DB_PATH, RESTRICTED_TABLES, PII_COLUMNS
from identity import SessionToken, CredentialFactory
from skill_loader import load_skill


# ═══════════════════════════════════════════════════════════════════
# MCP server factories — shared singletons keyed by name
# ═══════════════════════════════════════════════════════════════════

_MCP_SERVERS: dict[str, MCPServer] = {}
_MCP_AUTH_PROVIDERS: dict[str, MCPAuthProvider] = {}


def get_or_build_analytics_mcp(cred_factory: CredentialFactory) -> MCPServer:
    """Build (once) the DataTech analytics MCP server and register its auth
    provider with the CredentialFactory."""
    name = "datatech-analytics-mcp"
    if name in _MCP_SERVERS:
        return _MCP_SERVERS[name]

    auth = MCPAuthProvider(token_ttl=3600)
    cred_factory.register_mcp_provider(name, auth)
    server = MCPServer(name=name, version="2.0", auth_provider=auth)

    # ── TOOLS ─────────────────────────────────────────────────────
    @server.tool(
        name="query_revenue",
        description=(
            "Query monthly revenue by region_id (HN=Hanoi, HC=HCMC, DN=Da Nang) "
            "over a date range (YYYY-MM). Returns VND amounts and order counts."
        ),
        parameters={
            "type": "object",
            "properties": {
                "region_id":   {"type": "string", "enum": ["HN","HC","DN"]},
                "start_month": {"type": "string", "description": "YYYY-MM"},
                "end_month":   {"type": "string", "description": "YYYY-MM"},
            },
            "required": ["region_id", "start_month", "end_month"],
        },
        allowed_roles={"analyst", "admin"},
        required_scope="revenue:read",
    )
    def tool_revenue(region_id, start_month, end_month):
        conn = get_db()
        rows = conn.execute(
            "SELECT r.name AS region, rv.month, rv.total_vnd, rv.order_count "
            "FROM revenue rv JOIN regions r ON rv.region_id = r.id "
            "WHERE rv.region_id = ? AND rv.month >= ? AND rv.month <= ? "
            "ORDER BY rv.month",
            (region_id, start_month, end_month),
        ).fetchall()
        conn.close()
        return {"results": [dict(r) for r in rows], "currency": "VND"} if rows \
                else {"error": "No data"}

    @server.tool(
        name="list_products",
        description="List products; optionally filter by category (Laptop, Phone, Tablet, Accessories, Monitor).",
        parameters={
            "type": "object",
            "properties": {"category": {"type": "string"}},
            "required": [],
        },
        allowed_roles={"viewer", "analyst", "admin"},
        required_scope="products:read",
    )
    def tool_products(category=None):
        conn = get_db()
        base = ("SELECT id, name, category, price, stock FROM products "
                "WHERE is_active = 1")
        rows = conn.execute(base + (" AND category = ?" if category else ""),
                            (category,) if category else ()).fetchall()
        conn.close()
        return {"products": [dict(r) for r in rows], "count": len(rows)}

    sql_handler = build_sql_tool(
        DB_PATH,
        allowed_tables={"regions","products","customers","orders","revenue"},
        blocked_tables=RESTRICTED_TABLES,
        pii_columns=PII_COLUMNS, max_rows=50,
    )

    @server.tool(
        name="execute_sql",
        description=(
            "Execute a read-only SQL SELECT statement against the DataTech database. "
            "Allowed tables: regions, products, customers, orders, revenue. "
            "internal_config is restricted. PII (email, phone) is auto-masked. "
            "Maximum 50 rows per query."
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "SQL SELECT"}},
            "required": ["query"],
        },
        allowed_roles={"analyst", "admin"},
        required_scope="sql:execute",
    )
    def tool_sql(query):
        return sql_handler(query)

    # ── RESOURCES ─────────────────────────────────────────────────
    @server.resource(
        uri="datatech://schema",
        name="Database Schema",
        description="Full database schema including tables, columns, types, and row counts. "
                    "Agents MUST read this before writing SQL.",
        allowed_roles={"analyst", "admin"},
        required_scope="sql:execute",
    )
    def res_schema():
        return get_schema()

    @server.resource(
        uri="datatech://rules",
        name="Business Rules",
        description="KPI thresholds, formatting and reporting conventions.",
        allowed_roles={"analyst", "admin"},
    )
    def res_rules():
        return ("DATATECH VIETNAM — BUSINESS RULES\n"
                "- Revenue drop > 10%: flag as NEEDS ATTENTION\n"
                "- Revenue growth > 10%: flag as STRONG GROWTH\n"
                "- Amounts in VND with thousand separators\n"
                "- Sort regions by revenue (highest first)\n"
                "- Executive summary: max 3 sentences\n"
                "- PII columns (email, phone) are ALWAYS masked")

    @server.resource(
        uri="datatech://company",
        name="Company Overview",
        description="Company info, regions, currency.",
        allowed_roles={"viewer", "analyst", "admin"},
    )
    def res_company():
        return ("DataTech Vietnam — Technology Consulting\n"
                "Regions: Hanoi (id=HN), Ho Chi Minh City (id=HC), Da Nang (id=DN)\n"
                "Currency: VND. Fiscal year: Jan-Dec.")

    # ── PROMPTS ───────────────────────────────────────────────────
    server.prompt(
        "revenue_analysis",
        "Period-over-period revenue analysis template",
        "Analyse revenue for {region} (region_id={region_id}). "
        "Compare {current_month} against {prior_month}. "
        "Return a markdown table with columns Month | Revenue (VND) | Change % | Status. "
        "Flag drops > 10% as NEEDS ATTENTION; growth > 10% as STRONG GROWTH. "
        "End with a two-sentence executive summary.",
    )
    server.prompt(
        "sql_analysis",
        "Structured SQL exploration template",
        "Answer using SQL: {question}\n"
        "Tables: regions, products, customers, orders, revenue.\n"
        "Show the SQL you wrote, then the results as a table.",
    )
    server.prompt(
        "full_report",
        "Comprehensive business report template",
        "Full business report for {period}:\n"
        "1. Revenue for ALL regions\n"
        "2. Top selling products\n"
        "3. Customer insights\n"
        "Follow the Business Rules. End with an executive summary.",
    )

    _MCP_SERVERS[name] = server
    _MCP_AUTH_PROVIDERS[name] = auth
    return server


def get_or_build_inventory_mcp(cred_factory: CredentialFactory) -> MCPServer:
    name = "datatech-inventory-mcp"
    if name in _MCP_SERVERS:
        return _MCP_SERVERS[name]

    auth = MCPAuthProvider(token_ttl=3600)
    cred_factory.register_mcp_provider(name, auth)
    server = MCPServer(name=name, version="1.0", auth_provider=auth)

    sql_handler = build_sql_tool(
        DB_PATH,
        allowed_tables={"products", "orders"},
        blocked_tables=RESTRICTED_TABLES, max_rows=50,
    )

    @server.tool(
        name="inventory_sql",
        description=("Inventory SQL SELECT queries. Tables: products, orders. "
                     "Use to check stock levels, sales velocity, etc."),
        parameters={
            "type":"object",
            "properties":{"query":{"type":"string"}},
            "required":["query"],
        },
        allowed_roles={"analyst","admin"},
        required_scope="sql:execute",
    )
    def tool_inv(query):
        return sql_handler(query)

    @server.tool(
        name="stock_summary",
        description="Summary of products flagged LOW (<50 stock) or CRITICAL (<20 stock).",
        parameters={"type":"object","properties":{},"required":[]},
        allowed_roles={"analyst","admin"},
        required_scope="products:read",
    )
    def tool_stock():
        conn = get_db()
        rows = conn.execute(
            "SELECT id, name, category, stock, "
            "CASE WHEN stock<20 THEN 'CRITICAL' "
            "     WHEN stock<50 THEN 'LOW' ELSE 'OK' END AS status "
            "FROM products WHERE is_active=1 ORDER BY stock ASC").fetchall()
        conn.close()
        return {"items": [dict(r) for r in rows]}

    _MCP_SERVERS[name] = server
    _MCP_AUTH_PROVIDERS[name] = auth
    return server


# ═══════════════════════════════════════════════════════════════════
# MCP → LangChain tool bridge
# ═══════════════════════════════════════════════════════════════════

def _make_lc_tool(server: MCPServer, tool_name: str, token: str) -> StructuredTool:
    """Convert one MCP tool into a LangChain StructuredTool preserving its schema."""
    mcp_tool = server.tools[tool_name]
    spec = mcp_tool.parameters
    props = spec.get("properties", {})
    required = set(spec.get("required", []))

    # Build a Pydantic model dynamically from the JSON-Schema
    fields = {}
    for pname, pdef in props.items():
        py_type = {"string": str, "number": float, "integer": int,
                    "boolean": bool}.get(pdef.get("type","string"), str)
        default = ... if pname in required else ""
        fields[pname] = (py_type, Field(default=default,
                                         description=pdef.get("description","")))
    ArgSchema = type(f"{tool_name}_Args", (BaseModel,), {
        "__annotations__": {k: v[0] for k, v in fields.items()},
        **{k: v[1] for k, v in fields.items()},
    })

    def _call(**kwargs):
        # Drop empty-string defaults for optional args so MCP sees clean payload
        clean = {k: v for k, v in kwargs.items() if v not in ("", None)}
        result = server.call_tool(tool_name, clean, token=token)
        return json.dumps(result, ensure_ascii=False, indent=2)

    return StructuredTool.from_function(
        func=_call,
        name=tool_name,
        description=mcp_tool.description,
        args_schema=ArgSchema,
    )


def build_langchain_tools_from_mcp(server: MCPServer, token: str) -> list[StructuredTool]:
    """Return every MCP tool visible to ``token`` as LangChain tools."""
    tools: list[StructuredTool] = []
    for t in server.list_tools(token=token):
        tools.append(_make_lc_tool(server, t["name"], token))
    return tools


# ═══════════════════════════════════════════════════════════════════
# Agent factories (LangGraph create_react_agent)
# ═══════════════════════════════════════════════════════════════════

def _base_llm() -> ChatOpenAI:
    return ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4.1-nano"),
                      api_key=os.getenv("OPENAI_API_KEY"))


def _compose_system_prompt(server: MCPServer, token: str,
                            extra_sections: list[tuple[str, str]] | None = None) -> str:
    """Read the MCP resources visible to ``token`` and compose a system prompt.

    This is the practical realisation of the 3-primitives pattern: the
    *Resources* are read here and pasted into the system prompt so that
    the agent has the context it needs before touching any *Tool*.
    """
    parts = [
        "You are a data analyst agent for DataTech Vietnam.",
        "The DataTech database is your ONLY source of truth. Do NOT rely on "
        "general knowledge or training-data cut-offs — every time a user asks "
        "about revenue, customers, products or orders you MUST call a tool "
        "(query_revenue, list_products, or execute_sql) to fetch live data.",
        "If a tool returns an error, report it honestly. Never invent values.",
        "",
    ]

    for r in server.list_resources(token=token):
        content = server.read_resource(r["uri"], token=token)
        if isinstance(content, str):
            parts.append(f"## {r['name']} (MCP resource: {r['uri']})")
            parts.append(content)
            parts.append("")

    for title, body in (extra_sections or []):
        parts.append(f"## {title}")
        parts.append(body)
        parts.append("")

    return "\n".join(parts)


def build_analytics_agent(cred_factory: CredentialFactory,
                           session: SessionToken,
                           apply_skill: bool = False,
                           langfuse_handler=None,
                           agent_name: str = "analytics_agent"):
    """Build a LangGraph agent that talks to the analytics MCP server."""
    server = get_or_build_analytics_mcp(cred_factory)
    token = cred_factory.derive_mcp_token(session, "datatech-analytics-mcp")
    tools = build_langchain_tools_from_mcp(server, token)

    extras = []
    if apply_skill:
        project_root = os.path.abspath(os.path.join(
            os.path.dirname(__file__), ".."))
        skill = load_skill(os.path.join(project_root, "skills", "kpi-report-skill"))
        extras.append((f"Active Skill: {skill.name}", skill.to_system_prompt()))

    system_prompt = _compose_system_prompt(server, token, extras)
    agent = create_react_agent(model=_base_llm(), tools=tools,
                                prompt=system_prompt, name=agent_name)
    if langfuse_handler is not None:
        agent = agent.with_config({"callbacks": [langfuse_handler],
                                    "metadata": {"user_id": session.user_id,
                                                  "agent": agent_name}})
    return agent, server, token


def build_inventory_agent(cred_factory: CredentialFactory,
                           session: SessionToken,
                           langfuse_handler=None,
                           agent_name: str = "inventory_agent"):
    server = get_or_build_inventory_mcp(cred_factory)
    token = cred_factory.derive_mcp_token(session, "datatech-inventory-mcp")
    tools = build_langchain_tools_from_mcp(server, token)

    system_prompt = (
        "You are the inventory agent for DataTech Vietnam.\n"
        "Flag products with stock < 50 as LOW and < 20 as CRITICAL.\n"
        "Return a markdown table."
    )
    agent = create_react_agent(model=_base_llm(), tools=tools,
                                prompt=system_prompt, name=agent_name)
    if langfuse_handler is not None:
        agent = agent.with_config({"callbacks": [langfuse_handler],
                                    "metadata": {"user_id": session.user_id,
                                                  "agent": agent_name}})
    return agent, server, token


def build_writer_agent(cred_factory: CredentialFactory,
                        session: SessionToken,
                        langfuse_handler=None,
                        agent_name: str = "writer_agent"):
    """Writer does not need MCP tools; it is a pure LLM agent."""
    # Still ensure user is authorised to use the writer agent
    if "writer_agent" not in cred_factory.available_agents(session):
        raise PermissionError(
            f"User '{session.user_id}' has no grant for writer_agent")

    # Use create_react_agent with no tools so LangGraph wrapping is identical
    agent = create_react_agent(
        model=_base_llm(),
        tools=[],
        prompt=("You are the writer agent. "
                "Produce a concise (3-4 sentence) executive summary for C-suite "
                "stakeholders. Focus on key metrics and actionable insights."),
        name=agent_name)
    if langfuse_handler is not None:
        agent = agent.with_config({"callbacks": [langfuse_handler],
                                    "metadata": {"user_id": session.user_id,
                                                  "agent": agent_name}})
    return agent


# ═══════════════════════════════════════════════════════════════════
# Reset helper (useful for notebooks that re-run the Setup cell)
# ═══════════════════════════════════════════════════════════════════

def reset_builder_state():
    """Clear cached singletons — call at start of each notebook."""
    _MCP_SERVERS.clear()
    _MCP_AUTH_PROVIDERS.clear()
