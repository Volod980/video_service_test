"""
AI Video Generation Pipeline — FastAPI Application

Endpoints:
  POST /generate                                — Start generation job
  GET  /status/{job_id}                         — Poll job progress
  GET  /download/{job_id}                       — Download final video
  GET  /health                                  — Liveness probe

  GET  /jobs/{job_id}/frames                    — List all frame metadata + URLs
  GET  /jobs/{job_id}/clips                     — List all clip metadata + URLs
  GET  /jobs/{job_id}/frames/{frame_id}/image   — Serve frame image file
  GET  /jobs/{job_id}/clips/{scene_id}/video    — Serve clip video file

  POST /jobs/{job_id}/regenerate-frame/{frame_id}   — Regen single frame
  POST /jobs/{job_id}/regenerate-clip/{scene_id}    — Regen single clip + reassemble
  POST /jobs/{job_id}/ai-edit-frame/{frame_id}      — Claude rewrites prompt + regen
  POST /jobs/{job_id}/ai-edit-clip/{scene_id}       — Claude rewrites prompt + regen
  POST /jobs/{job_id}/assemble                      — Re-run ffmpeg concat
  POST /jobs/{job_id}/add-music                     — Mix audio into final video
"""

import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import anthropic
import aiofiles
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pipeline import (
    PipelineSettings,
    ai_rewrite_clip_prompt,
    ai_rewrite_frame_prompt,
    assemble_final_video,
    generate_single_clip,
    generate_single_frame,
    run_pipeline,
)
from utils import JOBS_DIR, add_music_to_video, cleanup_job_dir, get_job_dir, upload_files_to_fal

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

JOBS: dict[str, dict] = {}
_anthropic_client: anthropic.AsyncAnthropic | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _anthropic_client
    api_key = os.getenv("ANTHROPIC_API_KEY")
    fal_key = os.getenv("FAL_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — /generate will return 503")
    if not fal_key:
        logger.warning("FAL_KEY not set — /generate will return 503")
    if api_key:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Service started. Jobs dir: %s", JOBS_DIR)
    yield
    if _anthropic_client:
        await _anthropic_client.close()
    logger.info("Service shut down.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI Video Generation Pipeline",
    description="Upload product photos + prompt → cinematic product video.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class RegenerateFrameRequest(BaseModel):
    custom_prompt: str | None = Field(None, description="Override image prompt (optional)")

class RegenerateClipRequest(BaseModel):
    custom_prompt: str | None = Field(None, description="Override motion prompt (optional)")

class AiEditFrameRequest(BaseModel):
    user_instruction: str = Field(..., description="e.g. 'change background to black marble'")

class AiEditClipRequest(BaseModel):
    user_instruction: str = Field(..., description="e.g. 'make lighting warmer'")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_AUDIO_TYPES = {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/wave"}
MAX_IMAGE_SIZE_MB = 20
MAX_IMAGES = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_keys() -> None:
    """Raise 503 if API keys are missing."""
    if not os.getenv("ANTHROPIC_API_KEY") or not os.getenv("FAL_KEY"):
        raise HTTPException(
            status_code=503,
            detail="Service not configured: ANTHROPIC_API_KEY and FAL_KEY must be set.",
        )


def _require_job(job_id: str) -> dict:
    """Return job or raise 404."""
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


def _require_completed_job(job_id: str) -> dict:
    """Return job, ensuring it completed successfully."""
    job = _require_job(job_id)
    if job["status"] == "processing":
        raise HTTPException(status_code=202, detail="Job is still processing.")
    if job["status"] == "failed":
        raise HTTPException(status_code=500, detail=f"Job failed: {job.get('error')}")
    return job


def _base_url(request_url: str, job_id: str) -> str:
    """Build the base URL prefix for job assets."""
    # Simple helper — just use /jobs/{job_id} relative path; clients resolve against API base
    return f"/jobs/{job_id}"


def _frame_url(job_id: str, frame_id: int) -> str:
    return f"/jobs/{job_id}/frames/{frame_id}/image"


def _clip_url(job_id: str, scene_id: int) -> str:
    return f"/jobs/{job_id}/clips/{scene_id}/video"


async def save_upload(upload: UploadFile, dest: Path, allowed: set[str], max_mb: int) -> Path:
    """Save uploaded file to disk after validating type and size."""
    content_type = (upload.content_type or "").split(";")[0].strip()
    if content_type not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"'{upload.filename}': unsupported type '{content_type}'. Allowed: {allowed}",
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await upload.read(1024 * 64):
            size += len(chunk)
            if size > max_mb * 1_048_576:
                raise HTTPException(status_code=413, detail=f"'{upload.filename}' exceeds {max_mb} MB.")
            await f.write(chunk)
    return dest


# ---------------------------------------------------------------------------
# Background task wrappers
# ---------------------------------------------------------------------------

async def _run_pipeline_bg(job_id: str, image_paths: list[Path], user_prompt: str, settings: PipelineSettings) -> None:
    await run_pipeline(
        job_id=job_id,
        image_paths=image_paths,
        user_prompt=user_prompt,
        settings=settings,
        job_state=JOBS[job_id],
        anthropic_client=_anthropic_client,
    )


async def _regenerate_frame_bg(job_id: str, frame_id: int, custom_prompt: str | None) -> None:
    """Background: regenerate one frame, update job_state."""
    job = JOBS[job_id]
    job_dir = get_job_dir(job_id)
    settings = PipelineSettings(**job["settings"])
    frame_info = job["frames"][frame_id]
    prompt = custom_prompt or frame_info["prompt"]

    try:
        reference_urls = await upload_files_to_fal(
            [Path(p) for p in job["reference_image_paths"]]
        )
        frame_dict = {"frame_id": frame_id, "image_prompt": prompt}
        _, new_path = await generate_single_frame(frame_dict, reference_urls, job_dir, settings)
        job["frames"][frame_id]["path"] = str(new_path)
        if custom_prompt:
            job["frames"][frame_id]["prompt"] = custom_prompt
        job[f"regen_frame_{frame_id}"] = "done"
    except Exception as exc:
        logger.exception("Regen frame %d failed for job %s", frame_id, job_id)
        job[f"regen_frame_{frame_id}"] = f"error: {exc}"


async def _regenerate_clip_bg(job_id: str, scene_id: int, custom_prompt: str | None) -> None:
    """Background: regenerate one clip, re-assemble final video."""
    job = JOBS[job_id]
    job_dir = get_job_dir(job_id)
    settings = PipelineSettings(**job["settings"])
    # Find the clip info (scene_id is 1-indexed)
    clip_info = next((c for c in job["clips"] if c["scene_id"] == scene_id), None)
    if not clip_info:
        job[f"regen_clip_{scene_id}"] = f"error: scene {scene_id} not found"
        return

    try:
        prompt = custom_prompt or clip_info["motion_prompt"]
        frame_paths = [Path(f["path"]) for f in job["frames"]]
        scene_dict = {
            "scene_id": scene_id,
            "motion_prompt": prompt,
            "start_frame_id": clip_info["start_frame_id"],
            "end_frame_id": clip_info["end_frame_id"],
        }
        _, new_path = await generate_single_clip(scene_dict, frame_paths, job_dir, settings)
        clip_info["path"] = str(new_path)
        if custom_prompt:
            clip_info["motion_prompt"] = custom_prompt

        # Re-assemble
        clip_paths = [Path(c["path"]) for c in sorted(job["clips"], key=lambda x: x["scene_id"])]
        final_video = await assemble_final_video(clip_paths, job_dir)
        job["final_video_path"] = str(final_video)
        job[f"regen_clip_{scene_id}"] = "done"
    except Exception as exc:
        logger.exception("Regen clip %d failed for job %s", scene_id, job_id)
        job[f"regen_clip_{scene_id}"] = f"error: {exc}"


async def _ai_edit_frame_bg(job_id: str, frame_id: int, user_instruction: str) -> None:
    job = JOBS[job_id]
    try:
        original_prompt = job["frames"][frame_id]["prompt"]
        new_prompt = await ai_rewrite_frame_prompt(original_prompt, user_instruction, _anthropic_client)
        logger.info("AI rewritten frame %d prompt: %.80s...", frame_id, new_prompt)
        await _regenerate_frame_bg(job_id, frame_id, new_prompt)
        job[f"ai_edit_frame_{frame_id}"] = "done"
    except Exception as exc:
        logger.exception("AI edit frame %d failed for job %s", frame_id, job_id)
        job[f"ai_edit_frame_{frame_id}"] = f"error: {exc}"


async def _ai_edit_clip_bg(job_id: str, scene_id: int, user_instruction: str) -> None:
    job = JOBS[job_id]
    try:
        clip_info = next((c for c in job["clips"] if c["scene_id"] == scene_id), None)
        if not clip_info:
            job[f"ai_edit_clip_{scene_id}"] = f"error: scene {scene_id} not found"
            return
        new_prompt = await ai_rewrite_clip_prompt(clip_info["motion_prompt"], user_instruction, _anthropic_client)
        logger.info("AI rewritten clip %d prompt: %.80s...", scene_id, new_prompt)
        await _regenerate_clip_bg(job_id, scene_id, new_prompt)
        job[f"ai_edit_clip_{scene_id}"] = "done"
    except Exception as exc:
        logger.exception("AI edit clip %d failed for job %s", scene_id, job_id)
        job[f"ai_edit_clip_{scene_id}"] = f"error: {exc}"


# ---------------------------------------------------------------------------
# Core endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    configured = bool(os.getenv("ANTHROPIC_API_KEY")) and bool(os.getenv("FAL_KEY"))
    return {"status": "ok", "configured": configured, "active_jobs": len(JOBS)}


@app.post("/generate", status_code=202)
async def generate(
    background_tasks: BackgroundTasks,
    images: list[UploadFile] = File(..., description="1–10 product photos"),
    prompt: str = Form(...),
    num_scenes: int = Form(3, ge=2, le=5),
    image_quality: str = Form("pro"),
    video_quality: str = Form("pro"),
    clip_duration: int = Form(5),
):
    """
    Start a video generation job.

    - **images**: 1–10 product photos (JPEG/PNG/WebP)
    - **prompt**: mood/style description
    - **num_scenes**: 2–5 (default 3)
    - **image_quality**: standard | pro | ultra
    - **video_quality**: standard | pro
    - **clip_duration**: 5 | 10 (seconds per clip)
    """
    _require_keys()

    if not images:
        raise HTTPException(status_code=400, detail="At least one image is required.")
    if len(images) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_IMAGES} images allowed.")
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")
    if image_quality not in ("standard", "pro", "ultra"):
        raise HTTPException(status_code=400, detail="image_quality must be standard|pro|ultra")
    if video_quality not in ("standard", "pro"):
        raise HTTPException(status_code=400, detail="video_quality must be standard|pro")
    if clip_duration not in (5, 10):
        raise HTTPException(status_code=400, detail="clip_duration must be 5 or 10")

    settings = PipelineSettings(
        num_scenes=num_scenes,
        image_quality=image_quality,
        video_quality=video_quality,
        clip_duration=clip_duration,
    )

    job_id = str(uuid.uuid4())
    job_dir = get_job_dir(job_id)
    uploads_dir = job_dir / "uploads"

    image_paths: list[Path] = []
    for i, upload in enumerate(images):
        suffix = Path(upload.filename or f"image_{i}.jpg").suffix or ".jpg"
        dest = uploads_dir / f"product_{i}{suffix}"
        saved = await save_upload(upload, dest, ALLOWED_IMAGE_TYPES, MAX_IMAGE_SIZE_MB)
        image_paths.append(saved)

    JOBS[job_id] = {
        "status": "processing",
        "step": 0,
        "prompt": prompt,
        "image_count": len(image_paths),
        "settings": settings.to_dict(),
        "master_plan": None,
        "frames": [],
        "clips": [],
        "reference_image_paths": [str(p) for p in image_paths],
        "final_video_path": None,
        "final_video_with_music_path": None,
        "error": None,
    }

    background_tasks.add_task(_run_pipeline_bg, job_id, image_paths, prompt, settings)
    logger.info("Job %s started (%d images, %d scenes, quality=%s/%s)",
                job_id, len(image_paths), num_scenes, image_quality, video_quality)

    return {
        "job_id": job_id,
        "message": "Video generation started",
        "status_url": f"/status/{job_id}",
        "download_url": f"/download/{job_id}",
    }


@app.get("/status/{job_id}")
async def status(job_id: str):
    """Poll generation progress."""
    job = _require_job(job_id)
    step_labels = {
        0: "queued", 1: "generating master plan",
        2: "generating keyframes", 3: "generating video clips",
        4: "assembling final video",
    }
    return {
        "job_id": job_id,
        "status": job["status"],
        "step": job["step"],
        "step_label": step_labels.get(job["step"], "unknown"),
        "settings": job.get("settings"),
        "result_url": f"/download/{job_id}" if job["status"] == "completed" else None,
        "frames_url": f"/jobs/{job_id}/frames" if job["frames"] else None,
        "clips_url": f"/jobs/{job_id}/clips" if job["clips"] else None,
        "error": job.get("error"),
    }


@app.get("/download/{job_id}")
async def download(job_id: str):
    """Download the completed final_video.mp4."""
    job = _require_completed_job(job_id)
    path = job.get("final_video_path")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Final video file not found.")
    return FileResponse(
        path=path,
        media_type="video/mp4",
        filename=f"product_video_{job_id[:8]}.mp4",
        headers={"X-Job-Id": job_id},
    )


# ---------------------------------------------------------------------------
# Asset listing endpoints
# ---------------------------------------------------------------------------

@app.get("/jobs/{job_id}/frames")
async def list_frames(job_id: str):
    """Return metadata + image URLs for all frames in this job."""
    job = _require_job(job_id)
    return {
        "job_id": job_id,
        "frames": [
            {
                "frame_id": f["frame_id"],
                "prompt": f["prompt"],
                "image_url": _frame_url(job_id, f["frame_id"]) if f.get("path") and Path(f["path"]).exists() else None,
                "ready": bool(f.get("path") and Path(f["path"]).exists()),
            }
            for f in job.get("frames", [])
        ],
    }


@app.get("/jobs/{job_id}/clips")
async def list_clips(job_id: str):
    """Return metadata + video URLs for all clips in this job."""
    job = _require_job(job_id)
    return {
        "job_id": job_id,
        "clips": [
            {
                "scene_id": c["scene_id"],
                "motion_prompt": c["motion_prompt"],
                "start_frame_id": c["start_frame_id"],
                "end_frame_id": c["end_frame_id"],
                "video_url": _clip_url(job_id, c["scene_id"]) if c.get("path") and Path(c["path"]).exists() else None,
                "ready": bool(c.get("path") and Path(c["path"]).exists()),
            }
            for c in job.get("clips", [])
        ],
    }


# ---------------------------------------------------------------------------
# Asset file serving
# ---------------------------------------------------------------------------

@app.get("/jobs/{job_id}/frames/{frame_id}/image")
async def serve_frame(job_id: str, frame_id: int):
    """Serve a frame image file."""
    job = _require_job(job_id)
    frames = job.get("frames", [])
    frame = next((f for f in frames if f["frame_id"] == frame_id), None)
    if not frame or not frame.get("path"):
        raise HTTPException(status_code=404, detail=f"Frame {frame_id} not found or not yet generated.")
    path = Path(frame["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Frame file missing from disk.")
    return FileResponse(path=str(path), media_type="image/jpeg", filename=f"frame_{frame_id}.jpg")


@app.get("/jobs/{job_id}/clips/{scene_id}/video")
async def serve_clip(job_id: str, scene_id: int):
    """Serve a clip video file."""
    job = _require_job(job_id)
    clip = next((c for c in job.get("clips", []) if c["scene_id"] == scene_id), None)
    if not clip or not clip.get("path"):
        raise HTTPException(status_code=404, detail=f"Clip {scene_id} not found or not yet generated.")
    path = Path(clip["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Clip file missing from disk.")
    return FileResponse(path=str(path), media_type="video/mp4", filename=f"clip_{scene_id}.mp4")


# ---------------------------------------------------------------------------
# Granular regeneration endpoints
# ---------------------------------------------------------------------------

@app.post("/jobs/{job_id}/regenerate-frame/{frame_id}", status_code=202)
async def regenerate_frame(
    job_id: str,
    frame_id: int,
    background_tasks: BackgroundTasks,
    body: RegenerateFrameRequest = Body(default=RegenerateFrameRequest()),
):
    """Regenerate a single keyframe. Optionally override the image prompt."""
    _require_keys()
    job = _require_completed_job(job_id)
    if frame_id >= len(job.get("frames", [])):
        raise HTTPException(status_code=404, detail=f"Frame {frame_id} does not exist for this job.")
    job[f"regen_frame_{frame_id}"] = "pending"
    background_tasks.add_task(_regenerate_frame_bg, job_id, frame_id, body.custom_prompt)
    return {
        "message": f"Frame {frame_id} regeneration started",
        "image_url": _frame_url(job_id, frame_id),
        "status_key": f"regen_frame_{frame_id}",
    }


@app.post("/jobs/{job_id}/regenerate-clip/{scene_id}", status_code=202)
async def regenerate_clip(
    job_id: str,
    scene_id: int,
    background_tasks: BackgroundTasks,
    body: RegenerateClipRequest = Body(default=RegenerateClipRequest()),
):
    """Regenerate a single clip and re-assemble the final video."""
    _require_keys()
    job = _require_completed_job(job_id)
    if not any(c["scene_id"] == scene_id for c in job.get("clips", [])):
        raise HTTPException(status_code=404, detail=f"Scene {scene_id} does not exist.")
    job[f"regen_clip_{scene_id}"] = "pending"
    background_tasks.add_task(_regenerate_clip_bg, job_id, scene_id, body.custom_prompt)
    return {
        "message": f"Clip {scene_id} regeneration started (will auto-reassemble)",
        "video_url": _clip_url(job_id, scene_id),
        "status_key": f"regen_clip_{scene_id}",
    }


# ---------------------------------------------------------------------------
# AI-powered editing endpoints
# ---------------------------------------------------------------------------

@app.post("/jobs/{job_id}/ai-edit-frame/{frame_id}", status_code=202)
async def ai_edit_frame(
    job_id: str,
    frame_id: int,
    background_tasks: BackgroundTasks,
    body: AiEditFrameRequest,
):
    """
    Claude rewrites the frame's image_prompt based on user instruction,
    then regenerates the frame. product_bible is preserved untouched.
    """
    _require_keys()
    job = _require_completed_job(job_id)
    if frame_id >= len(job.get("frames", [])):
        raise HTTPException(status_code=404, detail=f"Frame {frame_id} does not exist.")
    job[f"ai_edit_frame_{frame_id}"] = "pending"
    background_tasks.add_task(_ai_edit_frame_bg, job_id, frame_id, body.user_instruction)
    return {
        "message": f"AI edit for frame {frame_id} started",
        "instruction": body.user_instruction,
        "image_url": _frame_url(job_id, frame_id),
        "status_key": f"ai_edit_frame_{frame_id}",
    }


@app.post("/jobs/{job_id}/ai-edit-clip/{scene_id}", status_code=202)
async def ai_edit_clip(
    job_id: str,
    scene_id: int,
    background_tasks: BackgroundTasks,
    body: AiEditClipRequest,
):
    """
    Claude rewrites the clip's motion_prompt based on user instruction,
    then regenerates the clip and re-assembles the final video.
    """
    _require_keys()
    job = _require_completed_job(job_id)
    if not any(c["scene_id"] == scene_id for c in job.get("clips", [])):
        raise HTTPException(status_code=404, detail=f"Scene {scene_id} does not exist.")
    job[f"ai_edit_clip_{scene_id}"] = "pending"
    background_tasks.add_task(_ai_edit_clip_bg, job_id, scene_id, body.user_instruction)
    return {
        "message": f"AI edit for clip {scene_id} started (will auto-reassemble)",
        "instruction": body.user_instruction,
        "video_url": _clip_url(job_id, scene_id),
        "status_key": f"ai_edit_clip_{scene_id}",
    }


# ---------------------------------------------------------------------------
# Assembly and music
# ---------------------------------------------------------------------------

@app.post("/jobs/{job_id}/assemble", status_code=202)
async def assemble(job_id: str, background_tasks: BackgroundTasks):
    """Re-run ffmpeg concat on current clips → new final_video.mp4."""
    job = _require_completed_job(job_id)
    clip_paths = [Path(c["path"]) for c in sorted(job.get("clips", []), key=lambda x: x["scene_id"])]
    missing = [str(p) for p in clip_paths if not p.exists()]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing clip files: {missing}")

    async def _assemble_bg():
        try:
            job_dir = get_job_dir(job_id)
            final = await assemble_final_video(clip_paths, job_dir)
            job["final_video_path"] = str(final)
        except Exception as exc:
            logger.exception("Assemble failed for job %s", job_id)

    background_tasks.add_task(_assemble_bg)
    return {"message": "Assembly started", "download_url": f"/download/{job_id}"}


@app.post("/jobs/{job_id}/add-music", status_code=202)
async def add_music(
    job_id: str,
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(..., description="MP3 or WAV audio file"),
):
    """
    Mix an audio track into the final video.
    Audio is looped/trimmed to exact video length.
    Returns a new download URL for final_video_with_music.mp4.
    """
    job = _require_completed_job(job_id)
    final_video = job.get("final_video_path")
    if not final_video or not Path(final_video).exists():
        raise HTTPException(status_code=404, detail="Final video not found. Run /assemble first.")

    job_dir = get_job_dir(job_id)
    audio_path = job_dir / f"music{Path(audio.filename or 'audio.mp3').suffix}"
    await save_upload(audio, audio_path, ALLOWED_AUDIO_TYPES, max_mb=50)

    async def _music_bg():
        try:
            output = job_dir / "final_video_with_music.mp4"
            await add_music_to_video(Path(final_video), audio_path, output)
            job["final_video_with_music_path"] = str(output)
        except Exception as exc:
            logger.exception("Music mix failed for job %s", job_id)
            job["music_error"] = str(exc)

    background_tasks.add_task(_music_bg)
    return {
        "message": "Music mixing started",
        "download_url": f"/jobs/{job_id}/download-with-music",
    }


@app.get("/jobs/{job_id}/download-with-music")
async def download_with_music(job_id: str):
    """Download the video with music mixed in."""
    job = _require_job(job_id)
    path = job.get("final_video_with_music_path")
    if not path:
        if job.get("music_error"):
            raise HTTPException(status_code=500, detail=f"Music mix failed: {job['music_error']}")
        raise HTTPException(status_code=202, detail="Music is still being mixed. Try again shortly.")
    if not Path(path).exists():
        raise HTTPException(status_code=404, detail="Music video file not found on disk.")
    return FileResponse(
        path=path,
        media_type="video/mp4",
        filename=f"product_video_music_{job_id[:8]}.mp4",
    )


# ---------------------------------------------------------------------------
# Entry point
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
