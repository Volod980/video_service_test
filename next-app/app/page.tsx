"use client";
import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import DropZone from "@/components/DropZone";
import ProgressSteps from "@/components/ProgressSteps";
import { startGeneration, getStatus, estimateCost } from "@/lib/api";
import type { StatusResponse } from "@/lib/api";

export default function GeneratorPage() {
  const router = useRouter();

  // Form state
  const [files, setFiles] = useState<File[]>([]);
  const [prompt, setPrompt] = useState("");
  const [numScenes, setNumScenes] = useState(3);
  const [imageQuality, setImageQuality] = useState<"standard" | "pro" | "ultra">("pro");
  const [videoQuality, setVideoQuality] = useState<"standard" | "pro">("pro");
  const [clipDuration, setClipDuration] = useState<5 | 10>(5);

  // Job state
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<StatusResponse | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const cost = estimateCost(numScenes, imageQuality, videoQuality, clipDuration);

  // Poll status once job is started
  useEffect(() => {
    if (!jobId) return;
    pollRef.current = setInterval(async () => {
      try {
        const s = await getStatus(jobId);
        setJobStatus(s);
        if (s.status === "completed") {
          clearInterval(pollRef.current!);
          setTimeout(() => router.push(`/editor/${jobId}`), 1200);
        } else if (s.status === "failed") {
          clearInterval(pollRef.current!);
          setError(s.error ?? "Pipeline failed.");
        }
      } catch {}
    }, 3000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [jobId, router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!files.length) { setError("Upload at least one product photo."); return; }
    if (!prompt.trim()) { setError("Enter a prompt."); return; }
    setError(null);
    setIsSubmitting(true);

    const fd = new FormData();
    files.forEach((f) => fd.append("images", f));
    fd.append("prompt", prompt);
    fd.append("num_scenes", String(numScenes));
    fd.append("image_quality", imageQuality);
    fd.append("video_quality", videoQuality);
    fd.append("clip_duration", String(clipDuration));

    try {
      const res = await startGeneration(fd);
      setJobId(res.job_id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to start generation.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const isGenerating = !!jobId && jobStatus?.status === "processing";
  const isDone = jobStatus?.status === "completed";

  return (
    <main className="min-h-screen bg-bg flex flex-col">
      {/* Header */}
      <header className="border-b border-border px-6 py-4 flex items-center gap-3">
        <div className="w-7 h-7 rounded-lg bg-accent flex items-center justify-center">
          <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M15 10l4.553-2.069A1 1 0 0121 8.82v6.36a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
          </svg>
        </div>
        <span className="font-bold text-white text-lg">AI Video Studio</span>
      </header>

      <div className="flex-1 flex items-start justify-center px-4 py-10">
        <div className="w-full max-w-2xl space-y-6">

          {!jobId ? (
            /* ── Generator Form ─────────────────────────────────────────── */
            <form onSubmit={handleSubmit} className="space-y-6">
              <div>
                <h1 className="text-3xl font-bold text-white">Generate Product Video</h1>
                <p className="text-gray-500 mt-1 text-sm">
                  Upload photos, describe your vision — AI handles the rest.
                </p>
              </div>

              {/* Upload */}
              <section className="bg-surface rounded-xl p-5 border border-border space-y-3">
                <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
                  Product Photos
                </h2>
                <DropZone files={files} onChange={setFiles} />
              </section>

              {/* Prompt */}
              <section className="bg-surface rounded-xl p-5 border border-border space-y-3">
                <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
                  Video Brief
                </h2>
                <textarea
                  rows={4}
                  placeholder="Describe the desired mood and style — e.g. 'Minimal white studio, slow cinematic reveal, morning light, dark clean background'"
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  className="w-full bg-bg border border-border rounded-lg p-3 text-sm text-gray-200
                    resize-none focus:outline-none focus:border-accent/60 placeholder-gray-700"
                />
              </section>

              {/* Settings */}
              <section className="bg-surface rounded-xl p-5 border border-border space-y-4">
                <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
                  Settings
                </h2>

                {/* Number of scenes */}
                <div className="space-y-2">
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-400">Number of scenes</span>
                    <span className="text-white font-medium">{numScenes} scenes · {numScenes + 1} frames</span>
                  </div>
                  <input
                    type="range" min={2} max={5} step={1}
                    value={numScenes}
                    onChange={(e) => setNumScenes(Number(e.target.value))}
                    className="w-full accent-accent"
                  />
                  <div className="flex justify-between text-xs text-gray-700">
                    <span>2 (fast)</span><span>3</span><span>4</span><span>5 (detailed)</span>
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-3">
                  {/* Image quality */}
                  <div className="space-y-1.5">
                    <label className="text-xs text-gray-500">Frame Quality</label>
                    <select
                      value={imageQuality}
                      onChange={(e) => setImageQuality(e.target.value as "standard" | "pro" | "ultra")}
                      className="w-full bg-bg border border-border rounded-lg px-2 py-2 text-sm text-gray-200
                        focus:outline-none focus:border-accent/60"
                    >
                      <option value="standard">Standard (1K)</option>
                      <option value="pro">Pro (2K)</option>
                      <option value="ultra">Ultra (4K)</option>
                    </select>
                  </div>

                  {/* Video quality */}
                  <div className="space-y-1.5">
                    <label className="text-xs text-gray-500">Video Quality</label>
                    <select
                      value={videoQuality}
                      onChange={(e) => setVideoQuality(e.target.value as "standard" | "pro")}
                      className="w-full bg-bg border border-border rounded-lg px-2 py-2 text-sm text-gray-200
                        focus:outline-none focus:border-accent/60"
                    >
                      <option value="standard">Standard</option>
                      <option value="pro">Pro</option>
                    </select>
                  </div>

                  {/* Clip duration */}
                  <div className="space-y-1.5">
                    <label className="text-xs text-gray-500">Clip Duration</label>
                    <select
                      value={clipDuration}
                      onChange={(e) => setClipDuration(Number(e.target.value) as 5 | 10)}
                      className="w-full bg-bg border border-border rounded-lg px-2 py-2 text-sm text-gray-200
                        focus:outline-none focus:border-accent/60"
                    >
                      <option value={5}>5 seconds</option>
                      <option value={10}>10 seconds</option>
                    </select>
                  </div>
                </div>

                {/* Cost estimate */}
                <div className="bg-bg rounded-lg p-3 border border-border flex justify-between text-xs text-gray-500">
                  <span>Estimated cost</span>
                  <div className="text-right space-x-4">
                    <span>Frames ~${cost.frames}</span>
                    <span>Clips ~${cost.clips}</span>
                    <span className="text-gray-300 font-medium">Total ~${cost.total}</span>
                  </div>
                </div>
              </section>

              {error && (
                <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-sm text-red-400">
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={isSubmitting || !files.length || !prompt.trim()}
                className="w-full py-3 rounded-xl bg-accent text-white font-semibold text-base
                  hover:bg-accent-light disabled:opacity-40 disabled:cursor-not-allowed
                  transition-colors flex items-center justify-center gap-2"
              >
                {isSubmitting ? (
                  <>
                    <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                        d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                    Starting...
                  </>
                ) : (
                  "Generate Video"
                )}
              </button>
            </form>
          ) : (
            /* ── Progress View ──────────────────────────────────────────── */
            <div className="space-y-6">
              <div>
                <h1 className="text-2xl font-bold text-white">
                  {isDone ? "Video Ready!" : "Generating Your Video"}
                </h1>
                <p className="text-gray-500 mt-1 text-sm">
                  Job ID: <span className="font-mono text-gray-400">{jobId.slice(0, 8)}...</span>
                </p>
              </div>

              <div className="bg-surface rounded-xl p-5 border border-border">
                <ProgressSteps
                  currentStep={jobStatus?.step ?? 0}
                  status={jobStatus?.status ?? "processing"}
                />
              </div>

              {isDone && (
                <div className="bg-accent/10 border border-accent/30 rounded-xl p-4 text-center space-y-3">
                  <p className="text-accent font-medium">
                    All done! Redirecting to editor...
                  </p>
                  <button
                    onClick={() => router.push(`/editor/${jobId}`)}
                    className="px-6 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent-light transition-colors"
                  >
                    Open Editor
                  </button>
                </div>
              )}

              {jobStatus?.status === "failed" && (
                <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 space-y-2">
                  <p className="text-red-400 font-medium">Generation failed</p>
                  <p className="text-xs text-red-400/70">{jobStatus.error}</p>
                  <button
                    onClick={() => { setJobId(null); setJobStatus(null); }}
                    className="text-sm text-gray-400 hover:text-white transition-colors"
                  >
                    ← Try again
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
