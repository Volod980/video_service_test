"""
Core pipeline logic for AI Video Generation.

Step 1 — Master Plan Generation (Claude / Anthropic API)
Step 2 — Keyframe Generation (fal.ai — Nano Banana 2 edit endpoint)
Step 3 — Video Clip Generation (fal.ai — Kling v1.6 Pro)
Step 4 — Final Assembly (ffmpeg concat)
"""

import asyncio
import base64
import json
import logging
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic
import fal_client

from prompts import (
    AI_EDIT_CLIP_SYSTEM_PROMPT,
    AI_EDIT_FRAME_SYSTEM_PROMPT,
    build_master_plan_system_prompt,
    build_master_plan_user_message,
)
from utils import (
    concatenate_clips,
    download_file,
    get_job_dir,
    upload_file_to_fal,
    upload_files_to_fal,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings dataclass
# ---------------------------------------------------------------------------

@dataclass
class PipelineSettings:
    num_scenes: int = 3          # 2–5 scenes
    image_quality: str = "pro"   # standard | pro | ultra
    video_quality: str = "pro"   # standard | pro
    clip_duration: int = 5       # 5 | 10 seconds

    @property
    def num_frames(self) -> int:
        return self.num_scenes + 1

    @property
    def nb2_resolution(self) -> str:
        """Nano Banana 2 resolution parameter."""
        return {"standard": "1K", "pro": "2K", "ultra": "4K"}.get(self.image_quality, "2K")

    @property
    def nb2_thinking(self) -> str | None:
        """Nano Banana 2 thinking mode (None = disabled)."""
        return {"standard": None, "pro": "minimal", "ultra": "high"}.get(self.image_quality)

    @property
    def kling_model(self) -> str:
        """Kling model endpoint based on video quality."""
        return (
            "fal-ai/kling-video/v1.6/pro/image-to-video"
            if self.video_quality == "pro"
            else "fal-ai/kling-video/v1.6/standard/image-to-video"
        )

    def to_dict(self) -> dict:
        return {
            "num_scenes": self.num_scenes,
            "image_quality": self.image_quality,
            "video_quality": self.video_quality,
            "clip_duration": self.clip_duration,
        }


# ---------------------------------------------------------------------------
# Step 1 — Master Plan via Claude
# ---------------------------------------------------------------------------

async def generate_master_plan(
    image_paths: list[Path],
    user_prompt: str,
    settings: PipelineSettings,
    anthropic_client: anthropic.AsyncAnthropic,
) -> dict[str, Any]:
    """
    Call Claude with all product images (base64) + user prompt.
    Returns parsed Master Plan JSON: product_bible, scenes, frames.
    """
    logger.info("Step 1: Generating Master Plan (%d scenes, %d frames)...", settings.num_scenes, settings.num_frames)

    # Build vision content blocks
    image_blocks: list[dict] = []
    for img_path in image_paths:
        mime_type, _ = mimetypes.guess_type(str(img_path))
        if mime_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            mime_type = "image/jpeg"
        with open(img_path, "rb") as f:
            img_data = base64.standard_b64encode(f.read()).decode("utf-8")
        image_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": mime_type, "data": img_data},
        })

    image_blocks.append({
        "type": "text",
        "text": build_master_plan_user_message(user_prompt, len(image_paths)),
    })

    response = await anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=build_master_plan_system_prompt(settings.num_scenes),
        messages=[{"role": "user", "content": image_blocks}],
    )

    raw_text = response.content[0].text.strip()

    # Strip accidental markdown fences
    if raw_text.startswith("```"):
        lines = raw_text.splitlines()
        raw_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        master_plan: dict = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Claude returned invalid JSON: {exc}\nExcerpt: {raw_text[:300]}"
        ) from exc

    # Validate schema
    missing = {"product_bible", "scenes", "frames"} - master_plan.keys()
    if missing:
        raise ValueError(f"Master Plan missing keys: {missing}")
    if len(master_plan["scenes"]) != settings.num_scenes:
        raise ValueError(f"Expected {settings.num_scenes} scenes, got {len(master_plan['scenes'])}")
    if len(master_plan["frames"]) != settings.num_frames:
        raise ValueError(f"Expected {settings.num_frames} frames, got {len(master_plan['frames'])}")

    logger.info("Step 1 complete. Product bible: %.80s...", master_plan["product_bible"])
    return master_plan


# ---------------------------------------------------------------------------
# Step 2 — Keyframe generation via Nano Banana 2 (fal-ai/nano-banana-2/edit)
# ---------------------------------------------------------------------------

async def generate_single_frame(
    frame: dict,
    reference_image_urls: list[str],
    job_dir: Path,
    settings: PipelineSettings,
) -> tuple[int, Path]:
    """
    Generate one keyframe using Nano Banana 2 edit endpoint.
    Uses all uploaded product photos as image_urls for maximum product consistency.
    Returns (frame_id, local_path).
    """
    frame_id = frame["frame_id"]
    image_prompt = frame["image_prompt"]
    dest_path = job_dir / f"frame_{frame_id}.jpg"

    logger.info("Generating frame %d (quality=%s, res=%s)...", frame_id, settings.image_quality, settings.nb2_resolution)

    loop = asyncio.get_event_loop()

    def _run_nb2() -> dict:
        # fal-ai/nano-banana-2/edit: image_urls are reference images used for product consistency
        # Passing all uploaded product photos keeps the product visually anchored
        args: dict = {
            "prompt": image_prompt,
            "image_urls": reference_image_urls,
            "num_images": 1,
            "aspect_ratio": "16:9",
            "output_format": "jpeg",
            "resolution": settings.nb2_resolution,
            "safety_tolerance": "6",  # permissive for commercial product photography
        }
        if settings.nb2_thinking:
            args["thinking"] = settings.nb2_thinking

        return fal_client.subscribe(
            "fal-ai/nano-banana-2/edit",
            arguments=args,
        )

    result = await loop.run_in_executor(None, _run_nb2)

    images = result.get("images", [])
    if not images:
        raise RuntimeError(f"Nano Banana 2 returned no images for frame {frame_id}: {result}")

    await download_file(images[0]["url"], dest_path)
    logger.info("Frame %d saved: %s", frame_id, dest_path)
    return frame_id, dest_path


async def generate_keyframes(
    master_plan: dict,
    reference_image_paths: list[Path],
    job_dir: Path,
    settings: PipelineSettings,
) -> list[Path]:
    """
    Generate all N+1 keyframes in parallel.
    Returns ordered list of local frame file paths.
    """
    logger.info("Step 2: Generating %d keyframes in parallel (Nano Banana 2)...", settings.num_frames)

    # Upload all reference product images to fal storage once
    reference_urls = await upload_files_to_fal(reference_image_paths)
    logger.info("Uploaded %d reference images to fal storage", len(reference_urls))

    tasks = [
        generate_single_frame(frame, reference_urls, job_dir, settings)
        for frame in master_plan["frames"]
    ]
    results: list[tuple[int, Path]] = await asyncio.gather(*tasks)

    # Sort by frame_id
    results.sort(key=lambda t: t[0])
    frame_paths = [path for _, path in results]
    logger.info("Step 2 complete. Frames: %s", [p.name for p in frame_paths])
    return frame_paths


# ---------------------------------------------------------------------------
# Step 3 — Video clip generation via Kling v1.6 Pro
# ---------------------------------------------------------------------------

async def generate_single_clip(
    scene: dict,
    frame_paths: list[Path],
    job_dir: Path,
    settings: PipelineSettings,
) -> tuple[int, Path]:
    """
    Generate one video clip for a scene using Kling v1.6 Pro.
    Returns (scene_id, local_path).
    """
    scene_id = scene["scene_id"]
    start_path = frame_paths[scene["start_frame_id"]]
    end_path = frame_paths[scene["end_frame_id"]]
    motion_prompt = scene["motion_prompt"]
    dest_path = job_dir / f"clip_{scene_id}.mp4"

    logger.info("Generating clip %d (frames %d→%d, model=%s)...",
                scene_id, scene["start_frame_id"], scene["end_frame_id"], settings.kling_model)

    # Upload start/end frames for this clip
    start_url, end_url = await asyncio.gather(
        upload_file_to_fal(start_path),
        upload_file_to_fal(end_path),
    )

    loop = asyncio.get_event_loop()

    def _submit_kling() -> dict:
        handler = fal_client.submit(
            settings.kling_model,
            arguments={
                "prompt": motion_prompt,
                "image_url": start_url,
                "tail_image_url": end_url,
                "duration": str(settings.clip_duration),
                "aspect_ratio": "16:9",
                "negative_prompt": (
                    "blurry, low quality, distorted, watermark, text overlay, "
                    "deformed product, wrong colors, artifacts, compression noise"
                ),
                "cfg_scale": 0.5,
            },
        )
        return handler.get()

    result = await loop.run_in_executor(None, _submit_kling)

    # Extract video URL
    video_url: str | None = None
    if isinstance(result, dict):
        if "video" in result and isinstance(result["video"], dict):
            video_url = result["video"].get("url")
        elif "video_url" in result:
            video_url = result["video_url"]

    if not video_url:
        raise RuntimeError(f"Kling returned no video URL for scene {scene_id}: {result}")

    await download_file(video_url, dest_path)
    logger.info("Clip %d saved: %s", scene_id, dest_path)
    return scene_id, dest_path


async def generate_video_clips(
    master_plan: dict,
    frame_paths: list[Path],
    job_dir: Path,
    settings: PipelineSettings,
) -> list[Path]:
    """
    Generate all N clips in parallel.
    Returns ordered list of local clip file paths.
    """
    logger.info("Step 3: Generating %d clips in parallel (Kling)...", settings.num_scenes)

    tasks = [
        generate_single_clip(scene, frame_paths, job_dir, settings)
        for scene in master_plan["scenes"]
    ]
    results: list[tuple[int, Path]] = await asyncio.gather(*tasks)
    results.sort(key=lambda t: t[0])
    clip_paths = [path for _, path in results]
    logger.info("Step 3 complete. Clips: %s", [p.name for p in clip_paths])
    return clip_paths


# ---------------------------------------------------------------------------
# Step 4 — Final assembly
# ---------------------------------------------------------------------------

async def assemble_final_video(clip_paths: list[Path], job_dir: Path) -> Path:
    """Concatenate clips into final_video.mp4 using ffmpeg."""
    logger.info("Step 4: Assembling %d clips...", len(clip_paths))
    output_path = job_dir / "final_video.mp4"
    from utils import concatenate_clips as _concat
    await _concat(clip_paths, output_path)
    logger.info("Step 4 complete: %s (%.1f MB)", output_path, output_path.stat().st_size / 1_048_576)
    return output_path


# ---------------------------------------------------------------------------
# AI-powered prompt rewriting (Feature 3)
# ---------------------------------------------------------------------------

async def ai_rewrite_frame_prompt(
    original_prompt: str,
    user_instruction: str,
    anthropic_client: anthropic.AsyncAnthropic,
) -> str:
    """Ask Claude to rewrite a frame image_prompt based on user instruction."""
    response = await anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=AI_EDIT_FRAME_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"ORIGINAL IMAGE PROMPT:\n{original_prompt}\n\n"
                f"USER INSTRUCTION: {user_instruction}\n\n"
                "Rewrite the prompt according to the instruction. "
                "Output ONLY the new prompt string, single line, no explanation."
            ),
        }],
    )
    return response.content[0].text.strip()


async def ai_rewrite_clip_prompt(
    original_prompt: str,
    user_instruction: str,
    anthropic_client: anthropic.AsyncAnthropic,
) -> str:
    """Ask Claude to rewrite a clip motion_prompt based on user instruction."""
    response = await anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=AI_EDIT_CLIP_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"ORIGINAL MOTION PROMPT:\n{original_prompt}\n\n"
                f"USER INSTRUCTION: {user_instruction}\n\n"
                "Rewrite the prompt according to the instruction. "
                "Output ONLY the new prompt string, single line, no explanation."
            ),
        }],
    )
    return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# Full pipeline orchestrator
# ---------------------------------------------------------------------------

async def run_pipeline(
    job_id: str,
    image_paths: list[Path],
    user_prompt: str,
    settings: PipelineSettings,
    job_state: dict,
    anthropic_client: anthropic.AsyncAnthropic,
) -> None:
    """
    Orchestrate the full 4-step pipeline, updating job_state throughout.

    job_state keys updated:
        status, step, error, master_plan,
        frames (list of {frame_id, path, prompt}),
        clips  (list of {scene_id, path, motion_prompt, start_frame_id, end_frame_id}),
        reference_image_paths, final_video_path
    """
    job_dir = get_job_dir(job_id)

    try:
        # ── Step 1: Master Plan ──────────────────────────────────────────
        job_state["step"] = 1
        master_plan = await generate_master_plan(image_paths, user_prompt, settings, anthropic_client)
        job_state["master_plan"] = master_plan

        # Store frame/clip prompts for later regeneration
        job_state["frames"] = [
            {"frame_id": f["frame_id"], "path": None, "prompt": f["image_prompt"]}
            for f in master_plan["frames"]
        ]
        job_state["clips"] = [
            {
                "scene_id": s["scene_id"],
                "path": None,
                "motion_prompt": s["motion_prompt"],
                "start_frame_id": s["start_frame_id"],
                "end_frame_id": s["end_frame_id"],
            }
            for s in master_plan["scenes"]
        ]
        job_state["reference_image_paths"] = [str(p) for p in image_paths]

        # ── Step 2: Keyframes ────────────────────────────────────────────
        job_state["step"] = 2
        frame_paths = await generate_keyframes(master_plan, image_paths, job_dir, settings)

        # Update frame paths in job_state
        for i, fp in enumerate(frame_paths):
            job_state["frames"][i]["path"] = str(fp)

        # ── Step 3: Video Clips ──────────────────────────────────────────
        job_state["step"] = 3
        clip_paths = await generate_video_clips(master_plan, frame_paths, job_dir, settings)

        # Update clip paths in job_state
        for i, cp in enumerate(clip_paths):
            job_state["clips"][i]["path"] = str(cp)

        # ── Step 4: Assembly ─────────────────────────────────────────────
        job_state["step"] = 4
        final_video = await assemble_final_video(clip_paths, job_dir)

        job_state["final_video_path"] = str(final_video)
        job_state["status"] = "completed"
        logger.info("Pipeline complete for job %s → %s", job_id, final_video)

    except Exception as exc:
        logger.exception("Pipeline failed for job %s at step %d", job_id, job_state.get("step", "?"))
        job_state["status"] = "failed"
        job_state["error"] = str(exc)
