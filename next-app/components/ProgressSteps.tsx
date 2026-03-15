"use client";

interface Step {
  label: string;
  sublabel: string;
}

const STEPS: Step[] = [
  { label: "Master Plan", sublabel: "Claude analyzes product" },
  { label: "Keyframes", sublabel: "Nano Banana 2 generates frames" },
  { label: "Video Clips", sublabel: "Kling generates motion" },
  { label: "Assembly", sublabel: "ffmpeg concatenates clips" },
];

interface Props {
  currentStep: number; // 0 = not started, 1–4 = in progress, 5 = done
  status: "processing" | "completed" | "failed";
}

export default function ProgressSteps({ currentStep, status }: Props) {
  return (
    <div className="space-y-2">
      {STEPS.map((step, i) => {
        const stepNum = i + 1;
        const isDone = status === "completed" || currentStep > stepNum;
        const isActive = currentStep === stepNum && status === "processing";
        const isFailed = status === "failed" && currentStep === stepNum;
        const isPending = currentStep < stepNum;

        return (
          <div
            key={i}
            className={`
              flex items-center gap-3 p-3 rounded-lg border transition-all duration-300
              ${isDone ? "border-accent/40 bg-accent/5" : ""}
              ${isActive ? "border-accent bg-accent/10" : ""}
              ${isFailed ? "border-red-500/40 bg-red-500/5" : ""}
              ${isPending ? "border-border bg-transparent opacity-40" : ""}
            `}
          >
            {/* Icon */}
            <div className={`
              w-7 h-7 rounded-full flex-shrink-0 flex items-center justify-center text-sm font-bold
              ${isDone ? "bg-accent text-white" : ""}
              ${isActive ? "bg-accent/20 text-accent" : ""}
              ${isFailed ? "bg-red-500/20 text-red-400" : ""}
              ${isPending ? "bg-surface text-gray-600" : ""}
            `}>
              {isDone ? (
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                </svg>
              ) : isActive ? (
                <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
              ) : isFailed ? (
                "✕"
              ) : (
                stepNum
              )}
            </div>

            {/* Labels */}
            <div className="flex-1 min-w-0">
              <p className={`text-sm font-medium ${isActive ? "text-white" : isDone ? "text-gray-300" : "text-gray-600"}`}>
                {step.label}
              </p>
              <p className="text-xs text-gray-600">{step.sublabel}</p>
            </div>

            {/* Active indicator */}
            {isActive && (
              <div className="flex gap-1">
                {[0, 1, 2].map((d) => (
                  <div
                    key={d}
                    className="w-1 h-1 rounded-full bg-accent animate-bounce"
                    style={{ animationDelay: `${d * 0.15}s` }}
                  />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
