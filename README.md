# Extensible Agents — MCP + Skills + A2A, with user-owned credentials

Three production-style hands-on labs. Every lab uses the same identity layer,
so what students learn in Lab 1 carries through to Lab 3 unchanged.

| Lab  | Topic                                            | Outcome |
|------|--------------------------------------------------|---------|
| **1** | MCP Server with LangGraph `create_react_agent` | Understand the three MCP primitives (Tool, Resource, Prompt) and see per-user tool visibility driven by session-derived credentials. |
| **2** | Skills layer on top of Lab 1                    | Attach a Skill to the same agent and compare output quality without/with. |
| **3** | A2A multi-agent system                          | Wrap the Lab 1/2 agent as a Remote Agent, add a Writer and an Inventory agent, and have a supervisor auto-discover + delegate tasks based on the user's grants. |

Every lab routes its LLM calls through **Langfuse** when enabled
(`LANGFUSE_ENABLED=true`), giving you a fully-filterable per-user trace UI.

---

## Big-picture architecture

```
 ┌─────────────┐   (fake) login    ┌──────────────────────┐
 │   User      │──────────────────▶│   Identity Provider  │
 │  analyst_x  │  user_id + pwd    │ (users / sessions)   │
 └─────────────┘                   └──────────┬───────────┘
                                              │ SessionToken
                                              ▼
                                   ┌──────────────────────┐
                                   │   GrantRegistry      │
                                   │  mcp_scopes / agents │
                                   └──────────┬───────────┘
                                              │
                                              ▼
                                   ┌──────────────────────┐
                                   │  CredentialFactory   │
                                   │  derive scoped tokens│
                                   └───┬──────────────┬───┘
                                       │              │
                       MCP bearer      │              │   A2A creds
                       (scoped)        ▼              ▼   (apiKey / OAuth)
                           ┌───────────────────┐ ┌────────────────────┐
                           │  MCP Server(s)    │ │  Remote Agents     │
                           │  Tool / Resource  │ │  Agent Cards       │
                           │  Prompt           │ │  Tasks             │
                           └────────┬──────────┘ └──────────┬─────────┘
                                    │                       │
                                    ▼                       ▼
                              SQLite DB                  Supervisor
                                                         (LangGraph)
```

### Authentication flow

```
login(user, pwd)         ────► Signed SessionToken   (ttl: 1h)
                                   │
                                   ▼
   CredentialFactory.derive_mcp_token(session, server_name)
                                   │
                                   ▼    (scopes intersected with grants)
                     MCP Bearer  →  MCP Server validates, returns tool list
                                   │
                                   ▼
   create_react_agent(tools = scoped list, prompt = Resources + Skill)

   -- later, for A2A --
   CredentialFactory.derive_a2a_credentials(session, agent_id, scheme)
      scheme = apiKey  → per-session API key "sk.<user>.<agent>.<sid>"
      scheme = oauth2  → client-credentials token for that agent
```

All credentials ride the session — if the session is revoked or expires,
every downstream token becomes useless at its next expiry.

---

## Quick start

```bash
# 1. Set your API keys
cp .env.example .env
# edit .env: OPENAI_API_KEY, optional LANGFUSE_*

# 2. Create the conda env and register the Jupyter kernel
conda env create -f environment.yml --solver=libmamba
conda activate extensible-agents
python -m ipykernel install --user --name extensible-agents \
       --display-name "Extensible Agents (conda)"

# 3. Build the lab database and generate notebooks
python db/setup_database.py
python generate_notebooks.py

# 4. Run everything (tests first, then the notebooks)
python -m pytest tests/               # 56 tests
jupyter nbconvert --to notebook --execute --inplace \
        --ExecutePreprocessor.kernel_name=extensible-agents \
        --ExecutePreprocessor.timeout=240 \
        notebooks/Lab1_MCP_Server_LangGraph_Agent.ipynb \
        notebooks/Lab2_Skills_Better_Output.ipynb \
        notebooks/Lab3_A2A_MultiAgent_Auth_LangGraph.ipynb

# 5. Or open interactively
jupyter notebook notebooks/
```

## Repository layout

```
extensible_agents/
├── .env / .env.example
├── environment.yml         # conda env definition
├── requirements.txt
├── generate_notebooks.py   # single source of truth for the 3 labs
├── nb_common.py            # small helpers used by the generator
│
├── db/
│   ├── setup_database.py   # creates the DataTech Vietnam SQLite DB
│   └── datatech.db         # (git-ignored)
│
├── lib/
│   ├── identity.py         # IdP, GrantRegistry, SessionToken, CredentialFactory
│   ├── mcp_framework.py    # MCP server + OAuth bearer auth + SQL sandbox
│   ├── a2a_framework.py    # Agent cards, remote agents, auth
│   ├── skill_loader.py     # read SKILL.md + references
│   ├── agent_builder.py    # build_analytics/inventory/writer agents
│   └── tracing.py          # Langfuse OpenAI + LangChain handler
│
├── skills/
│   └── kpi-report-skill/
│       ├── SKILL.md
│       └── references/kpi_format_rules.md
│
├── notebooks/
│   ├── Lab1_MCP_Server_LangGraph_Agent.ipynb
│   ├── Lab2_Skills_Better_Output.ipynb
│   └── Lab3_A2A_MultiAgent_Auth_LangGraph.ipynb
│
├── scripts/
│   ├── setup_check.py
│   ├── mcp_server_demo.py
│   ├── a2a_demo.py
│   └── supervisor_flow.py
│
├── config/agent_cards/*.json
└── tests/          # 56 tests, all passing
```

## Fake lab users

All logins are fake; the identity layer simulates a proper OAuth flow so
students focus on delegation, not login UX.

| user_id       | password   | roles               | MCP analytics scopes                                   | Allowed agents                            |
|---------------|-----------|---------------------|--------------------------------------------------------|-------------------------------------------|
| admin_thiem   | admin456  | admin, analyst      | all                                                    | analytics, writer, inventory              |
| analyst_duc   | duc123    | analyst             | revenue, products, sql                                 | analytics, writer                         |
| analyst_mai   | mai123    | analyst             | revenue, products                                      | analytics                                 |
| viewer_nam    | nam789    | viewer              | products                                               | (none)                                    |

Grant decisions are expressed in `lib/identity.py::seed_lab_users`. In
production this would be an admin UI / approval ticket, not code.

## Langfuse

Optional but recommended. Set `LANGFUSE_ENABLED=true` plus the usual
`LANGFUSE_SECRET_KEY` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_HOST` and every
notebook cell that runs an agent will:

1. Wrap the OpenAI client (`tracing.get_openai_client`).
2. Attach a `CallbackHandler` to `create_react_agent` (`tracing.get_langchain_handler`).

Traces land with `metadata.user_id` set so you can filter the Langfuse UI by
user and audit any session end to end.

## Tests

```
pytest tests/                # 56 pass
```

The test suite covers the MCP server, the A2A layer, Skills, and — most
importantly — the identity / grant / credential-factory flow.
