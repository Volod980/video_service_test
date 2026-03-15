"use client";
import { useState } from "react";
import { aiEditClip, aiEditFrame, regenerateClip, regenerateFrame, assetUrl } from "@/lib/api";
import type { ClipInfo, FrameInfo } from "@/lib/api";

interface EditPanelProps {
  jobId: string;
  selected: { type: "frame"; data: FrameInfo } | { type: "clip"; data: ClipInfo } | null;
  onRefresh: () => void;
}

export default function EditPanel({ jobId, selected, onRefresh }: EditPanelProps) {
  const [prompt, setPrompt] = useState("");
  const [aiInput, setAiInput] = useState("");
  const [loading, setLoading] = useState<"regen" | "ai" | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  // Sync prompt when selection changes
  const currentPrompt = selected
    ? selected.type === "frame"
      ? selected.data.prompt
      : selected.data.motion_prompt
    : "";

  const handleRegen = async () => {
    if (!selected) return;
    setLoading("regen");
    setMessage(null);
    try {
      const customPrompt = prompt.trim() || undefined;
      if (selected.type === "frame") {
        await regenerateFrame(jobId, selected.data.frame_id, customPrompt);
      } else {
        await regenerateClip(jobId, selected.data.scene_id, customPrompt);
      }
      setMessage("Regeneration started — refresh in ~30 seconds.");
      setTimeout(onRefresh, 30_000);
    } catch (e: unknown) {
      setMessage(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(null);
    }
  };

  const handleAiEdit = async () => {
    if (!selected || !aiInput.trim()) return;
    setLoading("ai");
    setMessage(null);
    try {
      if (selected.type === "frame") {
        await aiEditFrame(jobId, selected.data.frame_id, aiInput.trim());
      } else {
        await aiEditClip(jobId, selected.data.scene_id, aiInput.trim());
      }
      setMessage("AI edit started — Claude is rewriting the prompt, then regenerating.");
      setAiInput("");
      setTimeout(onRefresh, 45_000);
    } catch (e: unknown) {
      setMessage(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(null);
    }
  };

  if (!selected) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-600 gap-2">
        <svg className="w-10 h-10 opacity-30" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1}
            d="M15 15l-2 5L9 9l11 4-5 2zm0 0l5 5" />
        </svg>
        <p className="text-sm">Select a frame or clip to edit</p>
      </div>
    );
  }

  const isFrame = selected.type === "frame";
  const id = isFrame ? `Frame ${selected.data.frame_id}` : `Clip ${selected.data.scene_id}`;

  return (
    <div className="flex flex-col h-full overflow-y-auto p-4 gap-4">
      {/* Header */}
      <div className="flex items-center gap-2">
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${isFrame ? "bg-blue-500/20 text-blue-400" : "bg-purple-500/20 text-purple-400"}`}>
          {isFrame ? "Frame" : "Clip"}
        </span>
        <span className="text-sm font-semibold text-white">{id}</span>
      </div>

      {/* Current prompt (editable) */}
      <div className="space-y-1.5">
        <label className="text-xs text-gray-500 uppercase tracking-wide">
          {isFrame ? "Image Prompt" : "Motion Prompt"}
        </label>
        <textarea
          rows={8}
          className="w-full bg-surface border border-border rounded-lg p-3 text-xs text-gray-300
            font-mono leading-relaxed resize-none focus:outline-none focus:border-accent/60
            placeholder-gray-700"
          placeholder={currentPrompt}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
        <p className="text-xs text-gray-700">Leave blank to use the existing prompt above.</p>
      </div>

      {/* Regenerate button */}
      <button
        onClick={handleRegen}
        disabled={loading !== null}
        className="w-full py-2 rounded-lg bg-surface border border-border text-sm text-gray-300
          hover:border-accent/60 hover:text-white disabled:opacity-40 disabled:cursor-not-allowed
          transition-all flex items-center justify-center gap-2"
      >
        {loading === "regen" ? (
          <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        ) : (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        )}
        Regenerate
      </button>

      {/* Divider */}
      <div className="border-t border-border" />

      {/* AI Edit */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 rounded-full bg-accent/20 flex items-center justify-center">
            <svg className="w-3 h-3 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </div>
          <span className="text-sm font-medium text-white">AI Edit</span>
        </div>
        <p className="text-xs text-gray-600">
          {isFrame
            ? "Describe what to change (background, angle, lighting). Product details stay locked."
            : "Describe camera or mood changes. Product identity stays locked."}
        </p>
        <textarea
          rows={3}
          placeholder={isFrame
            ? 'e.g. "change background to black marble, overhead lighting"'
            : 'e.g. "slower dolly, warmer golden hour lighting"'}
          value={aiInput}
          onChange={(e) => setAiInput(e.target.value)}
          className="w-full bg-surface border border-border rounded-lg p-3 text-sm text-gray-300
            resize-none focus:outline-none focus:border-accent/60 placeholder-gray-700"
        />
        <button
          onClick={handleAiEdit}
          disabled={loading !== null || !aiInput.trim()}
          className="w-full py-2 rounded-lg bg-accent text-white text-sm font-medium
            hover:bg-accent-light disabled:opacity-40 disabled:cursor-not-allowed
            transition-colors flex items-center justify-center gap-2"
        >
          {loading === "ai" ? (
            <>
              <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              Claude is editing...
            </>
          ) : (
            "Apply AI Edit"
          )}
        </button>
      </div>

      {/* Status message */}
      {message && (
        <p className={`text-xs p-3 rounded-lg ${message.startsWith("Error") ? "bg-red-500/10 text-red-400" : "bg-accent/10 text-accent-light"}`}>
          {message}
        </p>
      )}
    </div>
  );
}
