"""
CreditJump — backend API (v0, deploy-ready)

Receives an uploaded transcript + a destination school, runs it through the
parser and matching engine, returns the evaluation as JSON.

Endpoints
    GET  /health      liveness check (used by host uptime monitors)
    GET  /schools     destinations we have an articulation table for
    POST /evaluate    transcript file + destination -> evaluation

Run locally:
    pip install -r requirements.txt
    uvicorn api:app --reload --port 8000      # docs at /docs

Config (environment variables — see .env.example):
    ALLOWED_ORIGINS      comma-separated front-end origins ("*" for local only)
    RATE_LIMIT_PER_MIN   max /evaluate calls per IP per minute (default 20)
    PORT                 set automatically by most hosts

Privacy: the transcript is written to a temp file, parsed, and deleted in the
same request. It is never stored and never written to logs.
"""

import os
import glob
import json
import time
import tempfile
from collections import defaultdict, deque
from threading import Lock

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from transcript_parser import parse_transcript
from matching_engine import match

app = FastAPI(title="CreditJump API", version="0.1.0")

# --- CORS: lock to your front-end origin(s) in production -------------------
_origins_env = os.getenv("ALLOWED_ORIGINS", "*").strip()
ALLOWED_ORIGINS = ["*"] if _origins_env == "*" else [o.strip() for o in _origins_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# --- simple per-IP rate limit (abuse / cost protection) ---------------------
# In-memory: fine for a single instance. For multiple instances, back this
# with Redis instead.
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MIN", "20"))
_hits = defaultdict(deque)
_lock = Lock()


@app.middleware("http")
async def rate_limit(request, call_next):
    if request.url.path == "/evaluate":
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        with _lock:
            dq = _hits[ip]
            while dq and now - dq[0] > 60:
                dq.popleft()
            if len(dq) >= RATE_LIMIT:
                return JSONResponse(status_code=429,
                                    content={"detail": "Too many requests — try again in a minute."})
            dq.append(now)
    return await call_next(request)


ARTICULATIONS_DIR = os.path.join(os.path.dirname(__file__), "articulations")
ALLOWED_EXT = {".pdf"}
MAX_BYTES = 10 * 1024 * 1024


def load_registry() -> dict:
    registry = {}
    for path in glob.glob(os.path.join(ARTICULATIONS_DIR, "*.json")):
        with open(path) as f:
            dest = json.load(f).get("destination")
        if dest:
            registry[dest] = path
    return registry


REGISTRY = load_registry()


@app.get("/health")
def health():
    return {"status": "ok", "destinations_loaded": len(REGISTRY)}


@app.get("/schools")
def schools():
    return {"destinations": sorted(REGISTRY.keys())}


@app.post("/evaluate")
async def evaluate(transcript: UploadFile = File(...), destination: str = Form(...)):
    if destination not in REGISTRY:
        raise HTTPException(status_code=404,
                            detail=f"No transfer table for '{destination}' yet. "
                                   f"Available: {sorted(REGISTRY.keys())}")

    ext = os.path.splitext(transcript.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=422,
                            detail="Please upload a PDF transcript. Scanned images "
                                   "aren't supported yet (OCR is on the roadmap).")

    data = await transcript.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (10 MB max).")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        parsed = parse_transcript(tmp_path)
        if parsed["course_count"] == 0:
            raise HTTPException(status_code=422,
                                detail="Couldn't read any courses — this usually means the PDF "
                                       "is a scan with no text layer. Try the original export "
                                       "from your student portal.")
        result = match(parsed, REGISTRY[destination])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Processing failed: {exc}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {
        "from_school": parsed["issuing_institution"],
        "destination": result["destination"],
        "cumulative_gpa": parsed["cumulative_gpa"],
        "summary": result["summary"],
        "courses": result["courses"],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
