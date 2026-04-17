"""Langfuse tracing helpers for the extensible-agents labs.

If ``LANGFUSE_ENABLED=true`` is set in the environment, calls to
``get_openai_client`` and ``get_langchain_handler`` return OpenAI / LangChain
objects that emit traces to Langfuse.  Otherwise they return plain, un-traced
equivalents so the labs also work fully offline.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("tracing")


def _is_enabled() -> bool:
    return os.getenv("LANGFUSE_ENABLED", "false").lower() == "true"


# ── Singleton langfuse client (used for ad-hoc trace_event calls) ──────────
_langfuse = None


def _get_langfuse():
    global _langfuse
    if _langfuse is None and _is_enabled():
        try:
            from langfuse import Langfuse
            _langfuse = Langfuse()
            logger.info("Langfuse tracing enabled.")
        except Exception as e:
            logger.warning("Langfuse init failed: %s", e)
    return _langfuse


# ── OpenAI client (auto-wrapped when tracing enabled) ─────────────────────
def get_openai_client(**kwargs):
    """Return an OpenAI client.  If Langfuse is on, every completion is traced."""
    api_key = kwargs.pop("api_key", os.getenv("OPENAI_API_KEY"))
    if _is_enabled():
        try:
            from langfuse.openai import OpenAI as TracedOpenAI
            client = TracedOpenAI(api_key=api_key, **kwargs)
            print("  [Langfuse] OpenAI client is traced  -> "
                  + os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"))
            return client
        except Exception as e:
            logger.warning("Langfuse OpenAI wrap failed: %s", e)
    from openai import OpenAI
    return OpenAI(api_key=api_key, **kwargs)


# ── LangChain callback handler for create_react_agent ────────────────────
def get_langchain_handler():
    """Return a LangChain callback handler for Langfuse, or ``None``."""
    if not _is_enabled():
        return None
    try:
        # Langfuse v3+
        from langfuse.langchain import CallbackHandler
    except ImportError:
        try:
            # Legacy v2
            from langfuse.callback import CallbackHandler  # type: ignore
        except ImportError:
            logger.warning("No Langfuse CallbackHandler available.")
            return None
    try:
        handler = CallbackHandler()
        print("  [Langfuse] LangChain CallbackHandler enabled.")
        return handler
    except Exception as e:
        logger.warning("Langfuse handler init failed: %s", e)
        return None


def trace_event(name: str, metadata: dict | None = None):
    lf = _get_langfuse()
    if lf is None:
        return None
    try:
        return lf.trace(name=name, metadata=metadata or {})
    except Exception:
        return None


def flush():
    lf = _get_langfuse()
    if lf is not None:
        try:
            lf.flush()
        except Exception:
            pass
