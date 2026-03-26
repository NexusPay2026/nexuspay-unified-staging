"""
NexusPay Intelligence Platform — Unified Backend API
=====================================================
Consolidates: Portal API + Visitor Webhook + AI Orchestration + R2 Storage
Surfaces served:
  1. nexuspayservices.com         — Main website
  2. freeanalysis.nexuspayservices.com — Landing page (lead capture)
  3. nexuspayai.com               — Portal / Bloomberg terminal UI
  4. nexuspaydashboard.netlify.app — Visitor tracking dashboard
"""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine, Base, database
from app.routers import auth, merchants, users, visitors, audit, health, storage

# ── Lifespan ────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables on startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await database.connect()
    yield
    await database.disconnect()

# ── App ─────────────────────────────────────────────────────
app = FastAPI(
    title="NexusPay Intelligence API",
    version="4.0.0",
    description="Unified backend for website, landing page, portal, and visitor dashboard",
    lifespan=lifespan,
)

# ── CORS — allow all four frontend surfaces ──────────────────
ALLOWED_ORIGINS = [
    # 1. Main website
    "https://nexuspayservices.com",
    "https://www.nexuspayservices.com",
    # 2. Landing page
    "https://freeanalysis.nexuspayservices.com",
    "https://nexuspaylandingpage.netlify.app",
    # 3. Portal / Bloomberg terminal UI
    "https://nexuspayai.com",
    "https://www.nexuspayai.com",
    # 4. Visitor tracking dashboard
    "https://nexuspaydashboard.netlify.app",
    # Dev
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8080",
]

# If in dev, allow all
if settings.APP_ENV == "development":
    ALLOWED_ORIGINS = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ─────────────────────────────────────────────────
app.include_router(health.router,    tags=["Health"])
app.include_router(auth.router,      prefix="/api",  tags=["Auth"])
app.include_router(merchants.router, prefix="/api",  tags=["Merchants"])
app.include_router(users.router,     prefix="/api",  tags=["Users"])
app.include_router(visitors.router,  tags=["Visitors / Leads"])
app.include_router(audit.router,     prefix="/api",  tags=["AI Audit"])
app.include_router(storage.router,   prefix="/api",  tags=["Storage / R2"])
