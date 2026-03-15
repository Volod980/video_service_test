# AI Video Generation Pipeline

Upload 1–10 product photos + a text prompt → get a cinematic product video.

The pipeline runs 4 sequential steps:
1. **Master Plan** — Claude analyzes photos and generates a detailed shooting script (JSON)
2. **Keyframes** — fal.ai Flux Dev generates 4 keyframe images in parallel
3. **Video Clips** — fal.ai Kling v1.6 Pro generates 3 × 5-second clips in parallel
4. **Assembly** — ffmpeg concatenates clips into `final_video.mp4`

---

## Project Structure

```
video_service_test/
├── video_pipeline/
│   ├── main.py          # FastAPI app + API endpoints
│   ├── pipeline.py      # Core 4-step pipeline logic
│   ├── prompts.py       # System prompt for Claude Master Plan call
│   ├── utils.py         # fal upload helper, ffmpeg wrapper, cleanup
│   ├── requirements.txt
│   └── .env.example
├── Dockerfile
├── docker-compose.yml
├── railway.toml
└── .env.example
```

---

## Local Setup

### Prerequisites
- Python 3.11+
- [ffmpeg](https://ffmpeg.org/download.html) installed and on `$PATH`
- [Anthropic API key](https://console.anthropic.com/account/keys)
- [fal.ai API key](https://fal.ai/dashboard/keys)

### 1. Install dependencies

```bash
cd video_pipeline
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your API keys
```

### 3. Run the server

```bash
uvicorn main:app --reload --port 8000
```

Interactive API docs: http://localhost:8000/docs

### 4. Run with Docker Compose

```bash
cp .env.example .env   # fill in keys
docker compose up --build
```

---

## Deploy to Railway

Railway is the recommended hosting platform — it supports Dockerfile deployments and provides $5/month free credits.

### Step 1 — Push to GitHub

```bash
git add .
git commit -m "Initial commit"
git push origin main
```

### Step 2 — Connect Railway

1. Go to [railway.app](https://railway.app) and click **New Project**
2. Select **Deploy from GitHub repo** → choose your repository
3. Railway auto-detects the `Dockerfile` and `railway.toml`

### Step 3 — Set Environment Variables

In Railway dashboard → your service → **Variables**, add:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` |
| `FAL_KEY` | `...` |
| `JOBS_DIR` | `/tmp/video_pipeline_jobs` |

Railway automatically injects `PORT` — no need to set it manually.

Click **Deploy** — your API is live in ~2 minutes. 🚀

---

## API Reference

### `POST /generate`

Start a video generation job.

**Request** (multipart/form-data):
- `images[]` — 1–10 product photos (JPEG/PNG/WebP, max 20 MB each)
- `prompt` — text describing desired video mood/style

**Response**:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Video generation started",
  "status_url": "/status/550e8400-...",
  "download_url": "/download/550e8400-..."
}
```

**curl example**:
```bash
curl -X POST https://your-app.railway.app/generate \
  -F "images[]=@product_front.jpg" \
  -F "images[]=@product_side.jpg" \
  -F "prompt=Minimal white studio, slow cinematic reveal, morning light"
```

---

### `GET /status/{job_id}`

Poll pipeline progress.

**Response**:
```json
{
  "job_id": "550e8400-...",
  "status": "processing",
  "step": 2,
  "step_label": "generating keyframes",
  "result_url": null,
  "error": null
}
```

Steps: `1` = Master Plan, `2` = Keyframes, `3` = Video Clips, `4` = Assembly

When `status` is `"completed"`, `result_url` is populated.

---

### `GET /download/{job_id}`

Download the finished `final_video.mp4`.

```bash
curl -o my_video.mp4 https://your-app.railway.app/download/550e8400-...
```

> Note: job files are cleaned up from disk after the video is downloaded.

---

### `GET /health`

Liveness probe — returns `{"status": "ok"}`.

---

## How the Pipeline Works

### Step 1 — Master Plan (Claude)

Claude receives all product images as base64 and generates a strict JSON plan:
- `product_bible` — 150–200 word hyper-specific product description (exact colors, materials, logo details, textures)
- `frames[0..3]` — 4 keyframe prompts, each starting with the full `product_bible`
- `scenes[1..3]` — 3 motion prompts describing camera movement + action for Kling

### Step 2 — Keyframes (fal.ai Flux Dev)

4 frames generated in parallel (`asyncio.gather`). Each uses:
- The frame's `image_prompt` (starts with `product_bible`)
- First uploaded product photo as visual reference anchor

Saved as `frame_0.jpg` … `frame_3.jpg`.

### Step 3 — Video Clips (fal.ai Kling v1.6 Pro)

3 clips generated in parallel. Each uses:
- `start_image_url` + `end_image_url` from consecutive frames
- `motion_prompt` from the scene
- Duration: 5 seconds, aspect ratio: 16:9

Saved as `clip_1.mp4` … `clip_3.mp4`.

### Step 4 — Assembly (ffmpeg)

`ffmpeg -f concat` concatenates all clips into `final_video.mp4` using stream copy (no re-encoding) when possible.

---

## Frontend

The frontend should be a separate Next.js application deployed independently to Vercel or Netlify.

It communicates with this backend over HTTP:
1. `POST /generate` with a `FormData` containing images + prompt
2. Poll `GET /status/{job_id}` every 5 seconds
3. Redirect to `GET /download/{job_id}` when `status === "completed"`

Set `NEXT_PUBLIC_API_URL` in the frontend's environment variables to point to this backend's Railway URL.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude |
| `FAL_KEY` | Yes | fal.ai API key |
| `JOBS_DIR` | No | Temp directory for job files (default: `/tmp/video_pipeline_jobs`) |
| `PORT` | No | Server port (default: `8000`; Railway injects this automatically) |
| `ENV` | No | Set to `development` for hot-reload |

---

## Cost Estimate (per video)

| Step | Model | Approx. Cost |
|---|---|---|
| Master Plan | Claude Sonnet | ~$0.01 |
| 4 Keyframes | Flux Dev (fal.ai) | ~$0.04 |
| 3 × 5s Clips | Kling v1.6 Pro (fal.ai) | ~$0.90 |
| **Total** | | **~$0.95** |

Prices are approximate and subject to change. Check [fal.ai pricing](https://fal.ai/pricing) and [Anthropic pricing](https://www.anthropic.com/pricing) for current rates.
