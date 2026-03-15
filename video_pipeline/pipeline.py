"""
Core pipeline logic for AI Video Generation.

Step 1 — Master Plan Generation (Claude / Anthropic API)
Step 2 — Keyframe Generation (fal.ai — Flux Dev / Nano Banana 2)
Step 3 — Video Clip Generation (fal.ai — Kling v1.6 Pro)
Step 4 — Final Assembly (ffmpeg concat)
"""

import asyncio
import base64
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

import anthropic
import fal_client

from prompts import MASTER_PLAN_SYSTEM_PROMPT, build_master_plan_user_message
from utils import (
    cleanup_job_dir,
    concatenate_clips,
    download_file,
    get_job_dir,
    upload_file_to_fal,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
MasterPlan = dict[str, Any]
JobState = dict[str, Any]


# ---------------------------------------------------------------------------
# Step 1 — Master Plan via Claude
# ---------------------------------------------------------------------------

async def generate_master_plan(
    image_paths: list[Path],
    user_prompt: str,
    anthropic_client: anthropic.AsyncAnthropic,
) -> MasterPlan:
    """
    Call Claude with all product images (base64 encoded) + user prompt.
    Returns parsed Master Plan JSON containing product_bible, scenes, frames.
    """
    logger.info("Step 1: Generating Master Plan for %d image(s)...", len(image_paths))

    # Build image content blocks (base64 encoded)
    image_blocks: list[dict] = []
    for img_path in image_paths:
        mime_type, _ = mimetypes.guess_type(str(img_path))
        if mime_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            mime_type = "image/jpeg"  # default fallback

        with open(img_path, "rb") as f:
            img_data = base64.standard_b64encode(f.read()).decode("utf-8")

        image_blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": img_data,
                },
            }
        )

    # Append the text instruction after the images
    image_blocks.append(
        {
            "type": "text",
            "text": build_master_plan_user_message(user_prompt, len(image_paths)),
        }
    )

    # Call Claude — use claude-sonnet-4-6 for best vision + JSON output quality
    response = await anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=MASTER_PLAN_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": image_blocks}],
    )

    raw_text = response.content[0].text.strip()
    logger.debug("Raw Master Plan response (first 500 chars): %s", raw_text[:500])

    # Strip any accidental markdown code fences the model might have added
    if raw_text.startswith("```"):
        lines = raw_text.splitlines()
        # Remove first and last fence lines
        raw_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        master_plan: MasterPlan = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Claude returned invalid JSON for Master Plan: {exc}\n"
            f"Raw response excerpt: {raw_text[:300]}"
        ) from exc

    # Basic schema validation
    required_keys = {"product_bible", "scenes", "frames"}
    missing = required_keys - master_plan.keys()
    if missing:
        raise ValueError(f"Master Plan missing required keys: {missing}")
    if len(master_plan["scenes"]) != 3:
        raise ValueError(f"Expected 3 scenes, got {len(master_plan['scenes'])}")
    if len(master_plan["frames"]) != 4:
        raise ValueError(f"Expected 4 frames, got {len(master_plan['frames'])}")

    logger.info("Step 1 complete. Product bible: %.80s...", master_plan["product_bible"])
    return master_plan


# ---------------------------------------------------------------------------
# Step 2 — Keyframe generation via fal.ai (Flux Dev / Nano Banana 2)
# ---------------------------------------------------------------------------

async def _generate_single_frame(
    frame: dict,
    reference_image_url: str,
    job_dir: Path,
) -> Path:
    """Generate a single keyframe image using fal.ai Flux Dev."""
    frame_id = frame["frame_id"]
    image_prompt = frame["image_prompt"]
    dest_path = job_dir / f"frame_{frame_id}.jpg"

    logger.info("Generating frame %d...", frame_id)

    # fal_client.run is synchronous — wrap in executor
    loop = asyncio.get_event_loop()

    def _run_fal() -> dict:
        return fal_client.run(
            # Use fal-ai/flux/dev as the primary model (Nano Banana 2 / Flux-based)
            "fal-ai/flux/dev",
            arguments={
                "prompt": image_prompt,
                "image_url": reference_image_url,  # product reference for consistency
                "image_size": "landscape_16_9",
                "num_inference_steps": 28,
                "guidance_scale": 3.5,
                "num_images": 1,
                "enable_safety_checker": True,
                "output_format": "jpeg",
            },
        )

    result = await loop.run_in_executor(None, _run_fal)

    # Extract image URL from fal response
    images = result.get("images", [])
    if not images:
        raise RuntimeError(f"fal returned no images for frame {frame_id}: {result}")

    frame_url = images[0]["url"]
    await download_file(frame_url, dest_path)
    logger.info("Frame %d saved to %s", frame_id, dest_path)
    return dest_path


async def generate_keyframes(
    master_plan: MasterPlan,
    reference_image_path: Path,
    job_dir: Path,
) -> list[Path]:
    """
    Generate all N+1 keyframes in parallel using asyncio.gather().
    Returns list of local frame file paths ordered by frame_id.
    """
    logger.info("Step 2: Generating %d keyframes in parallel...", len(master_plan["frames"]))

    # Upload the reference product image once to fal storage
    reference_url = await upload_file_to_fal(reference_image_path)

    # Dispatch all frame generation tasks concurrently
    tasks = [
        _generate_single_frame(frame, reference_url, job_dir)
        for frame in master_plan["frames"]
    ]
    frame_paths: list[Path] = await asyncio.gather(*tasks)

    # Sort by frame_id order (gather preserves insertion order, but be explicit)
    frame_paths = sorted(frame_paths, key=lambda p: int(p.stem.split("_")[1]))
    logger.info("Step 2 complete. Frames: %s", [p.name for p in frame_paths])
    return frame_paths


# ---------------------------------------------------------------------------
# Step 3 — Video clip generation via fal.ai (Kling v1.6 Pro)
# ---------------------------------------------------------------------------

async def _generate_single_clip(
    scene: dict,
    frame_paths: list[Path],
    job_dir: Path,
) -> Path:
    """
    Generate a single video clip for a scene using Kling v1.6 Pro.
    Uses fal queue polling for async completion.
    """
    scene_id = scene["scene_id"]
    start_frame_path = frame_paths[scene["start_frame_id"]]
    end_frame_path = frame_paths[scene["end_frame_id"]]
    motion_prompt = scene["motion_prompt"]
    dest_path = job_dir / f"clip_{scene_id}.mp4"

    logger.info("Generating clip %d (frames %d→%d)...", scene_id, scene["start_frame_id"], scene["end_frame_id"])

    # Upload start and end frames to fal storage for this clip
    start_url, end_url = await asyncio.gather(
        upload_file_to_fal(start_frame_path),
        upload_file_to_fal(end_frame_path),
    )

    loop = asyncio.get_event_loop()

    def _submit_kling() -> dict:
        """Submit Kling job to fal queue and poll until complete."""
        handler = fal_client.submit(
            "fal-ai/kling-video/v1.6/pro/image-to-video",
            arguments={
                "prompt": motion_prompt,
                "image_url": start_url,
                "tail_image_url": end_url,
                "duration": "5",
                "aspect_ratio": "16:9",
                "negative_prompt": (
                    "blurry, low quality, distorted, watermark, text, "
                    "deformed product, wrong colors, artifacts"
                ),
                "cfg_scale": 0.5,
            },
        )
        # Poll the queue until complete
        result = handler.get()
        return result

    result = await loop.run_in_executor(None, _submit_kling)

    # Extract video URL from result
    video_url = None
    if isinstance(result, dict):
        # fal returns {"video": {"url": "..."}} or {"video_url": "..."}
        if "video" in result and isinstance(result["video"], dict):
            video_url = result["video"].get("url")
        elif "video_url" in result:
            video_url = result["video_url"]

    if not video_url:
        raise RuntimeError(f"Kling returned no video URL for scene {scene_id}: {result}")

    await download_file(video_url, dest_path)
    logger.info("Clip %d saved to %s", scene_id, dest_path)
    return dest_path


async def generate_video_clips(
    master_plan: MasterPlan,
    frame_paths: list[Path],
    job_dir: Path,
) -> list[Path]:
    """
    Generate all N video clips in parallel using asyncio.gather().
    Returns list of local clip file paths ordered by scene_id.
    """
    logger.info("Step 3: Generating %d video clips in parallel...", len(master_plan["scenes"]))

    tasks = [
        _generate_single_clip(scene, frame_paths, job_dir)
        for scene in master_plan["scenes"]
    ]
    clip_paths: list[Path] = await asyncio.gather(*tasks)

    # Sort by scene_id / clip number
    clip_paths = sorted(clip_paths, key=lambda p: int(p.stem.split("_")[1]))
    logger.info("Step 3 complete. Clips: %s", [p.name for p in clip_paths])
    return clip_paths


# ---------------------------------------------------------------------------
# Step 4 — Final assembly
# ---------------------------------------------------------------------------

async def assemble_final_video(clip_paths: list[Path], job_dir: Path) -> Path:
    """Concatenate all clips into final_video.mp4 using ffmpeg."""
    logger.info("Step 4: Assembling final video from %d clips...", len(clip_paths))
    output_path = job_dir / "final_video.mp4"
    await concatenate_clips(clip_paths, output_path)
    logger.info("Step 4 complete. Final video: %s (%.1f MB)", output_path, output_path.stat().st_size / 1_048_576)
    return output_path


# ---------------------------------------------------------------------------
# Full pipeline orchestrator
# ---------------------------------------------------------------------------

async def run_pipeline(
    job_id: str,
    image_paths: list[Path],
    user_prompt: str,
    job_state: JobState,
    anthropic_client: anthropic.AsyncAnthropic,
) -> None:
    """
    Orchestrates the full 4-step video generation pipeline.
    Updates job_state in-place so the /status endpoint can poll progress.

    job_state keys updated throughout:
        status: "processing" | "completed" | "failed"
        step: 1 | 2 | 3 | 4
        error: str (only on failure)
        result_path: Path (on completion)
        master_plan: dict (after step 1)
    """
    job_dir = get_job_dir(job_id)

    try:
        # ── Step 1: Master Plan ──────────────────────────────────────────────
        job_state["step"] = 1
        master_plan = await generate_master_plan(image_paths, user_prompt, anthropic_client)
        job_state["master_plan"] = master_plan

        # ── Step 2: Keyframes ────────────────────────────────────────────────
        job_state["step"] = 2
        # Use the first uploaded image as the visual reference anchor
        frame_paths = await generate_keyframes(master_plan, image_paths[0], job_dir)

        # ── Step 3: Video Clips ──────────────────────────────────────────────
        job_state["step"] = 3
        clip_paths = await generate_video_clips(master_plan, frame_paths, job_dir)

        # ── Step 4: Final Assembly ────────────────────────────────────────────
        job_state["step"] = 4
        final_video = await assemble_final_video(clip_paths, job_dir)

        job_state["status"] = "completed"
        job_state["result_path"] = final_video
        logger.info("Pipeline complete for job %s → %s", job_id, final_video)

    except Exception as exc:
        logger.exception("Pipeline failed for job %s at step %d", job_id, job_state.get("step", "?"))
        job_state["status"] = "failed"
        job_state["error"] = str(exc)
