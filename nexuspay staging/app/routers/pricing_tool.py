"""
Pricing Tool API — Statement extraction + Proposal generation
Routes through existing AI providers (Claude, GPT-4o, Gemini, Grok)
Employee/Admin only
"""
import json, os
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/pricing-tool", tags=["pricing-tool"])

class ExtractRequest(BaseModel):
    file_base64: str
    media_type: str

class ProposalRequest(BaseModel):
    business_name: str = "Prospective Merchant"
    current_processor: str = "Unknown"
    current_rate: Optional[float] = None
    current_fees: Optional[float] = None
    monthly_volume: float = 0
    transactions: int = 0
    credit_card_pct: float = 75
    program_label: str = ""
    program_short: str = ""
    model_label: str = ""
    new_rate: float = 0
    np_residual_mo: float = 0
    market_benchmark: float = 0
    annual_savings: Optional[float] = None
    findings: List[str] = []
    rep_name: str = ""
    model_config = {"protected_namespaces": ()}
EXTRACT_PROMPT = """You are a merchant processing statement analyst for NexusPay. Extract fields from this statement. Return ONLY valid JSON, no markdown, no backticks:
{"business_name":null,"contact_email":null,"contact_phone":null,"monthly_volume":null,"transaction_count":null,"credit_card_pct":null,"avg_ticket":null,"effective_rate":null,"current_processor":null,"total_fees":null,"interchange_cost":null,"industry":null,"mcc_code":null,"findings":[]}
If a field cannot be determined, use null. For effective_rate, calculate as (total_fees/monthly_volume*100) if both available. Flag hidden fees, overcharges, PCI issues."""

async def _call_extract(file_base64, media_type):
    import httpx
    providers = []
    k = os.getenv("ANTHROPIC_API_KEY")
    if k: providers.append(("claude", k))
    k = os.getenv("OPENAI_API_KEY")
    if k: providers.append(("openai", k))
    k = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if k: providers.append(("gemini", k))
    k = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")
    if k: providers.append(("grok", k))
    if not providers:
        raise HTTPException(500, "No AI API keys configured")
    last = ""
    async with httpx.AsyncClient(timeout=120.0) as c:
        for name, key in providers:
            try:
                if name == "claude":
                    ct = []
                    if media_type == "application/pdf":
                        ct.append({"type":"document","source":{"type":"base64","media_type":"application/pdf","data":file_base64}})
                    else:
                        ct.append({"type":"image","source":{"type":"base64","media_type":media_type,"data":file_base64}})
                    ct.append({"type":"text","text":EXTRACT_PROMPT})
                    r = await c.post("https://api.anthropic.com/v1/messages",headers={"x-api-key":key,"anthropic-version":"2023-06-01","Content-Type":"application/json"},json={"model":"claude-sonnet-4-20250514","max_tokens":1500,"messages":[{"role":"user","content":ct}]})
                    d = r.json(); txt = "".join(b.get("text","") for b in d.get("content",[]) if b.get("type")=="text")
                elif name == "openai":
                    ct = []
                    if media_type.startswith("image"):
                        ct.append({"type":"image_url","image_url":{"url":f"data:{media_type};base64,{file_base64}"}})
                    ct.append({"type":"text","text":EXTRACT_PROMPT})
                    r = await c.post("https://api.openai.com/v1/chat/completions",headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},json={"model":"gpt-4o","max_tokens":1500,"messages":[{"role":"user","content":ct}]})
                    d = r.json(); txt = d.get("choices",[{}])[0].get("message",{}).get("content","")
                elif name == "gemini":
                    pts = [{"text":EXTRACT_PROMPT}]
                    if media_type.startswith("image"):
                        pts.append({"inline_data":{"mime_type":media_type,"data":file_base64}})
                    r = await c.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}",json={"contents":[{"parts":pts}]})
                    d = r.json(); txt = d.get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text","")
                elif name == "grok":
                    ct = [{"type":"text","text":EXTRACT_PROMPT}]
                    if media_type.startswith("image"):
                        ct.append({"type":"image_url","image_url":{"url":f"data:{media_type};base64,{file_base64}"}})
                    r = await c.post("https://api.x.ai/v1/chat/completions",headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},json={"model":"grok-2-vision-1212","max_tokens":1500,"messages":[{"role":"user","content":ct}]})
                    d = r.json(); txt = d.get("choices",[{}])[0].get("message",{}).get("content","")
                clean = txt.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                return json.loads(clean)
            except Exception as ex:
                last = f"{name}: {ex}"; continue
    raise HTTPException(500, f"All AI providers failed: {last}")

async def _call_proposal(prompt):
    import httpx
    providers = []
    k = os.getenv("ANTHROPIC_API_KEY")
    if k: providers.append(("claude", k))
    k = os.getenv("OPENAI_API_KEY")
    if k: providers.append(("openai", k))
    k = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if k: providers.append(("gemini", k))
    k = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")
    if k: providers.append(("grok", k))
    if not providers:
        raise HTTPException(500, "No AI keys configured")
    last = ""
    async with httpx.AsyncClient(timeout=60.0) as c:
        for name, key in providers:
            try:
                if name == "claude":
                    r = await c.post("https://api.anthropic.com/v1/messages",headers={"x-api-key":key,"anthropic-version":"2023-06-01","Content-Type":"application/json"},json={"model":"claude-sonnet-4-20250514","max_tokens":1000,"messages":[{"role":"user","content":prompt}]})
                    d = r.json(); return "".join(b.get("text","") for b in d.get("content",[]) if b.get("type")=="text")
                elif name == "openai":
                    r = await c.post("https://api.openai.com/v1/chat/completions",headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},json={"model":"gpt-4o","max_tokens":1000,"messages":[{"role":"user","content":prompt}]})
                    return r.json()["choices"][0]["message"]["content"]
                elif name == "gemini":
                    r = await c.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}",json={"contents":[{"parts":[{"text":prompt}]}]})
                    return r.json()["candidates"][0]["content"]["parts"][0]["text"]
                elif name == "grok":
                    r = await c.post("https://api.x.ai/v1/chat/completions",headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},json={"model":"grok-2-1212","max_tokens":1000,"messages":[{"role":"user","content":prompt}]})
                    return r.json()["choices"][0]["message"]["content"]
            except Exception as ex:
                last = f"{name}: {ex}"; continue
    raise HTTPException(500, f"All providers failed: {last}")

@router.post("/extract")
async def extract_statement(req: ExtractRequest, user=Depends(get_current_user)):
    if user.role not in ("admin", "employee"):
        raise HTTPException(403, "Employee or admin access required")
    return await _call_extract(req.file_base64, req.media_type)

@router.post("/proposal")
async def generate_proposal(req: ProposalRequest, user=Depends(get_current_user)):
    if user.role not in ("admin", "employee"):
        raise HTTPException(403, "Employee or admin access required")
    prompt = f"""Write a merchant pricing proposal for NexusPay (veteran-owned). Clean text only, no markdown.
Merchant: {req.business_name}
Current Processor: {req.current_processor}
Current Rate: {str(req.current_rate)+'%' if req.current_rate else 'N/A'}
Current Fees: {'$'+f'{req.current_fees:,.2f}' if req.current_fees else 'N/A'}
Volume: ${req.monthly_volume:,.0f}/mo | {req.transactions:,} txns | {req.credit_card_pct}% CC
Recommended: {req.program_label} ({req.program_short}) x {req.model_label}
New Rate: {req.new_rate:.2f}% | NP Residual: ${req.np_residual_mo:,.2f}/mo | Market: {req.market_benchmark:.2f}%
{'Savings: $'+f'{req.annual_savings:,.2f}/yr' if req.annual_savings else ''}
{'Issues: '+'; '.join(req.findings) if req.findings else ''}
3 paragraphs: (1) current situation, (2) solution and why, (3) savings + next steps.
End with: Ready to get started? Call (720) 689-7272 or visit nexuspayservices.com"""
    txt = await _call_proposal(prompt)
    return {"proposal_text": txt, "generated_at": datetime.utcnow().isoformat()}
