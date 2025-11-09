#!/usr/bin/env python3
"""
Secure FastAPI app to search voter details by voter_id from Supabase.

Security features:
- Requires X-API-Key header (server-side secret, not shared with clients).
- Uses Supabase ANON key (read-only) — do NOT use service-role here.
- Per-IP rate limiting (60 requests/min) via slowapi.
- Strict CORS from env (no wildcard by default).
- Input validation: voter_id sanitized (uppercase, strip '/', alnum only, length cap).

Run:
    uvicorn main_secure:app --host 0.0.0.0 --port 8000

Test:
    curl -H "X-API-Key: $INTERNAL_API_KEY" "http://127.0.0.1:8000/search?voter_id=WB/24/162/012136"
"""

from __future__ import annotations
import os
import re
from typing import List

from dotenv import load_dotenv
from fastapi import FastAPI, Query, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from supabase import create_client, Client

# Rate limiting
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

load_dotenv()

# ---------- Config / Env ----------
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()  # READ-ONLY
INTERNAL_API_KEY = os.getenv(
    "INTERNAL_API_KEY", ""
).strip()  # Your server API key (required)

ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()
]
TABLE_NAME = os.getenv("SUPABASE_TABLE", "2002_voter_details")

if not SUPABASE_URL or not SUPABASE_KEY or not INTERNAL_API_KEY:
    raise RuntimeError(
        "Missing required env vars. Need SUPABASE_URL, SUPABASE_ANON_KEY, INTERNAL_API_KEY."
    )

# ---------- Supabase Client ----------
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------- FastAPI app ----------
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app = FastAPI(title="Secure Voter Search API", version="1.0.0")
app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    lambda request, exc: HTTPException(status_code=429, detail="Rate limit exceeded"),
)

# Rate-limit middleware
app.add_middleware(SlowAPIMiddleware)

# CORS — default: none; set ALLOWED_ORIGINS to enable specific origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["X-API-Key", "Content-Type"],
)

# ---------- Auth dependency ----------
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)


def require_api_key(api_key: str = Depends(api_key_header)) -> None:
    # time-constant comparison not strictly necessary here but OK for parity
    if api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ---------- Helpers ----------
VOTER_ID_ALLOWED = re.compile(r"^[A-Z0-9]{3,40}$")  # tune length as needed


def normalize_voter_id(raw: str) -> str:
    """Normalize to match your DB: remove '/', uppercase, keep alphanumerics."""
    cleaned = raw.replace("/", "").upper()
    # Optional: trim spaces & other harmless chars
    cleaned = re.sub(r"[^A-Z0-9]", "", cleaned)
    return cleaned[:40]  # hard cap length


# ---------- Routes ----------
@app.get("/search")
@limiter.limit("30/minute")  # extra tight per-endpoint if desired
def search_voter(
    request: Request,
    voter_id: str = Query(..., description="Raw voter id; '/' will be removed"),
    _: None = Depends(require_api_key),
):
    # Normalize and validate
    norm_id = normalize_voter_id(voter_id)
    if not VOTER_ID_ALLOWED.match(norm_id):
        raise HTTPException(status_code=400, detail="Invalid voter_id format")

    try:
        resp = supabase.table(TABLE_NAME).select("*").eq("voter_id", norm_id).execute()
        data = resp.data or []
        if not data:
            raise HTTPException(status_code=404, detail="Voter ID not found")

        return {
            "query": {"raw": voter_id, "normalized": norm_id},
            "count": len(data),
            "results": data,
        }
    except HTTPException:
        raise
    except Exception as e:
        # Do not leak internal errors
        raise HTTPException(status_code=500, detail="Internal error") from e


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/")
def root():
    return {
        "message": "Secure Voter Search API. Use /search?voter_id=... with X-API-Key."
    }
