"""
Pricing Quote endpoints.
Admin and Employee roles only.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Quote
from app.schemas import QuoteCreate, QuoteResponse, QuoteListResponse
from app.services.auth_service import get_current_user
from app.services.quote_pdf import generate_quote_pdf, upload_quote_pdf

router = APIRouter()

ALLOWED_ROLES = ["admin", "employee"]


def require_internal(user: dict):
    if user.get("role") not in ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Pricing quotes are restricted to internal staff")


@router.post("/quotes", response_model=QuoteResponse)
async def create_quote(
    payload: QuoteCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_internal(user)

    quote = Quote(
        created_by=user["sub"],
        created_by_role=user.get("role", "employee"),
        merchant_name=payload.merchant_name or "",
        vertical=payload.vertical,
        risk_level=payload.risk_level,
        volume=payload.volume,
        transactions=payload.transactions,
        markup_pct=payload.markup_pct,
        auth_sell=payload.auth_sell,
        avs_sell=payload.avs_sell,
        batch_sell=payload.batch_sell,
        monthly_sell=payload.monthly_sell,
        transarmor_sell=payload.transarmor_sell,
        pci_sell=payload.pci_sell,
        has_amex=payload.has_amex,
        amex_volume=payload.amex_volume,
        use_gateway=payload.use_gateway,
        beacon_trad_residual=payload.results.get("beacon_trad_residual", 0),
        beacon_trad_margin=payload.results.get("beacon_trad_margin", 0),
        beacon_flex_residual=payload.results.get("beacon_flex_residual", 0),
        beacon_flex_margin=payload.results.get("beacon_flex_margin", 0),
        maverick_residual=payload.results.get("maverick_residual", 0),
        maverick_tnr=payload.results.get("maverick_tnr", 0),
        maverick_risk=payload.risk_level,
        best_program=payload.results.get("best_program", ""),
        best_residual=payload.results.get("best_residual", 0),
        notes=payload.notes or "",
        status="draft",
    )

    db.add(quote)
    await db.commit()
    await db.refresh(quote)

    pdf_url = None
    try:
        pdf_bytes = generate_quote_pdf(quote)
        pdf_url = await upload_quote_pdf(quote.id, pdf_bytes)
        if pdf_url:
            quote.pdf_url = pdf_url
            await db.commit()
    except Exception:
        pass

    return _to_response(quote)


@router.get("/quotes", response_model=QuoteListResponse)
async def list_quotes(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    vertical: str = Query(None),
    status: str = Query(None),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_internal(user)

    query = select(Quote).order_by(desc(Quote.created_at))

    if vertical:
        query = query.where(Quote.vertical == vertical)
    if status:
        query = query.where(Quote.status == status)

    if user.get("role") == "employee":
        query = query.where(Quote.created_by == user["sub"])

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    quotes = result.scalars().all()

    return QuoteListResponse(
        quotes=[_to_response(q) for q in quotes],
        total=len(quotes),
    )


@router.get("/quotes/{quote_id}", response_model=QuoteResponse)
async def get_quote(
    quote_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_internal(user)

    result = await db.execute(select(Quote).where(Quote.id == quote_id))
    quote = result.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")

    if user.get("role") == "employee" and quote.created_by != user["sub"]:
        raise HTTPException(status_code=403, detail="Access denied")

    return _to_response(quote)


@router.put("/quotes/{quote_id}/status")
async def update_quote_status(
    quote_id: int,
    new_status: str = Query(..., pattern="^(draft|sent|accepted|rejected|expired)$"),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_internal(user)

    result = await db.execute(select(Quote).where(Quote.id == quote_id))
    quote = result.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")

    quote.status = new_status
    await db.commit()

    return {"message": f"Quote {quote_id} status updated to {new_status}"}


@router.delete("/quotes/{quote_id}")
async def delete_quote(
    quote_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    result = await db.execute(select(Quote).where(Quote.id == quote_id))
    quote = result.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")

    await db.delete(quote)
    await db.commit()

    return {"message": f"Quote {quote_id} deleted"}


def _to_response(q: Quote) -> QuoteResponse:
    return QuoteResponse(
        id=q.id,
        created_by=q.created_by,
        created_at=q.created_at.isoformat() if q.created_at else "",
        merchant_name=q.merchant_name or "",
        vertical=q.vertical,
        risk_level=q.risk_level,
        volume=q.volume,
        transactions=q.transactions,
        markup_pct=q.markup_pct,
        auth_sell=q.auth_sell,
        avs_sell=q.avs_sell,
        batch_sell=q.batch_sell,
        monthly_sell=q.monthly_sell,
        transarmor_sell=q.transarmor_sell,
        pci_sell=q.pci_sell,
        has_amex=q.has_amex,
        amex_volume=q.amex_volume,
        use_gateway=q.use_gateway,
        beacon_trad_residual=q.beacon_trad_residual,
        beacon_trad_margin=q.beacon_trad_margin,
        beacon_flex_residual=q.beacon_flex_residual,
        beacon_flex_margin=q.beacon_flex_margin,
        maverick_residual=q.maverick_residual,
        maverick_tnr=q.maverick_tnr,
        maverick_risk=q.maverick_risk,
        best_program=q.best_program,
        best_residual=q.best_residual,
        notes=q.notes or "",
        status=q.status,
        pdf_url=q.pdf_url or "",
    )
