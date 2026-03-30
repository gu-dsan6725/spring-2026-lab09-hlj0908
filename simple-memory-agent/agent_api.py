from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import os
import uuid
import logging
import re

from dotenv import load_dotenv

from agent import Agent

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Memory Agent API",
    description="Multi-tenant conversational agent with semantic memory",
    version="1.0.0",
)

# ONE Agent instance per run_id
_session_cache: Dict[str, Agent] = {}
_session_owner: Dict[str, str] = {}


class InvocationRequest(BaseModel):
    user_id: str = Field(..., description="User identifier for memory isolation")
    run_id: Optional[str] = Field(None, description="Session ID")
    query: str = Field(..., description="User message")
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata"
    )


class InvocationResponse(BaseModel):
    user_id: str
    run_id: str
    response: str
    metadata: Optional[Dict[str, Any]] = None


def _resolve_api_key() -> str:
    api_key = (
        os.getenv("GROQ_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("GEMINI_API_KEY")
    )
    if not api_key:
        raise RuntimeError(
            "No LLM API key found. Set one of GROQ_API_KEY, ANTHROPIC_API_KEY, "
            "OPENAI_API_KEY, or GEMINI_API_KEY."
        )
    return api_key


def _clean_response_text(text: Optional[str]) -> str:
    if text is None:
        return ""

    cleaned = str(text).strip()

    # Remove fake function / tool call text that the model may print
    patterns = [
        r"\(insert_memory>\{.*?\}\)</function>",
        r"\(search_memory>\{.*?\}\)</function>",
        r"\(web_search>\{.*?\}\)</function>",
        r"\(function=[^)]+\>\{.*?\}\)",
        r"<insert_memory>.*?</insert_memory>",
        r"<search_memory>.*?</search_memory>",
        r"<web_search>.*?</web_search>",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.DOTALL)

    # Remove lines like "Tool #1: search_memory"
    cleaned = re.sub(r"Tool\s+#\d+:\s+[A-Za-z_][A-Za-z0-9_]*\s*", "", cleaned)

    # Trim excessive blank lines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    return cleaned.strip()


def _maybe_retry_for_memory(agent: Agent, query: str, response_text: str) -> str:
    lowered = response_text.lower()

    failure_markers = [
        "don't have any information",
        "do not have any information",
        "don't know",
        "do not know",
        "starting fresh",
        "i don't have any memories",
        "i dont have any memories",
        "null",
    ]

    if any(marker in lowered for marker in failure_markers):
        try:
            q = query.lower()

            if "remember about me" in q:
                retry = agent.chat(
                    "Summarize what you know about me from our previous conversations."
                )
                retry = _clean_response_text(retry)
                if retry:
                    return retry

            if "what project am i working on" in q or "what project did i mention" in q:
                retry = agent.chat(
                    "What project did I mention earlier in our conversations?"
                )
                retry = _clean_response_text(retry)
                if retry:
                    return retry

            if "what programming languages do i like" in q:
                retry = agent.chat(
                    "What programming language preference did I mention earlier?"
                )
                retry = _clean_response_text(retry)
                if retry:
                    return retry
        except Exception:
            logger.exception("Retry-for-memory failed")

    return response_text


def _get_or_create_agent(user_id: str, run_id: str) -> Agent:
    if run_id in _session_cache:
        owner = _session_owner.get(run_id)
        if owner is not None and owner != user_id:
            raise HTTPException(
                status_code=400,
                detail=f"run_id '{run_id}' already belongs to a different user."
            )
        return _session_cache[run_id]

    api_key = _resolve_api_key()
    agent = Agent(user_id=user_id, run_id=run_id, api_key=api_key)
    _session_cache[run_id] = agent
    _session_owner[run_id] = user_id
    return agent


@app.get("/ping")
def ping() -> Dict[str, str]:
    return {
        "status": "ok",
        "message": "Memory Agent API is running"
    }


@app.post("/invocation", response_model=InvocationResponse)
def invocation(req: InvocationRequest) -> InvocationResponse:
    try:
        run_id = req.run_id or str(uuid.uuid4())[:8]
        agent = _get_or_create_agent(req.user_id, run_id)

        response_text = agent.chat(req.query)
        response_text = _clean_response_text(response_text)
        response_text = _maybe_retry_for_memory(agent, req.query, response_text)

        if not response_text:
            response_text = "I don't have that information available right now."

        return InvocationResponse(
            user_id=req.user_id,
            run_id=run_id,
            response=response_text,
            metadata=req.metadata,
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Invocation failed")
        raise HTTPException(status_code=500, detail=str(e))