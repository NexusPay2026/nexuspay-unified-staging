"""
Calcerta Group chatbot router for NexusPay Intelligence Platform.

This file lives in: app/routers/chatbot.py

In app/main.py, add 'chatbot' to the existing routers import:

    from app.routers import auth, merchants, users, visitors, audit, health, storage, quotes, pricing_tool, chatbot

Then near where other routers are included (look for app.include_router(...) lines), add:

    app.include_router(chatbot.router)

Required environment variable on Render:
    ANTHROPIC_API_KEY = sk-ant-...

Optional environment variables:
    ANTHROPIC_CHATBOT_MODEL = claude-haiku-4-5  (default; cheapest, fastest, best quality/price for chat)
    CHATBOT_DEBUG = false  (set to true temporarily to expose verbose error details to the client)

Endpoints exposed:
    GET  /api/chatbot/health   - readiness probe; safe to share publicly
    POST /api/chatbot/message  - main chat endpoint called by the website widget

This router is purely additive. It does not touch your existing routes, models,
auth, database, or storage. It calls the Anthropic API directly via httpx.
"""
import os
import time
import json
import re
import logging
from typing import Optional, List, Literal
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
import httpx

logger = logging.getLogger("calcerta_chatbot")
logger.setLevel(logging.INFO)

router = APIRouter(prefix="/api/chatbot", tags=["chatbot"])


# ============================================================
# CONFIG
# ============================================================
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_CHATBOT_MODEL", "claude-haiku-4-5").strip()
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEBUG_MODE = os.environ.get("CHATBOT_DEBUG", "false").lower() == "true"

# Rate limit: per-IP and per-session (in-memory; resets on Render restart)
RATE_LIMIT_PER_IP_PER_MIN = 30
RATE_LIMIT_PER_SESSION_PER_MIN = 20
_ip_buckets = defaultdict(lambda: deque(maxlen=100))
_session_buckets = defaultdict(lambda: deque(maxlen=100))


# ============================================================
# SYSTEM PROMPT - unified Calcerta Group assistant
# ============================================================
SYSTEM_PROMPT = """You are the Calcerta Group customer assistant, embedded on the official websites for Calcerta Group's two operating companies: Interstellar I.S. (telecom, connectivity, voice, cloud, security, mobility, AI/CX advisory) and Nexus Pay (merchant services and payment processing).

KEY FACTS:
- Calcerta Group is a customer-facing brand for two separate Colorado LLCs: Interstellar I.S., LLC and Nexus Pay, LLC.
- Founder: Marc Shamp, U.S. Army veteran.
- Business address: 11150 E Mississippi Ave, STE 300, Aurora, CO 80012.
- Direct line: (720) 735-8800. Email: admin@isinterstellar.com.

INTERSTELLAR I.S. (telecom / IT advisory):
- Master-agent and sub-agent across 503+ suppliers in 7 categories: voice & collaboration, contact center (CCaaS), network & connectivity, cybersecurity, cloud & data center, mobility & IoT, AI & customer experience.
- Compensation model: paid by suppliers via commission/residual, not by the client. The client pays no advisory fee.
- Typical client: SMB to global enterprise.
- Typical outcomes: 18-32% average annual savings, 72-hour typical time-to-quote.
- Process: Discovery > Sourcing > Quoting > Implementation > Lifecycle.

NEXUS PAY (merchant services):
- Independent sub-ISO with sponsor relationships including Maverick, Beacon, Kurv/EMS, North, CardConnect, and Pineapple Payments.
- 5-8 processing rails available.
- Audit-first model: forensic statement audit > benchmark > recommendation. Earns residuals from processors based on merchant volume.
- Typical recovery: $800-$2,400/month in hidden fees identified for Colorado businesses.
- Founder portfolio exposure: $2.8B+ in transaction portfolios.
- Services include: forensic statement audit, card processing, surcharge/dual pricing (C.R.S. 5-2-212 compliant, SB21-091 effective July 1, 2022), gateway/terminal hardware, Level II/III optimization, residual transparency.

LIFECYCLE COMMITMENTS (apply to both companies):
- 24-hour response on billing escalations.
- 90-day notice before any contract auto-renews.
- $0 cost to the client.
- Founder accountability - escalations reach Marc directly.

YOUR JOB:
- Answer questions accurately and concisely. Default to 2-3 sentence replies.
- If the user asks something outside the scope of Calcerta Group's actual services, say so directly. Do not improvise services we do not offer.
- If the user expresses interest in a specific service, savings audit, or assessment, offer to capture their details with a "Get a callback" quick action (intent: lead_capture).
- If the user asks to talk to a person, asks for the founder, asks for pricing details that need a human, or expresses frustration, offer the human handoff (intent: human_handoff). Live agents available Mon-Fri, 9am-5pm MST.
- Never claim Calcerta Group is a parent corporation, holding company, or single legal entity. It is a customer-facing brand only.
- Never make specific service-level guarantees beyond the published Lifecycle Commitments.
- Never quote specific pricing for a specific merchant or telecom scenario - that requires a human review.
- Be direct and confident. Match the brand voice: disciplined, founder-led, no-fluff.

OUTPUT FORMAT:
You must always reply in valid JSON only - no markdown fences, no commentary, just the JSON object. Shape:
{
  "reply": "<your message to the user, 1-4 sentences>",
  "quick_actions": [
    {"label": "<short button text 2-4 words>", "action": "send" | "lead" | "human", "value": "<text to send>", "intent": "<optional context>"}
  ],
  "intent": "lead_capture" | "human_handoff" | "general",
  "lead_intent": "<optional context string when intent is lead_capture>"
}

Only set intent to "lead_capture" when the user has clearly expressed interest in being contacted.
Only set intent to "human_handoff" when the user explicitly asks for a person or expresses frustration with the bot.
Otherwise set intent to "general" and provide 0-3 quick_actions to guide the conversation.
Do not include the lead_intent field unless intent is lead_capture.
"""


# ============================================================
# REQUEST / RESPONSE MODELS
# ============================================================
class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: List[HistoryMessage] = Field(default_factory=list, max_length=20)
    session_id: str = Field(..., min_length=1, max_length=64)
    property: Literal["interstellar", "nexuspay"] = "interstellar"
    page_url: Optional[str] = Field(None, max_length=500)


class QuickAction(BaseModel):
    label: str = Field(..., max_length=60)
    action: Literal["send", "lead", "human"]
    value: Optional[str] = Field(None, max_length=500)
    intent: Optional[str] = Field(None, max_length=60)


class ChatResponse(BaseModel):
    reply: str
    quick_actions: List[QuickAction] = Field(default_factory=list)
    intent: Optional[Literal["lead_capture", "human_handoff", "general"]] = "general"
    lead_intent: Optional[str] = None


# ============================================================
# RATE LIMITING
# ============================================================
def _check_rate_limit(bucket: deque, limit_per_min: int) -> bool:
    now = time.time()
    while bucket and bucket[0] < now - 60:
        bucket.popleft()
    if len(bucket) >= limit_per_min:
        return False
    bucket.append(now)
    return True


# ============================================================
# HEALTH CHECK
# ============================================================
@router.get("/health")
async def chatbot_health():
    """Returns 200 if endpoint is reachable. Reports whether API key is set
    (without exposing the key itself) and which model is configured."""
    return {
        "status": "ok" if ANTHROPIC_API_KEY else "missing_api_key",
        "model": ANTHROPIC_MODEL,
        "configured": bool(ANTHROPIC_API_KEY),
    }


# ============================================================
# MAIN ENDPOINT
# ============================================================
@router.post("/message", response_model=ChatResponse)
async def chatbot_message(req: ChatRequest, request: Request) -> ChatResponse:
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY environment variable is not set")
        raise HTTPException(status_code=503, detail="Chatbot is not configured")

    # Rate limit
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(_ip_buckets[client_ip], RATE_LIMIT_PER_IP_PER_MIN):
        raise HTTPException(status_code=429, detail="Too many requests")
    if not _check_rate_limit(_session_buckets[req.session_id], RATE_LIMIT_PER_SESSION_PER_MIN):
        raise HTTPException(status_code=429, detail="Slow down for a moment")

    # Build messages array for Anthropic
    messages = []
    for h in req.history[-10:]:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": req.message})

    # Build the system prompt with site context
    system_prompt = SYSTEM_PROMPT + (
        f"\n\nThe user is on the {req.property} site. "
        f"Page URL: {req.page_url or 'unknown'}."
    )

    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 600,
        "system": system_prompt,
        "messages": messages,
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            r = await client.post(ANTHROPIC_API_URL, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        body_excerpt = e.response.text[:500] if e.response is not None else ""
        status = e.response.status_code if e.response else "?"
        logger.error(f"Anthropic API error {status}: {body_excerpt}")
        if DEBUG_MODE:
            raise HTTPException(
                status_code=502,
                detail=f"Anthropic API: {status} {body_excerpt[:200]}",
            )
        raise HTTPException(status_code=502, detail="Upstream AI service error")
    except httpx.RequestError as e:
        logger.error(f"Anthropic request failed: {e}")
        raise HTTPException(status_code=502, detail="Could not reach AI service")
    except Exception:
        logger.exception("Unexpected error calling Anthropic")
        raise HTTPException(status_code=500, detail="Internal error")

    # Extract assistant reply text from Anthropic response
    text_blocks = [
        b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
    ]
    raw_reply = "\n".join(text_blocks).strip()

    # Parse JSON (system prompt instructs JSON output)
    parsed = None
    try:
        cleaned = re.sub(
            r"^```(?:json)?\s*|\s*```\s*$", "", raw_reply, flags=re.MULTILINE
        ).strip()
        parsed = json.loads(cleaned)
    except Exception:
        # Fallback if model didn't return clean JSON for any reason
        logger.warning(f"Model did not return valid JSON. Raw: {raw_reply[:200]}")
        parsed = {
            "reply": raw_reply or "Sorry, I didn't catch that. Could you rephrase?",
            "quick_actions": [],
            "intent": "general",
        }

    # Build safe response object (Pydantic enforces field validation)
    return ChatResponse(
        reply=parsed.get("reply", "Sorry, I didn't catch that."),
        quick_actions=[
            QuickAction(**qa) for qa in (parsed.get("quick_actions") or [])[:4]
        ],
        intent=parsed.get("intent", "general"),
        lead_intent=parsed.get("lead_intent"),
    )
