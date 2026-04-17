#!/usr/bin/env python3
"""Verify environment setup for Extensible Agents Lab (v2)."""
import sys, os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def main():
    print("=" * 55)
    print("Extensible Agents Lab v2 — Environment Check")
    print("=" * 55)
    ok = True
    v = sys.version_info
    print(f"\nPython: {v.major}.{v.minor}.{v.micro}", end="")
    print(" [OK]" if v.major == 3 and v.minor >= 10 else " [WARN] 3.10+ recommended")

    for label, pkgs in [("Required", {"openai":"openai","dotenv":"python-dotenv",
        "langchain_openai":"langchain-openai","pydantic":"pydantic"}),
        ("Optional", {"langgraph":"langgraph"})]:
        print(f"\n{label} packages:")
        for imp, pip in pkgs.items():
            try: __import__(imp); print(f"  [OK] {pip}")
            except ImportError: print(f"  [MISSING] {pip}"); ok = label == "Required" and False or ok

    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    key = os.getenv("OPENAI_API_KEY", "")
    print(f"\n.env: {'[OK] ***' + key[-6:] if key else '[MISSING]'}, model={os.getenv('OPENAI_MODEL','?')}")
    db = os.path.join(PROJECT_ROOT, "db", "datatech.db")
    print(f"Database: {'[OK]' if os.path.exists(db) else '[RUN] python db/setup_database.py'}")
    print(f"Skill: {'[OK]' if os.path.exists(os.path.join(PROJECT_ROOT,'skills','kpi-report-skill','SKILL.md')) else '[MISSING]'}")

    print("\nOpenAI API: ", end="")
    try:
        from openai import OpenAI
        r = OpenAI(api_key=key).chat.completions.create(
            model=os.getenv("OPENAI_MODEL","gpt-4.1-nano"),
            messages=[{"role":"user","content":"Say OK"}], max_tokens=5)
        print(f"[OK] {r.choices[0].message.content}")
    except Exception as e: print(f"[FAILED] {e}"); ok = False

    print("\n" + "=" * 55)
    print("All checks passed!" if ok else "Fix issues above.")
    print("=" * 55)

if __name__ == "__main__":
    main()
