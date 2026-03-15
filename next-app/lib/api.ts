/**
 * API client for the FastAPI video generation backend.
 * All functions use NEXT_PUBLIC_API_URL as the base URL.
 */

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ── Types ────────────────────────────────────────────────────────────────────

export interface GenerateResponse {
  job_id: string;
  message: string;
  status_url: string;
  download_url: string;
}

export interface StatusResponse {
  job_id: string;
  status: "processing" | "completed" | "failed";
  step: number;
  step_label: string;
  settings: Record<string, unknown> | null;
  result_url: string | null;
  frames_url: string | null;
  clips_url: string | null;
  error: string | null;
}

export interface FrameInfo {
  frame_id: number;
  prompt: string;
  image_url: string | null;
  ready: boolean;
}

export interface ClipInfo {
  scene_id: number;
  motion_prompt: string;
  start_frame_id: number;
  end_frame_id: number;
  video_url: string | null;
  ready: boolean;
}

export interface FramesResponse {
  job_id: string;
  frames: FrameInfo[];
}

export interface ClipsResponse {
  job_id: string;
  clips: ClipInfo[];
}

// ── Helpers ──────────────────────────────────────────────────────────────────

async function apiRequest<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  // 202 responses may return JSON or nothing
  const text = await res.text();
  return text ? JSON.parse(text) : ({} as T);
}

/** Returns a URL that serves the asset from the backend */
export function assetUrl(relPath: string): string {
  return `${BASE}${relPath}`;
}

// ── Endpoints ────────────────────────────────────────────────────────────────

export async function startGeneration(formData: FormData): Promise<GenerateResponse> {
  return apiRequest<GenerateResponse>("/generate", { method: "POST", body: formData });
}

export async function getStatus(jobId: string): Promise<StatusResponse> {
  return apiRequest<StatusResponse>(`/status/${jobId}`);
}

export async function getFrames(jobId: string): Promise<FramesResponse> {
  return apiRequest<FramesResponse>(`/jobs/${jobId}/frames`);
}

export async function getClips(jobId: string): Promise<ClipsResponse> {
  return apiRequest<ClipsResponse>(`/jobs/${jobId}/clips`);
}

export async function regenerateFrame(
  jobId: string,
  frameId: number,
  customPrompt?: string,
): Promise<unknown> {
  return apiRequest(`/jobs/${jobId}/regenerate-frame/${frameId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ custom_prompt: customPrompt ?? null }),
  });
}

export async function regenerateClip(
  jobId: string,
  sceneId: number,
  customPrompt?: string,
): Promise<unknown> {
  return apiRequest(`/jobs/${jobId}/regenerate-clip/${sceneId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ custom_prompt: customPrompt ?? null }),
  });
}

export async function aiEditFrame(
  jobId: string,
  frameId: number,
  userInstruction: string,
): Promise<unknown> {
  return apiRequest(`/jobs/${jobId}/ai-edit-frame/${frameId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_instruction: userInstruction }),
  });
}

export async function aiEditClip(
  jobId: string,
  sceneId: number,
  userInstruction: string,
): Promise<unknown> {
  return apiRequest(`/jobs/${jobId}/ai-edit-clip/${sceneId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_instruction: userInstruction }),
  });
}

export async function assembleVideo(jobId: string): Promise<unknown> {
  return apiRequest(`/jobs/${jobId}/assemble`, { method: "POST" });
}

export async function addMusic(jobId: string, audioFile: File): Promise<unknown> {
  const fd = new FormData();
  fd.append("audio", audioFile);
  return apiRequest(`/jobs/${jobId}/add-music`, { method: "POST", body: fd });
}

export function downloadUrl(jobId: string): string {
  return `${BASE}/download/${jobId}`;
}

export function downloadWithMusicUrl(jobId: string): string {
  return `${BASE}/jobs/${jobId}/download-with-music`;
}

// ── Cost estimates ────────────────────────────────────────────────────────────

export function estimateCost(
  numScenes: number,
  imageQuality: string,
  videoQuality: string,
  clipDuration: number,
): { frames: number; clips: number; total: number } {
  const numFrames = numScenes + 1;
  const frameRate: Record<string, number> = { standard: 0.01, pro: 0.015, ultra: 0.02 };
  const clipRate: Record<string, number> = { standard: 0.2, pro: 0.35 };
  const durationMult = clipDuration === 10 ? 2 : 1;

  const frameCost = numFrames * (frameRate[imageQuality] ?? 0.015);
  const clipCost = numScenes * (clipRate[videoQuality] ?? 0.35) * durationMult;
  return {
    frames: Math.round(frameCost * 100) / 100,
    clips: Math.round(clipCost * 100) / 100,
    total: Math.round((frameCost + clipCost + 0.01) * 100) / 100,
  };
}
