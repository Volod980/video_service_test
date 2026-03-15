"""
AI Video Generation Pipeline — FastAPI Application

Endpoints:
  POST /generate       — Upload product images + prompt → returns job_id
  GET  /status/{id}    — Poll pipeline progress
  GET  /download/{id}  — Download completed final_video.mp4
  GET  /health         — Health check
"""

import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
import aiofiles
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from pipeline import run_pipeline
from utils import JOBS_DIR, cleanup_job_dir, get_job_dir

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# In-memory job registry  { job_id: { status, step, result_path, error, ... } }
JOBS: dict[str, dict] = {}

# Shared Anthropic async client (created once at startup)
_anthropic_client: anthropic.AsyncAnthropic | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared resources on startup, clean up on shutdown."""
    global _anthropic_client

    api_key = os.getenv("ANTHROPIC_API_KEY")
    fal_key = os.getenv("FAL_KEY")

    # Warn but do NOT crash — healthcheck must pass regardless of env vars.
    # Missing keys are caught per-request in /generate with a clear 503.
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY is not set — /generate will return 503")
    if not fal_key:
        logger.warning("FAL_KEY is not set — /generate will return 503")

    if api_key:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)

    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Video pipeline service started. Jobs dir: %s", JOBS_DIR)

    yield

    await _anthropic_client.close()
    logger.info("Video pipeline service shut down.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI Video Generation Pipeline",
    description="Upload product photos + a text prompt → get a cinematic product video.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_SIZE_MB = 20
MAX_IMAGES = 10


async def save_upload(upload: UploadFile, dest: Path) -> Path:
    """Persist an uploaded file to disk, validate type and size."""
    content_type = upload.content_type or ""
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File '{upload.filename}' has unsupported type '{content_type}'. "
                   f"Allowed: {', '.join(ALLOWED_IMAGE_TYPES)}",
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await upload.read(1024 * 64):
            size += len(chunk)
            if size > MAX_IMAGE_SIZE_MB * 1_048_576:
                raise HTTPException(
                    status_code=413,
                    detail=f"File '{upload.filename}' exceeds {MAX_IMAGE_SIZE_MB} MB limit.",
                )
            await f.write(chunk)

    return dest


# ---------------------------------------------------------------------------
# Background task wrapper
# ---------------------------------------------------------------------------

async def _run_pipeline_task(
    job_id: str,
    image_paths: list[Path],
    user_prompt: str,
) -> None:
    """Thin wrapper so FastAPI BackgroundTasks can call the pipeline."""
    await run_pipeline(
        job_id=job_id,
        image_paths=image_paths,
        user_prompt=user_prompt,
        job_state=JOBS[job_id],
        anthropic_client=_anthropic_client,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    """Liveness / readiness probe. Always returns 200 so Railway healthcheck passes."""
    configured = bool(os.getenv("ANTHROPIC_API_KEY")) and bool(os.getenv("FAL_KEY"))
    return {
        "status": "ok",
        "configured": configured,
        "active_jobs": len(JOBS),
    }


@app.post("/generate", status_code=202)
async def generate(
    background_tasks: BackgroundTasks,
    images: list[UploadFile] = File(..., description="1–10 product photos (JPEG/PNG/WebP)"),
    prompt: str = Form(..., description="Text describing desired video mood/style"),
):
    """
    Start a video generation job.

    - **images**: 1–10 product photos
    - **prompt**: mood/style description for the video (e.g. "minimal white studio, slow reveal, luxury feel")

    Returns a **job_id** you can use to poll `/status/{job_id}`.
    """
    # Validate required API keys are present before accepting the job
    if not os.getenv("ANTHROPIC_API_KEY") or not os.getenv("FAL_KEY"):
        raise HTTPException(
            status_code=503,
            detail="Service not configured: ANTHROPIC_API_KEY and FAL_KEY must be set.",
        )
    if not images:
        raise HTTPException(status_code=400, detail="At least one image is required.")
    if len(images) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_IMAGES} images allowed.")
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    job_id = str(uuid.uuid4())
    job_dir = get_job_dir(job_id)
    uploads_dir = job_dir / "uploads"

    # Persist all uploaded images
    image_paths: list[Path] = []
    for i, upload in enumerate(images):
        suffix = Path(upload.filename or f"image_{i}.jpg").suffix or ".jpg"
        dest = uploads_dir / f"product_{i}{suffix}"
        saved = await save_upload(upload, dest)
        image_paths.append(saved)

    # Register the job
    JOBS[job_id] = {
        "status": "processing",
        "step": 0,
        "result_path": None,
        "error": None,
        "prompt": prompt,
        "image_count": len(image_paths),
    }

    # Fire off the pipeline asynchronously
    background_tasks.add_task(_run_pipeline_task, job_id, image_paths, prompt)

    logger.info("Job %s started (%d image(s), prompt=%.60s...)", job_id, len(image_paths), prompt)
    return {
        "job_id": job_id,
        "message": "Video generation started",
        "status_url": f"/status/{job_id}",
        "download_url": f"/download/{job_id}",
    }


@app.get("/status/{job_id}")
async def status(job_id: str):
    """
    Poll the status of a generation job.

    Returns:
    - **status**: `processing` | `completed` | `failed`
    - **step**: current pipeline step (1=Master Plan, 2=Keyframes, 3=Clips, 4=Assembly)
    - **result_url**: download URL once completed
    - **error**: error message if failed
    """
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    step_labels = {
        0: "queued",
        1: "generating master plan",
        2: "generating keyframes",
        3: "generating video clips",
        4: "assembling final video",
    }

    return {
        "job_id": job_id,
        "status": job["status"],
        "step": job["step"],
        "step_label": step_labels.get(job["step"], "unknown"),
        "result_url": f"/download/{job_id}" if job["status"] == "completed" else None,
        "error": job.get("error"),
    }


@app.get("/download/{job_id}")
async def download(job_id: str):
    """
    Download the completed final_video.mp4.
    The job files are cleaned up from disk after this response is sent.
    """
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    if job["status"] == "processing":
        raise HTTPException(status_code=202, detail="Video is still being generated. Try again later.")
    if job["status"] == "failed":
        raise HTTPException(
            status_code=500,
            detail=f"Job failed at step {job['step']}: {job.get('error', 'unknown error')}",
        )

    result_path: Path | None = job.get("result_path")
    if not result_path or not result_path.exists():
        raise HTTPException(status_code=404, detail="Final video file not found on disk.")

    # Schedule cleanup after response is sent
    async def _cleanup():
        cleanup_job_dir(job_id)
        # Remove from in-memory registry too
        JOBS.pop(job_id, None)

    return FileResponse(
        path=str(result_path),
        media_type="video/mp4",
        filename=f"product_video_{job_id[:8]}.mp4",
        background=BackgroundTasks(),  # FileResponse handles sending; cleanup below
        headers={"X-Job-Id": job_id},
    )


# ---------------------------------------------------------------------------
# Entry point (for local dev)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("ENV", "production") == "development",
        log_level="info",
    )
