"use client";
import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import EditPanel from "@/components/EditPanel";
import {
  assembleVideo,
  addMusic,
  downloadUrl,
  downloadWithMusicUrl,
  getFrames,
  getClips,
  assetUrl,
} from "@/lib/api";
import type { ClipInfo, FrameInfo } from "@/lib/api";

type Selected =
  | { type: "frame"; data: FrameInfo }
  | { type: "clip"; data: ClipInfo }
  | null;

export default function EditorPage() {
  const { job_id: jobId } = useParams<{ job_id: string }>();
  const router = useRouter();

  const [frames, setFrames] = useState<FrameInfo[]>([]);
  const [clips, setClips] = useState<ClipInfo[]>([]);
  const [selected, setSelected] = useState<Selected>(null);
  const [assembling, setAssembling] = useState(false);
  const [assembled, setAssembled] = useState(false);
  const [musicFile, setMusicFile] = useState<File | null>(null);
  const [musicUploading, setMusicUploading] = useState(false);
  const [musicReady, setMusicReady] = useState(false);
  const [showMusicModal, setShowMusicModal] = useState(false);
  const musicInputRef = useRef<HTMLInputElement>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  const loadData = async () => {
    try {
      const [fr, cl] = await Promise.all([getFrames(jobId), getClips(jobId)]);
      setFrames(fr.frames);
      setClips(cl.clips);
      // Mark assembled if all clips are ready
      if (cl.clips.every((c) => c.ready)) setAssembled(true);
    } catch (e) {
      console.error("Failed to load job data", e);
    }
  };

  useEffect(() => {
    if (jobId) loadData();
  }, [jobId]);

  // Interleave frames and clips for the timeline
  // Timeline order: F0, Clip1, F1, Clip2, F2, Clip3, F3
  const timelineItems = frames.flatMap((frame, i) => {
    const clip = clips.find((c) => c.start_frame_id === i);
    return clip ? [{ kind: "frame" as const, data: frame }, { kind: "clip" as const, data: clip }] : [{ kind: "frame" as const, data: frame }];
  });

  const handleAssemble = async () => {
    setAssembling(true);
    setStatusMsg(null);
    try {
      await assembleVideo(jobId);
      setStatusMsg("Assembly started — ready in ~10 seconds.");
      setTimeout(() => { setAssembled(true); setAssembling(false); setStatusMsg(null); }, 12_000);
    } catch (e: unknown) {
      setStatusMsg(`Error: ${e instanceof Error ? e.message : String(e)}`);
      setAssembling(false);
    }
  };

  const handleMusicUpload = async () => {
    if (!musicFile) return;
    setMusicUploading(true);
    setStatusMsg(null);
    try {
      await addMusic(jobId, musicFile);
      setStatusMsg("Music is being mixed in — ready in ~15 seconds.");
      setTimeout(() => { setMusicReady(true); setMusicUploading(false); setShowMusicModal(false); setStatusMsg(null); }, 20_000);
    } catch (e: unknown) {
      setStatusMsg(`Error: ${e instanceof Error ? e.message : String(e)}`);
      setMusicUploading(false);
    }
  };

  const selectedFrame = selected?.type === "frame" ? selected.data : null;
  const selectedClip = selected?.type === "clip" ? selected.data : null;

  return (
    <div className="h-screen bg-bg flex flex-col overflow-hidden">
      {/* Top bar */}
      <header className="flex-shrink-0 border-b border-border px-4 py-2.5 flex items-center gap-3">
        <button
          onClick={() => router.push("/")}
          className="text-gray-600 hover:text-white transition-colors"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
        </button>
        <span className="text-sm font-bold text-white flex-1">
          Editor <span className="text-gray-600 font-mono font-normal text-xs ml-1">{jobId?.slice(0, 8)}</span>
        </span>

        <div className="flex items-center gap-2">
          {statusMsg && (
            <span className={`text-xs px-2 py-1 rounded ${statusMsg.startsWith("Error") ? "text-red-400" : "text-accent-light"}`}>
              {statusMsg}
            </span>
          )}

          {/* Assemble */}
          <button
            onClick={handleAssemble}
            disabled={assembling}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface border border-border
              text-sm text-gray-300 hover:border-accent/60 hover:text-white disabled:opacity-40 transition-all"
          >
            {assembling ? (
              <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            ) : (
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            )}
            Assemble
          </button>

          {/* Add music */}
          <button
            onClick={() => setShowMusicModal(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface border border-border
              text-sm text-gray-300 hover:border-accent/60 hover:text-white transition-all"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
            </svg>
            Add Music
          </button>

          {/* Download */}
          <a
            href={musicReady ? downloadWithMusicUrl(jobId) : downloadUrl(jobId)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-all
              ${assembled
                ? "bg-accent text-white hover:bg-accent-light"
                : "bg-surface border border-border text-gray-600 cursor-not-allowed pointer-events-none opacity-40"
              }`}
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
            Download{musicReady ? " (+Music)" : ""}
          </a>
        </div>
      </header>

      {/* Timeline */}
      <div className="flex-shrink-0 border-b border-border bg-surface px-4 py-3">
        <p className="text-xs text-gray-600 mb-2 uppercase tracking-wide">Timeline</p>
        <div className="flex items-center gap-1 overflow-x-auto pb-1">
          {timelineItems.map((item, idx) =>
            item.kind === "frame" ? (
              <button
                key={`f-${item.data.frame_id}`}
                onClick={() => setSelected({ type: "frame", data: item.data })}
                className={`
                  flex-shrink-0 w-14 h-14 rounded-lg overflow-hidden border-2 transition-all
                  ${selected?.type === "frame" && selected.data.frame_id === item.data.frame_id
                    ? "border-accent shadow-lg shadow-accent/20"
                    : "border-border hover:border-gray-600"
                  }
                  ${!item.data.ready ? "bg-surface" : ""}
                `}
              >
                {item.data.ready && item.data.image_url ? (
                  <img
                    src={assetUrl(item.data.image_url)}
                    alt={`Frame ${item.data.frame_id}`}
                    className="w-full h-full object-cover"
                  />
                ) : (
                  <div className="w-full h-full bg-surface flex items-center justify-center">
                    <span className="text-xs text-gray-600">F{item.data.frame_id}</span>
                  </div>
                )}
              </button>
            ) : (
              <button
                key={`c-${item.data.scene_id}`}
                onClick={() => setSelected({ type: "clip", data: item.data })}
                className={`
                  flex-shrink-0 flex items-center justify-center
                  w-10 h-10 rounded-lg border-2 transition-all
                  ${selected?.type === "clip" && selected.data.scene_id === item.data.scene_id
                    ? "border-accent bg-accent/10"
                    : "border-border bg-surface hover:border-gray-600"
                  }
                `}
              >
                <svg className={`w-4 h-4 ${item.data.ready ? "text-accent-light" : "text-gray-700"}`}
                  fill="currentColor" viewBox="0 0 24 24">
                  <path d="M8 5v14l11-7z" />
                </svg>
              </button>
            )
          )}
        </div>
      </div>

      {/* Main area: preview + edit panel */}
      <div className="flex-1 flex overflow-hidden">
        {/* Preview */}
        <div className="flex-1 flex items-center justify-center bg-black overflow-hidden">
          {selectedFrame && selectedFrame.ready && selectedFrame.image_url ? (
            <img
              key={selectedFrame.frame_id}
              src={assetUrl(selectedFrame.image_url)}
              alt={`Frame ${selectedFrame.frame_id}`}
              className="max-w-full max-h-full object-contain"
            />
          ) : selectedClip && selectedClip.ready && selectedClip.video_url ? (
            <video
              key={selectedClip.scene_id}
              ref={videoRef}
              src={assetUrl(selectedClip.video_url)}
              controls
              autoPlay
              loop
              className="max-w-full max-h-full"
            />
          ) : (
            <div className="flex flex-col items-center gap-3 text-gray-700">
              <svg className="w-16 h-16 opacity-20" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1}
                  d="M15 10l4.553-2.069A1 1 0 0121 8.82v6.36a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
              <p className="text-sm">
                {!selected ? "Select a frame or clip from the timeline" : "Not ready yet"}
              </p>
              <button
                onClick={loadData}
                className="text-xs text-accent hover:text-accent-light transition-colors"
              >
                Refresh
              </button>
            </div>
          )}
        </div>

        {/* Edit panel */}
        <aside className="w-72 flex-shrink-0 border-l border-border bg-surface overflow-y-auto">
          <EditPanel jobId={jobId} selected={selected} onRefresh={loadData} />
        </aside>
      </div>

      {/* Music modal */}
      {showMusicModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="bg-surface border border-border rounded-2xl p-6 w-full max-w-sm space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-white">Add Music</h3>
              <button onClick={() => setShowMusicModal(false)} className="text-gray-600 hover:text-white">✕</button>
            </div>
            <p className="text-sm text-gray-500">Upload an MP3 or WAV. It will be looped/trimmed to match the video length.</p>

            <input
              ref={musicInputRef}
              type="file"
              accept="audio/*"
              className="hidden"
              onChange={(e) => setMusicFile(e.target.files?.[0] ?? null)}
            />
            <button
              onClick={() => musicInputRef.current?.click()}
              className="w-full py-3 border border-dashed border-border rounded-xl text-sm text-gray-400
                hover:border-accent/60 hover:text-white transition-all"
            >
              {musicFile ? musicFile.name : "Click to select audio file"}
            </button>

            {statusMsg && (
              <p className={`text-xs ${statusMsg.startsWith("Error") ? "text-red-400" : "text-accent-light"}`}>
                {statusMsg}
              </p>
            )}

            <button
              onClick={handleMusicUpload}
              disabled={!musicFile || musicUploading}
              className="w-full py-2.5 rounded-xl bg-accent text-white text-sm font-medium
                hover:bg-accent-light disabled:opacity-40 disabled:cursor-not-allowed
                transition-colors flex items-center justify-center gap-2"
            >
              {musicUploading ? (
                <>
                  <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  Mixing...
                </>
              ) : "Mix Audio"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
