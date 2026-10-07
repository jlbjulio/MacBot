import { useEffect, useRef, useState } from "react";
import { Pause, Play, RotateCw } from "lucide-react";
import { api } from "./api";
import { invoke, isTauri } from "@tauri-apps/api/core";

export type Preparation = {
  status: string; model: string; completed: number; total: number; error: string;
  reused_bytes: number; failures: Record<string, string>; capabilities: Record<string, boolean>;
  models: { id: string; name: string; ready: boolean }[];
};
type Engines = { status: string; label?: string; completed: number; total: number; error: string };
const essentialModels = ["chat", "voice", "whisper", "embedding", "verifier", "image"];
const bytes = (value: number) => value < 1024 ** 3 ? `${(value / 1024 ** 2).toFixed(1)} MB` : `${(value / 1024 ** 3).toFixed(2)} GB`;

export function usePreparation(enabled: boolean) {
  const [state, setState] = useState<Preparation | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let started = false;
    let latestStatus = "";
    let timer: ReturnType<typeof setTimeout>;
    async function update() {
      try {
        const value = await api<Preparation>("/setup", { signal: AbortSignal.timeout(15000) });
        if (stopped) return;
        latestStatus = value.status;
        setState(value); setError("");
        if (value.status === "waiting" && !started) {
          started = true;
          await api("/setup", { method: "POST", signal: AbortSignal.timeout(15000) });
        }
      } catch (failure) { if (!stopped) setError((failure as Error).message); }
      finally { if (!stopped) timer = setTimeout(() => void update(), latestStatus === "ready" ? 15000 : 2000); }
    }
    void update();
    return () => { stopped = true; clearTimeout(timer); };
  }, [enabled]);
  async function action(path: string) {
    try { setError(""); await api(path, { method: "POST", signal: AbortSignal.timeout(15000) }); }
    catch (failure) { setError((failure as Error).message); }
  }
  return { state, error, pause: () => action("/setup/pause"), resume: () => action("/setup") };
}

export function SetupScreen({ onReady }: { onReady: () => void }) {
  const [state, setState] = useState<Engines>({ status: "starting", completed: 0, total: 0, error: "" });
  const [error, setError] = useState("");
  const ready = useRef(false);
  const [pending, setPending] = useState(false);
  const enginesReady = state.status === "ready";
  const preparation = usePreparation(enginesReady);
  const models = preparation.state;
  useEffect(() => {
    if (enginesReady && !preparation.error && models?.status === "ready" && essentialModels.every(id => models.models.some(model => model.id === id && model.ready)) && !ready.current) {
      ready.current = true; onReady();
    }
  }, [enginesReady, models, preparation.error, onReady]);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function update() {
      try {
        const value = isTauri() ? await invoke<Engines>("bootstrap_status") : { status: "ready", completed: 0, total: 0, error: "" };
        if (stopped) return;
        setState(value); setError("");
      } catch (failure) { if (!stopped) setError((failure as Error).message); }
      finally { if (!stopped) timer = setTimeout(() => void update(), 1000); }
    }
    void update();
    return () => { stopped = true; clearTimeout(timer); };
  }, []);
  async function action(command: string) {
    try { setError(""); await invoke(command); }
    catch (failure) { setError((failure as Error).message); }
  }
  const current = enginesReady ? models : state;
  const status = current?.status || "waiting";
  const percent = current?.total ? Math.min(100, Math.round(current.completed / current.total * 100)) : 0;
  const failure = error || preparation.error || current?.error;
  const active = ["downloading", "verifying", "installing"].includes(status);
  const recoverable = ["paused", "failed", "waiting"].includes(status);
  const label = status === "paused" ? "Progress saved" : failure || status === "failed" ? "Download incomplete" : status === "downloading" ? "Downloading" : status === "installing" ? "Setting up" : "Checking files";
  const heading = status === "paused" ? "Paused" : failure || status === "failed" ? "Couldn't finish" : "Getting ready";
  const completed = models ? essentialModels.filter(id => models.models.some(model => model.id === id && model.ready)).length : 0;
  async function toggleDownload() {
    setPending(true);
    try {
      if (enginesReady) await (active ? preparation.pause() : preparation.resume());
      else await action(active ? "bootstrap_pause" : "bootstrap_resume");
    } finally { setPending(false); }
  }
  return <main className="setup-screen">
    <div className="setup-panel">
      <span className="setup-wordmark">MacBot</span>
      <div className="setup-record" aria-hidden="true"><span /></div>
      <h1>{heading}</h1>
      <div className="setup-progress">
        <div className="setup-progress-label"><span role="status">{label}</span><span>{current?.total ? `${percent}%` : ""}</span></div>
        <progress value={current?.total ? percent : undefined} max={100} aria-label="Current preparation progress" />
        <div className="setup-progress-meta">
          <span>{current?.total ? `${bytes(current.completed)} / ${bytes(current.total)}` : ""}</span>
          {models && <span>{completed} of {essentialModels.length} complete</span>}
        </div>
      </div>
      {failure && <div className="setup-error">
        <p role="alert">Check your connection and free space, then try again.</p>
        <details><summary>Details</summary><p>{failure}</p></details>
      </div>}
      <div className="setup-actions">{(active || recoverable || failure) && <button disabled={pending} onClick={() => void toggleDownload()}>
        {active ? <Pause size={16} aria-hidden="true" /> : status === "paused" ? <Play size={16} aria-hidden="true" /> : <RotateCw size={16} aria-hidden="true" />}
        {pending ? active ? "Pausing…" : "Resuming…" : active ? "Pause download" : status === "paused" ? "Resume download" : failure || status === "failed" ? "Try again" : "Start downloads"}
      </button>}</div>
    </div>
  </main>;
}
