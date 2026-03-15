# AI Video Studio — Next.js Frontend

Dark-themed product video editor built with Next.js 14 (App Router) + Tailwind CSS.

## Pages

- **`/`** — Generator: upload product photos, set quality/scene/duration, track progress
- **`/editor/[job_id]`** — Editor: timeline, frame/clip preview, AI-powered prompt editing, music overlay

## Local Setup

```bash
cd next-app
cp .env.example .env.local
# Set NEXT_PUBLIC_API_URL to your running backend (e.g. http://localhost:8000)

npm install
npm run dev
```

Open http://localhost:3000

## Deploy to Railway (alongside the backend)

### Step 1 — Push to GitHub
The next-app/ directory is part of the same repo as the backend. Push as normal.

### Step 2 — Add a second Railway service
1. In your Railway project, click **+ New Service → GitHub Repo**
2. Select the same repo
3. Set the **Root Directory** to `next-app`
4. Railway will detect the `Dockerfile` and build it

### Step 3 — Set environment variables
In the Railway dashboard for the frontend service, add:

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | The Railway URL of your backend service (e.g. `https://video-pipeline-api.railway.app`) |

Click **Deploy** — your frontend is live in ~2 minutes.

> Note: `NEXT_PUBLIC_API_URL` is baked in at **build time** by Next.js.
> If you change the backend URL after deploying, redeploy the frontend.

## Deploy to Vercel or Netlify

Both platforms support Next.js natively without a Dockerfile.

**Vercel:**
1. `vercel --cwd next-app` or connect the repo in the Vercel dashboard
2. Set Root Directory to `next-app`
3. Add `NEXT_PUBLIC_API_URL` in project settings

**Netlify:**
1. Connect repo, set Base directory to `next-app`
2. Build command: `npm run build`
3. Add `NEXT_PUBLIC_API_URL` as an environment variable

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | Yes | Full URL of the FastAPI backend (no trailing slash) |
