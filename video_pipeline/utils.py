"""
Utility functions for the video generation pipeline.
- fal.ai file upload helper
- ffmpeg video concatenation wrapper
- ffmpeg audio mixing helper
- Job output directory management
"""

import asyncio
import logging
import os
from pathlib import Path

import aiofiles
import aiohttp
import fal_client

logger = logging.getLogger(__name__)

# Base directory for all job outputs
JOBS_DIR = Path(os.getenv("JOBS_DIR", "/tmp/video_pipeline_jobs"))


def get_job_dir(job_id: str) -> Path:
    """Return (and create) the output directory for a given job."""
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


async def upload_file_to_fal(file_path: str | Path) -> str:
    """
    Upload a local file to fal.ai storage and return the CDN URL.
    fal_client.upload_file is synchronous, so we run it in a thread pool.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found for fal upload: {file_path}")

    logger.info("Uploading %s to fal storage...", file_path.name)
    loop = asyncio.get_event_loop()
    url: str = await loop.run_in_executor(
        None,
        lambda: fal_client.upload_file(str(file_path)),
    )
    logger.info("Uploaded %s → %s", file_path.name, url)
    return url


async def upload_files_to_fal(file_paths: list[Path]) -> list[str]:
    """Upload multiple files to fal storage in parallel."""
    return list(await asyncio.gather(*[upload_file_to_fal(p) for p in file_paths]))


async def download_file(url: str, dest_path: str | Path) -> Path:
    """Download a remote file to dest_path using aiohttp."""
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Downloading %s → %s", url, dest_path)
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            response.raise_for_status()
            async with aiofiles.open(dest_path, "wb") as f:
                async for chunk in response.content.iter_chunked(1024 * 64):
                    await f.write(chunk)

    logger.info("Downloaded to %s (%.1f KB)", dest_path, dest_path.stat().st_size / 1024)
    return dest_path


def build_concat_list(clip_paths: list[Path], list_file: Path) -> None:
    """Write an ffmpeg concat list file from a list of clip paths."""
    with open(list_file, "w") as f:
        for clip in clip_paths:
            safe_path = str(clip.resolve()).replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")


async def concatenate_clips(clip_paths: list[Path], output_path: Path) -> Path:
    """
    Concatenate MP4 clips into a single final_video.mp4 using ffmpeg.
    Tries stream copy first (fast), falls back to H.264 re-encode.
    """
    if not clip_paths:
        raise ValueError("No clips provided for concatenation")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = output_path.parent / "concat_list.txt"
    build_concat_list(clip_paths, list_file)

    logger.info("Concatenating %d clips → %s", len(clip_paths), output_path)

    # Try stream copy (fastest, no quality loss)
    cmd_copy = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd_copy,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode == 0:
        logger.info("Concatenation complete (stream copy): %s", output_path)
        return output_path

    # Fallback: re-encode with H.264
    logger.warning("Stream copy failed (rc=%d), re-encoding. stderr: %s", proc.returncode, stderr.decode()[-400:])
    cmd_reencode = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac",
        str(output_path),
    ]
    proc2 = await asyncio.create_subprocess_exec(
        *cmd_reencode,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr2 = await proc2.communicate()
    if proc2.returncode != 0:
        raise RuntimeError(f"ffmpeg re-encode failed (rc={proc2.returncode}): {stderr2.decode()[-800:]}")

    logger.info("Concatenation complete (re-encoded): %s", output_path)
    return output_path


async def add_music_to_video(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> Path:
    """
    Mix an audio track into a video using ffmpeg.
    - Loops the audio if shorter than the video
    - Trims to exact video length
    - Copies video stream (no re-encode)
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Adding music %s to %s → %s", audio_path.name, video_path.name, output_path)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-stream_loop", "-1",       # loop audio indefinitely
        "-i", str(audio_path),
        "-map", "0:v",              # video from input 0
        "-map", "1:a",              # audio from input 1
        "-c:v", "copy",             # no video re-encode
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",                # trim to video length
        str(output_path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg music mix failed (rc={proc.returncode}): {stderr.decode()[-800:]}")

    logger.info("Music mix complete: %s (%.1f MB)", output_path, output_path.stat().st_size / 1_048_576)
    return output_path


def cleanup_job_dir(job_id: str) -> None:
    """Remove all files for a job after the final video has been downloaded."""
    import shutil
    job_dir = JOBS_DIR / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
        logger.info("Cleaned up job directory: %s", job_dir)
