#!/usr/bin/env python3
"""Generate 3 notebooks for the Extensible Agents course.

Lab 1 — MCP Server + LangGraph (create_react_agent): the 3 MCP primitives,
        two tool-attachment methods, user-session-derived credentials,
        per-login access control.
Lab 2 — Adding Skills on top of Lab 1 for standardised, high-quality output.
Lab 3 — A2A Multi-Agent System: the supervisor discovers available agents
        from the user session, then routes tasks via A2A authentication.
"""
import os
from nb_common import md, code, notebook

NOTEBOOK_DIR = os.path.join(os.path.dirname(__file__), "notebooks")
os.makedirs(NOTEBOOK_DIR, exist_ok=True)


# ╔═══════════════════════════════════════════════════════════════════╗
# ║  LAB 1                                                            ║
# ╚═══════════════════════════════════════════════════════════════════╝
def lab1():
    cells = [
        md("""# Lab 1 — MCP Server with LangGraph `create_react_agent`

> **Mode: DEMO in class** | Time: ~40 min

## What you will learn
- The **three MCP primitives** — Tool, Resource, Prompt — and why an agent
  usually needs all three to behave correctly in production.
- How to derive **MCP bearer tokens from a user session** so nothing is
  hard-coded (user-owned credentials).
- Two ways to attach MCP tools to a LangGraph `create_react_agent`:
  1. **Discovery first, then wrap** — call `list_tools()` and build
     `StructuredTool` wrappers dynamically.
  2. **Direct attach** — decorate handler functions with `@tool` and hand
     the list straight to `create_react_agent`.
- How to observe every run with **Langfuse**.

## The 3 primitives cheat-sheet

| Primitive | Purpose                      | Analogy                              |
|-----------|------------------------------|--------------------------------------|
| **Tool**      | *Perform* an action (call API, run SQL, send email) | A button the agent can press         |
| **Resource**  | *Provide* read-only context (schema, rules, docs)   | A document the agent can read        |
| **Prompt**    | *Shape* the response (template with placeholders)   | A fill-in-the-blank form             |

> If you drop **Resource**, the agent has no schema -> invents table/column names -> wrong SQL.
> If you drop **Prompt**, the agent answers in any format it likes -> hard to QA.
> If you drop **Tool**, the agent cannot touch real data -> hallucinates.

---
"""),

        md("""## Step 1 — Environment

We start Jupyter from the dedicated conda env (`conda env create -f environment.yml`)
then select the **Extensible Agents (conda)** kernel.
"""),

        code("""import os, sys, json
_cwd = os.getcwd()
PROJECT_ROOT = os.path.abspath(os.path.join(_cwd, ".."))
if not os.path.isdir(os.path.join(PROJECT_ROOT, "lib")):
    PROJECT_ROOT = _cwd
sys.path.insert(0, os.path.join(PROJECT_ROOT, "lib"))

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# Ensure DB is available
from data import DB_PATH
if not os.path.exists(DB_PATH):
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "db"))
    from setup_database import create_database
    create_database()

print(f"Project root : {PROJECT_ROOT}")
print(f"Model        : {os.getenv('OPENAI_MODEL','gpt-4.1-nano')}")
print(f"Langfuse     : {'enabled' if os.getenv('LANGFUSE_ENABLED','').lower()=='true' else 'disabled'}")
"""),

        md("""## Step 2 — Identity, Grants, Credential Factory

Production agents never hold hard-coded secrets. The flow is:

```
User login  ─►  SessionToken
               (signed JWT-like artefact)
                      │
                      ▼
             CredentialFactory
                      │
           ┌──────────┴──────────┐
           ▼                     ▼
   MCP Bearer Token        A2A credentials
   (scoped to user)        (per agent)
```

Grants are decided by admins — in real life this is a ticket/approval flow
similar to "Claude asking you to authorise access to your GitHub".
"""),

        code("""from identity import IdentityProvider, GrantRegistry, CredentialFactory, seed_lab_users
from a2a_framework import A2AAuthProvider
from agent_builder import (get_or_build_analytics_mcp,
                            build_langchain_tools_from_mcp,
                            reset_builder_state)
reset_builder_state()

idp = IdentityProvider()
grants = GrantRegistry()
seed_lab_users(idp, grants)              # fake the HR "grant approval" step

cred_factory = CredentialFactory(idp, grants)
cred_factory.register_a2a_provider(A2AAuthProvider())

# Build the analytics MCP server once; this also registers its auth provider
mcp_server = get_or_build_analytics_mcp(cred_factory)

print("Registered users and grants:")
for uid in ["admin_thiem","analyst_duc","analyst_mai","viewer_nam"]:
    g = grants.get(uid)
    print(f"  {uid:13s}  agents={sorted(g.agent_access)}  "
          f"mcp_scopes={ {k:sorted(v) for k,v in g.mcp_scopes.items()} }")
"""),

        md("""## Step 3 — Login as **admin** (default for Sections 4–5)

Every following section uses `admin_thiem` so we can focus on the MCP mechanics
without tripping on grant errors. Section 6 will try *other* logins to show
the credential-derivation effect.
"""),

        code("""session = idp.login("admin_thiem", "admin456")
print(f"Logged in as: {session.display_name}")
print(f"Session id  : {session.session_id[:12]}...  expires in "
      f"{int(session.expires_at - session.created_at)}s")

mcp_token = cred_factory.derive_mcp_token(session, "datatech-analytics-mcp")
print(f"Derived MCP token (for analytics MCP): ...{mcp_token[-8:]}")
"""),

        md("""## Step 4 — The 3 MCP primitives, in detail

### 4a. TOOLS
Tools are functions the agent can **call**. Each tool declares a JSON-Schema
for its parameters and an `allowed_roles`/`required_scope` so the server
itself enforces access control.

> **Why this matters** — the description and JSON-Schema are what the LLM
> reads when picking a tool. Vague descriptions = wrong tool chosen.
"""),

        code("""for t in mcp_server.list_tools(mcp_token):
    print(f"- {t['name']}")
    print(f"   {t['description'][:110]}...")
    print(f"   params: {list(t['parameters']['properties'].keys())}")
"""),

        md("""### 4b. RESOURCES — this is the primitive students miss most often
Resources are **read-only context**. The agent does not "call" a resource; we
read it once and paste it into the system prompt before any reasoning begins.

In this lab the server exposes three:

- `datatech://schema`   — the DB schema (tables, columns, types, row counts)
- `datatech://rules`    — business rules (KPI thresholds, formatting)
- `datatech://company`  — high-level company info

Why a resource and not a static string in the system prompt? Two reasons:

1. **Access control** — the schema needs `sql:execute` scope. A viewer logging
   in would see the prompt without a schema section.
2. **Freshness** — updating the DB schema updates the agent's context on the
   next session, no re-deploy.
"""),

        code("""# The agent_builder already reads all visible resources when composing the system prompt.
# Here we just inspect them:
for r in mcp_server.list_resources(mcp_token):
    content = mcp_server.read_resource(r["uri"], mcp_token)
    print(f"--- {r['uri']} --- ({r['name']})")
    print(content if isinstance(content, str) else content)
    print()
"""),

        md("""### 4c. PROMPTS — templates to lock the output format
Prompts are parameterised templates the server hands back rendered. They
standardise *how* the agent should frame an answer.
"""),

        code("""for p in mcp_server.list_prompts():
    print(f"- {p['name']}: {p['description']}")

# Render one to see the effect
rendered = mcp_server.render_prompt(
    "revenue_analysis",
    region="Hanoi", region_id="HN",
    current_month="2025-03", prior_month="2025-02")
print("\\nrevenue_analysis template rendered:\\n")
print(rendered)
"""),

        md("""## Step 5 — Attach the MCP tools to a LangGraph agent

There are two common ways to wire MCP tools into `create_react_agent`. Both
are shown here so you can pick the one that matches your codebase.

### Method A — Discovery-first (recommended for dynamic, user-scoped agents)
Ask the MCP server *which* tools the current token may see, then wrap each
one as a LangChain `StructuredTool`. The tool list reflects the user's scopes
automatically.
"""),

        code("""from tracing import get_langchain_handler
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

langfuse_handler = get_langchain_handler()
llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL","gpt-4.1-nano"),
                  api_key=os.getenv("OPENAI_API_KEY"))

# Method A — discovery first, wrap, attach
lc_tools_A = build_langchain_tools_from_mcp(mcp_server, mcp_token)
print("Method A tools:", [t.name for t in lc_tools_A])
"""),

        code("""# Compose a system prompt from the MCP Resources (the classic pattern):
sys_parts = [
    "You are a senior data analyst for DataTech Vietnam.",
    "The DataTech database is your ONLY source of truth. "
    "You MUST call a tool for ANY question about revenue, products, "
    "customers, or orders. Never answer from memory.",
    "Follow the Business Rules exactly when formatting output.",
    "",
]
for r in mcp_server.list_resources(mcp_token):
    content = mcp_server.read_resource(r["uri"], mcp_token)
    if isinstance(content, str):
        sys_parts += [f"## {r['name']}  ({r['uri']})", content, ""]
system_prompt_A = "\\n".join(sys_parts)

agent_A = create_react_agent(model=llm, tools=lc_tools_A,
                              prompt=system_prompt_A,
                              name="analytics_agent")
if langfuse_handler:
    agent_A = agent_A.with_config({"callbacks": [langfuse_handler],
                                    "metadata": {"user_id": session.user_id,
                                                  "method": "A"}})
print("Agent A ready.")
"""),

        md("""### Method B — Direct `@tool` attach (concise, static tool set)
Sometimes you already have Python handler functions and just want them
wrapped. Decorate with `@tool` and pass the list directly.
"""),

        code("""from langchain_core.tools import tool

@tool
def query_revenue(region_id: str, start_month: str, end_month: str) -> str:
    \"\"\"Query monthly revenue by region_id (HN/HC/DN) over a date range (YYYY-MM).\"\"\"
    return json.dumps(mcp_server.call_tool(
        "query_revenue",
        {"region_id": region_id, "start_month": start_month, "end_month": end_month},
        token=mcp_token,
    ), ensure_ascii=False, indent=2)

@tool
def list_products(category: str = "") -> str:
    \"\"\"List products; pass an empty string for all.\"\"\"
    args = {"category": category} if category else {}
    return json.dumps(mcp_server.call_tool("list_products", args, token=mcp_token),
                      ensure_ascii=False, indent=2)

@tool
def execute_sql(query: str) -> str:
    \"\"\"Execute a read-only SQL SELECT query against the DataTech database.\"\"\"
    return json.dumps(mcp_server.call_tool("execute_sql", {"query": query},
                                            token=mcp_token),
                      ensure_ascii=False, indent=2)

lc_tools_B = [query_revenue, list_products, execute_sql]
agent_B = create_react_agent(model=llm, tools=lc_tools_B,
                              prompt=system_prompt_A, name="analytics_agent_B")
if langfuse_handler:
    agent_B = agent_B.with_config({"callbacks": [langfuse_handler],
                                    "metadata": {"user_id": session.user_id,
                                                  "method": "B"}})
print("Agent B ready — tools attached directly via @tool.")
"""),

        md("""### Run both agents on the same question
Identical output proves the two wiring styles are interchangeable.
"""),

        code("""def ask(agent, q, label):
    print(f"\\n{'='*60}\\n[{label}] {q}\\n{'-'*60}")
    result = agent.invoke({"messages": [{"role": "user", "content": q}]})
    print(result["messages"][-1].content)

ask(agent_A, "What was Hanoi revenue in January 2025?", "Method A")
"""),

        code("""ask(agent_B, "What was Hanoi revenue in January 2025?", "Method B")"""),

        md("""### A query that forces all 3 primitives to be used
To produce a correct KPI report the agent must:

- **read the Schema resource**   (know table/column names)
- **follow the Rules resource**  (NEEDS ATTENTION / STRONG GROWTH flags)
- **render the Prompt template** (use it as the question framing)
- **call the Tool** (`query_revenue` / `execute_sql`) to pull the numbers
"""),

        code("""rendered = mcp_server.render_prompt(
    "revenue_analysis",
    region="Hanoi", region_id="HN",
    current_month="2025-03", prior_month="2025-02")
ask(agent_A, rendered, "Prompt + Resource + Tool (Method A)")
"""),

        md("""## Step 6 — Different logins, different agents

Here is the punchline of the whole credential story. We log in as **three
different users** and build an agent for each. Because the MCP token is
derived from the session, each agent automatically sees only what the user's
grants permit.

Expected outcomes:

| User           | Grant set                                          | Behaviour                                         |
|----------------|----------------------------------------------------|---------------------------------------------------|
| `admin_thiem`  | all scopes on both MCP servers                     | can run SQL, get revenue, list products           |
| `analyst_mai`  | `revenue:read`, `products:read` only               | revenue works; SQL requests fall back / refused   |
| `viewer_nam`   | `products:read` only                               | only `list_products`; revenue / SQL denied        |
"""),

        code("""from agent_builder import build_langchain_tools_from_mcp

def build_agent_for_session(idp_session):
    try:
        tk = cred_factory.derive_mcp_token(idp_session, "datatech-analytics-mcp")
    except PermissionError as e:
        return None, f"Cannot build MCP token: {e}", []
    # Compose a system prompt from whichever resources this user can see
    sp_parts = [
        "You are a data analyst for DataTech Vietnam.",
        "The DataTech database is your ONLY source of truth. "
        "You MUST call a tool for any data question.",
        "If no suitable tool is available, tell the user plainly.",
        "",
    ]
    for r in mcp_server.list_resources(tk):
        content = mcp_server.read_resource(r["uri"], tk)
        if isinstance(content, str):
            sp_parts += [f"## {r['name']}", content, ""]
    tools = build_langchain_tools_from_mcp(mcp_server, tk)
    ag = create_react_agent(model=llm, tools=tools,
                             prompt="\\n".join(sp_parts),
                             name=f"agent_for_{idp_session.user_id}")
    if langfuse_handler:
        ag = ag.with_config({"callbacks": [langfuse_handler],
                              "metadata": {"user_id": idp_session.user_id}})
    return ag, tk, [t.name for t in tools]


question = "What was Hanoi revenue in January 2025?  If you cannot find it, say so."

for uid, pwd in [("admin_thiem","admin456"),
                  ("analyst_mai","mai123"),
                  ("viewer_nam","nam789")]:
    print(f"\\n========== login: {uid} ==========")
    sess = idp.login(uid, pwd)
    ag, tk, names = build_agent_for_session(sess)
    if ag is None:
        print("  Cannot build agent:", tk)
        continue
    print(f"  tools for this user: {names}")
    try:
        r = ag.invoke({"messages":[{"role":"user","content":question}]})
        print(f"  answer: {r['messages'][-1].content}")
    except Exception as e:
        print(f"  runtime error: {e}")
"""),

        md("""### What just happened?

- **`admin_thiem`** has every scope, so the agent exposes `query_revenue`,
  `list_products` and `execute_sql`. It answers with the exact figure.
- **`analyst_mai`** has `revenue:read` + `products:read` — `execute_sql` is
  hidden from her agent. She can still answer via `query_revenue`.
- **`viewer_nam`** only has `products:read` — her agent has a single tool.
  It cannot answer the revenue question, so it tells the user so (exactly the
  safe fallback we requested).

No credentials were hard-coded. Each agent's capability set is a strict
projection of `User -> Grants -> Session -> MCP token -> visible tools`.
"""),

        md("""## Step 7 — Observability

Whether Langfuse is enabled or not, the MCP server keeps a structured call
log with correlation IDs, per-call duration, and the calling client.
"""),

        code("""print("MCP call log (last 6 entries):")
for e in mcp_server.get_call_log()[-6:]:
    print(f"  cid={e['correlation_id']}  {e['tool']:16s}  "
          f"client={e['client_id']:30s}  {e['duration_ms']:.1f} ms")

from tracing import flush
flush()
"""),

        md("""## Takeaways

1. The **3 MCP primitives** — Tool, Resource, Prompt — serve different jobs.
   Omit Resource and the agent hallucinates; omit Prompt and the format drifts.
2. **User-owned credentials**: `login()` -> `SessionToken` -> `CredentialFactory`
   -> scoped MCP token. Agents are rebuilt per-session, not pre-configured.
3. `create_react_agent` accepts MCP tools whether you discover-and-wrap
   (Method A) or directly attach with `@tool` (Method B).
4. **Langfuse** traces attach via `create_react_agent(...).with_config(...)`.

**Lab 2** — add a **Skill** to the same agent and watch the output quality
jump without changing the tools.
"""),
    ]
    notebook(cells, "Lab1_MCP_Server_LangGraph_Agent.ipynb", NOTEBOOK_DIR)


# ╔═══════════════════════════════════════════════════════════════════╗
# ║  LAB 2                                                            ║
# ╚═══════════════════════════════════════════════════════════════════╝
def lab2():
    cells = [
        md("""# Lab 2 — Skills: standardise and upgrade the agent's output

> **Mode: DEMO in class** | Time: ~20 min

This lab reuses the Lab 1 agent builder. We do not change any MCP tool; we
just attach a **Skill** (a reusable workflow document) to the agent's system
prompt and compare the output quality.

## What a Skill is

A Skill is a folder containing:

```
kpi-report-skill/
  SKILL.md              # workflow, output format, examples
  references/
    kpi_format_rules.md # additional domain docs
```

Think of it as an **onboarding guide for a new analyst** — it teaches *how*
to do the job. MCP teaches *what tools exist*; the Skill teaches *how to
combine them into a quality deliverable*.
"""),

        md("""## Step 1 — Setup (identical to Lab 1)"""),

        code("""import os, sys, json
_cwd = os.getcwd()
PROJECT_ROOT = os.path.abspath(os.path.join(_cwd, ".."))
if not os.path.isdir(os.path.join(PROJECT_ROOT, "lib")):
    PROJECT_ROOT = _cwd
sys.path.insert(0, os.path.join(PROJECT_ROOT, "lib"))

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from data import DB_PATH
if not os.path.exists(DB_PATH):
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "db"))
    from setup_database import create_database
    create_database()

from identity import IdentityProvider, GrantRegistry, CredentialFactory, seed_lab_users
from a2a_framework import A2AAuthProvider
from agent_builder import build_analytics_agent, reset_builder_state
from tracing import get_langchain_handler, flush

reset_builder_state()
idp = IdentityProvider(); grants = GrantRegistry(); seed_lab_users(idp, grants)
cred_factory = CredentialFactory(idp, grants)
cred_factory.register_a2a_provider(A2AAuthProvider())

session = idp.login("admin_thiem", "admin456")
langfuse_handler = get_langchain_handler()
print(f"Logged in as: {session.display_name}")
"""),

        md("""## Step 2 — Inspect the Skill

This is a real file on disk (`skills/kpi-report-skill/SKILL.md`). Edit it and
re-run the notebook to see the effect — that is the "easy to change" property
of Skills.
"""),

        code("""from skill_loader import load_skill
skill_dir = os.path.join(PROJECT_ROOT, "skills", "kpi-report-skill")
skill = load_skill(skill_dir)

print(f"Name       : {skill.name}")
print(f"Triggers   : {skill.triggers}")
print(f"References : {list(skill.references.keys())}")
print()
print("Rendered system-prompt fragment:")
print("-"*50)
print(skill.to_system_prompt()[:700] + "...")
"""),

        md("""## Step 3 — Two agents: without vs with Skill

`build_analytics_agent(..., apply_skill=True)` just concatenates the Skill
system-prompt fragment onto the resource-derived prompt.
"""),

        code("""agent_plain,   server, _ = build_analytics_agent(cred_factory, session,
                                                   apply_skill=False,
                                                   langfuse_handler=langfuse_handler,
                                                   agent_name="analytics_no_skill")

agent_skilled, _, _ = build_analytics_agent(cred_factory, session,
                                             apply_skill=True,
                                             langfuse_handler=langfuse_handler,
                                             agent_name="analytics_with_skill")

print("Tools (same for both):",
      [t['name'] for t in server.list_tools(
         cred_factory.derive_mcp_token(session, 'datatech-analytics-mcp'))])
"""),

        md("""## Step 4 — Compare outputs

Ask both agents the **same question**. The Skill contributes a fixed table
format, explicit status flags, and a mandatory executive summary.
"""),

        code("""QUESTION = "Produce a KPI report: March 2025 vs February 2025, all three regions."

def ask(agent, q, label):
    print(f"\\n{'='*60}\\n[{label}]\\n{q}\\n{'-'*60}")
    r = agent.invoke({"messages":[{"role":"user","content":q}]})
    print(r["messages"][-1].content)

ask(agent_plain, QUESTION, "WITHOUT SKILL")
"""),

        code("""ask(agent_skilled, QUESTION, "WITH SKILL")"""),

        md("""### Observation

| aspect               | without Skill | with Skill |
|----------------------|---------------|------------|
| table format         | free-form     | fixed columns (Region / Current / Prior / Change / Status) |
| status flags         | absent        | `STRONG GROWTH` / `NEEDS ATTENTION` |
| executive summary    | sometimes     | always, max 3 sentences |
| re-run consistency   | drifts        | stable |

Same tools. Same data. The Skill shifted the **workflow**, not the access
layer — that is the whole point of the Skills primitive.
"""),

        md("""## Step 5 — Another question to show generality"""),

        code("""ask(agent_skilled,
     "List the top 5 products by profit margin. Apply the Business Rules.",
     "WITH SKILL — different question")
flush()
"""),

        md("""## Takeaways

- A **Skill** is just a text document + structured reference files.
- Attaching one to a LangGraph agent is one line of code (`apply_skill=True`).
- The Skill can be **versioned, reviewed, and edited** without touching the
  agent code — exactly the separation you want between domain knowledge
  (product/finance/legal) and agent plumbing (ML engineers).

**Lab 3** — wrap this agent (and two others) as **A2A remote agents**, then
let a supervisor auto-discover and delegate per-user requests.
"""),
    ]
    notebook(cells, "Lab2_Skills_Better_Output.ipynb", NOTEBOOK_DIR)


# ╔═══════════════════════════════════════════════════════════════════╗
# ║  LAB 3                                                            ║
# ╚═══════════════════════════════════════════════════════════════════╝
def lab3():
    cells = [
        md("""# Lab 3 — A2A Multi-Agent System with Session-Derived Credentials

> **Mode: DEMO in class** | Time: ~40 min

This lab stitches everything together.

## Architecture

```
          ┌────────────────────────────────────┐
          │          Identity Provider          │
          │  (users, roles, grants, sessions)   │
          └──────────────┬─────────────────────┘
                         │ SessionToken
                         ▼
          ┌────────────────────────────────────┐
          │       CredentialFactory             │
          │  derive MCP tokens + A2A creds      │
          └──────────────┬─────────────────────┘
                         │
         ┌───────────────┼────────────────┐
         │               │                │
         ▼               ▼                ▼
   [Analytics agent] [Inventory agent] [Writer agent]
    (Lab 1 + Skill)   (MCP inventory)   (pure LLM)
         │               │                │
         └───────┬───────┴────────┬───────┘
                 │                │
                 ▼                ▼
         ┌────────────────────────────────┐
         │  Supervisor (LangGraph)        │
         │  1. list user's granted agents │
         │  2. LLM-classify user request  │
         │  3. check agent is allowed     │
         │  4. derive A2A creds for agent │
         │  5. submit task                │
         └────────────────────────────────┘
                          │
                          ▼
                        User
```

## Auth flow

```
Login ─► Session ─┬─► MCP token(analytics-mcp)  ── used by Analytics agent
                  ├─► MCP token(inventory-mcp)  ── used by Inventory agent
                  ├─► A2A api-key (analytics)   ── used by Supervisor -> Analytics
                  ├─► A2A OAuth token (writer)  ── used by Supervisor -> Writer
                  └─► A2A api-key (inventory)   ── used by Supervisor -> Inventory
```

No credential is stored long-term anywhere except the session; revoking the
session drops every downstream token on the next expiry.
"""),

        md("""## Step 1 — Boot the platform"""),

        code("""import os, sys, json, time
from typing import TypedDict
_cwd = os.getcwd()
PROJECT_ROOT = os.path.abspath(os.path.join(_cwd, ".."))
if not os.path.isdir(os.path.join(PROJECT_ROOT, "lib")):
    PROJECT_ROOT = _cwd
sys.path.insert(0, os.path.join(PROJECT_ROOT, "lib"))

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from data import DB_PATH
if not os.path.exists(DB_PATH):
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "db"))
    from setup_database import create_database
    create_database()

from identity import IdentityProvider, GrantRegistry, CredentialFactory, seed_lab_users
from a2a_framework import (AgentCard, SecurityScheme, A2AAuthProvider,
                            RemoteAgent, ClientAgent, AgentRegistry, TaskStatus)
from agent_builder import (build_analytics_agent, build_inventory_agent,
                            build_writer_agent, reset_builder_state)
from tracing import get_openai_client, get_langchain_handler, flush

reset_builder_state()
idp = IdentityProvider(); grants = GrantRegistry(); seed_lab_users(idp, grants)
a2a_auth = A2AAuthProvider()
cred_factory = CredentialFactory(idp, grants)
cred_factory.register_a2a_provider(a2a_auth)

oai = get_openai_client()
lh = get_langchain_handler()
MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-nano")
print("Platform ready.")
"""),

        md("""## Step 2 — Register 3 Remote Agents (each with its own Agent Card)

Each Remote Agent advertises its security scheme so supervisors know how to
authenticate.
"""),

        code("""# Agent cards + handlers ---------------------------------------------------
analytics_card = AgentCard(
    name="analytics_agent",
    description="Queries revenue, runs SQL, applies KPI Skill for reports.",
    endpoint="agent://analytics",
    capabilities=["kpi_report","revenue_query","data_analysis"],
    security=SecurityScheme(scheme_type="apiKey"))

inventory_card = AgentCard(
    name="inventory_agent",
    description="Product stock analysis, flags LOW/CRITICAL items.",
    endpoint="agent://inventory",
    capabilities=["inventory_check","stock_alert"],
    security=SecurityScheme(scheme_type="apiKey"))

writer_card = AgentCard(
    name="writer_agent",
    description="Writes concise executive summaries for C-suite audiences.",
    endpoint="agent://writer",
    capabilities=["executive_summary","report_writing"],
    security=SecurityScheme(scheme_type="oauth2"))

# The Remote Agent wraps the LangGraph agent built from Lab 1/2 ------------
def make_remote(card, handler_map):
    ra = RemoteAgent(card=card, auth_provider=a2a_auth)
    for cap, fn in handler_map.items():
        ra.register_handler(cap, fn)
    return ra

# Register cards in a discoverable registry
registry = AgentRegistry()
for c in [analytics_card, inventory_card, writer_card]:
    registry.register(c)
print("Registry contents:")
for c in registry.list_all():
    print(f"  {c.name:17s} auth={c.security.scheme_type:7s} "
          f"capabilities={c.capabilities}")
"""),

        md("""## Step 3 — User login & dynamic agent instantiation

The supervisor builds each Remote Agent **per session** — the underlying
LangGraph agent is created on demand with the caller's credentials, so every
MCP request is auditable back to the user.
"""),

        code("""def bind_user(session):
    \"\"\"Instantiate the three Remote Agents for *this* user session.\"\"\"
    # --- Analytics (uses Lab 1+2 builder, with Skill on) -----------------
    lg_analytics, _, _ = build_analytics_agent(cred_factory, session,
                                                 apply_skill=True,
                                                 langfuse_handler=lh)
    def h_kpi(data):
        msg = data.get("request","KPI report")
        r = lg_analytics.invoke({"messages":[{"role":"user","content":msg}]})
        return {"report": r["messages"][-1].content}
    analytics_remote = make_remote(analytics_card, {"kpi_report": h_kpi,
                                                      "revenue_query": h_kpi,
                                                      "data_analysis": h_kpi})

    # --- Inventory --------------------------------------------------------
    try:
        lg_inv, _, _ = build_inventory_agent(cred_factory, session,
                                               langfuse_handler=lh)
        def h_inv(data):
            r = lg_inv.invoke({"messages":[{"role":"user",
                 "content":data.get("request","Stock status summary")}]})
            return {"report": r["messages"][-1].content}
        inventory_remote = make_remote(inventory_card,
                                        {"inventory_check": h_inv,
                                         "stock_alert": h_inv})
    except PermissionError:
        inventory_remote = None

    # --- Writer (pure LLM) ------------------------------------------------
    try:
        lg_writer = build_writer_agent(cred_factory, session,
                                         langfuse_handler=lh)
        def h_sum(data):
            # Accept either 'data' (already-produced text) or 'request' (raw user query)
            payload = data.get("data") or data.get("request") or ""
            r = lg_writer.invoke({"messages":[{"role":"user",
                 "content":"Summarise:\\n" + payload}]})
            return {"summary": r["messages"][-1].content}
        writer_remote = make_remote(writer_card,
                                     {"executive_summary": h_sum,
                                      "report_writing":  h_sum})
    except PermissionError:
        writer_remote = None

    return {"analytics_agent": analytics_remote,
            "inventory_agent": inventory_remote,
            "writer_agent":    writer_remote}
"""),

        md("""## Step 4 — Supervisor with auto-discovery

The Supervisor does four things per user request:

1. **Classify** the request with an LLM — pick the capability needed.
2. **Discover** which agents advertise that capability (via the Registry).
3. **Intersect** with the user's grants — drop anything the user cannot use.
4. **Derive credentials** from the session and **delegate** the task via A2A.

It is called "auto" because the supervisor picks and connects on its own —
the user never names an agent.
"""),

        code("""def classify_intent(request: str) -> str:
    \"\"\"Choose one capability from the catalogue.\"\"\"
    catalogue = ["kpi_report", "inventory_check", "executive_summary", "direct"]
    resp = oai.chat.completions.create(
        model=MODEL,
        messages=[{"role":"system","content":
            "Pick exactly one label describing the user request. "
            "Options: " + ", ".join(catalogue)
            + ". Reply with the label only."},
            {"role":"user","content":request}],
    )
    label = (resp.choices[0].message.content or "").strip().lower()
    return label if label in catalogue else "direct"


def run_supervisor(session, request: str,
                     remotes: dict, verbose: bool = True):
    \"\"\"End-to-end supervisor pass for one user request.\"\"\"
    cap = classify_intent(request)
    if verbose: print(f"  [supervisor] classified -> {cap}")
    if cap == "direct":
        return f"I can help with KPI reports, inventory status, or exec summaries."

    # Discover who can handle this
    candidates = registry.search(cap)
    allowed_names = cred_factory.available_agents(session)
    # keep only agents this user has a grant for
    candidates = [c for c in candidates if c.name in allowed_names]
    if not candidates:
        return f"Your account has no agent authorised to handle '{cap}'."

    chosen = candidates[0]
    if verbose: print(f"  [supervisor] chosen agent -> {chosen.name} "
                       f"({chosen.security.scheme_type})")

    ra = remotes.get(chosen.name)
    if ra is None:
        return f"Selected agent '{chosen.name}' is not bound for this session."

    # Derive A2A credentials from the session
    creds = cred_factory.derive_a2a_credentials(session, chosen.name,
                                                  chosen.security.scheme_type)
    client = ClientAgent(f"supervisor_for_{session.user_id}")
    client.register_remote(ra)
    client.set_credentials(chosen.endpoint, creds)

    task = client.submit_task(chosen.endpoint, cap, {"request": request})
    if task.status == TaskStatus.COMPLETED:
        out = task.output_data
        return out.get("report") or out.get("summary") or str(out)
    return f"[{task.status.value}] {task.error}"
"""),

        md("""## Step 5 — Run as `admin_thiem` (full access)"""),

        code("""session_admin = idp.login("admin_thiem", "admin456")
remotes_admin = bind_user(session_admin)

for q in [
    "Produce a KPI report: March 2025 vs February 2025, all regions.",
    "Which products are running low on stock?",
    "Write an executive summary about our Q1 2025 sales.",
    "Hello, what can you do?",
]:
    print(f"\\n========== {q} ==========")
    print(run_supervisor(session_admin, q, remotes_admin))
"""),

        md("""## Step 6 — Same supervisor, different user = different results

`analyst_duc` has `analytics_agent` + `writer_agent` but **no inventory
access**. The supervisor detects this during grant-intersection and returns
a policy-driven refusal. No data leaks, no stack traces.
"""),

        code("""session_duc = idp.login("analyst_duc", "duc123")
remotes_duc = bind_user(session_duc)

for q in [
    "Produce a KPI report: March 2025 vs February 2025, all regions.",
    "Which products are running low on stock?",
    "Write an executive summary of the KPI report you just produced.",
]:
    print(f"\\n========== analyst_duc: {q} ==========")
    print(run_supervisor(session_duc, q, remotes_duc))
"""),

        md("""## Step 7 — `analyst_mai` (has only `analytics_agent`)"""),

        code("""session_mai = idp.login("analyst_mai", "mai123")
remotes_mai = bind_user(session_mai)

for q in [
    "Give me a KPI report for March 2025 vs February 2025.",
    "Write an executive summary.",         # no writer grant
    "What stock is low?",                  # no inventory grant
]:
    print(f"\\n========== analyst_mai: {q} ==========")
    print(run_supervisor(session_mai, q, remotes_mai))

flush()
"""),

        md("""## Takeaways

- The supervisor is a thin routing layer. **Everything security-relevant**
  (who may call which agent, with which MCP scopes) flows from the
  SessionToken through the CredentialFactory.
- Each Remote Agent keeps its **own Agent Card + security scheme**, mixing
  API-key and OAuth agents in the same system.
- Adding a new specialist is three steps: build it with the agent_builder,
  write its Agent Card, register it — the supervisor picks it up via
  Registry discovery automatically.
- **Langfuse** traces every LLM call with `user_id` metadata, so you can
  filter the UI by user and audit any session end-to-end.
"""),
    ]
    notebook(cells, "Lab3_A2A_MultiAgent_Auth_LangGraph.ipynb", NOTEBOOK_DIR)


# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    for f in os.listdir(NOTEBOOK_DIR):
        if f.endswith(".ipynb"):
            os.remove(os.path.join(NOTEBOOK_DIR, f))
    print("Generating notebooks...")
    lab1()
    lab2()
    lab3()
    print("\nAll 3 notebooks generated in notebooks/")
