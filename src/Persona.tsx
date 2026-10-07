import { useEffect, useState } from "react";
import { api, request, saveBlob } from "./api";
type Status = { enabled: boolean; status: { state: string; error?: string }; evaluation: { passed: boolean; steps: number; examples: number; seconds: number } | null };
export function Persona({ onError }: { onError: (message: string) => void }) {
  const [value, setValue] = useState<Status | null>(null);
  const [examples, setExamples] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    const refresh = () => void api<Status>("/persona").then((result) => { if (active) setValue(result); }).catch((error: Error) => { if (active) onError(error.message); });
    refresh(); const timer = setInterval(refresh, 10000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  async function action(run: () => Promise<unknown>) {
    setBusy(true);
    try { await run(); setValue(await api<Status>("/persona")); }
    catch (error) { onError((error as Error).message); }
    finally { setBusy(false); }
  }
  const training = value?.status.state === "running";
  return <div className="settings-section">
    <h3>Your creative voice</h3>
    <p>MacBot has an original, relaxed personality inspired by Mac Miller. It is an AI assistant. Its English voice is Piper's LJSpeech voice.</p>
    <p>Train a small style adapter for short everyday replies. Your main model still handles reasoning and files. Research reports and generated documents keep their factual tone.</p>
    <label className="checkbox-setting"><input type="checkbox" checked={value?.enabled ?? false} disabled={busy || training || !value?.evaluation?.passed} onChange={(event) => void action(() => api("/persona", { method: "PUT", body: JSON.stringify({ enabled: event.target.checked }) }))} /> Use my trained style adapter</label>
    {value?.evaluation && <p>{value.evaluation.examples} examples · {value.evaluation.steps} training steps · {value.evaluation.passed ? "Preservation checks passed" : "Preservation checks failed; adapter stays disabled"}. Tone needs human review.</p>}
    <label htmlFor="persona-examples">Your original examples (optional JSONL)</label>
    <textarea id="persona-examples" rows={3} value={examples} onChange={(event) => setExamples(event.target.value)} placeholder={'{"input":"Your draft","output":"Your preferred wording"}'} />
    <p>Leave this empty to use MacBot's original examples. Training runs on CPU and can take several minutes. It waits for active chat tasks to finish.</p>
    <div className="connection-actions">
      <button disabled={busy || training} onClick={() => void action(() => api("/persona/train", { method: "POST", body: JSON.stringify({ examples: examples.trim() ? examples.trim().split("\n").map((line) => JSON.parse(line)) : null }) }))}>{training ? "Training…" : "Train style adapter"}</button>
      <button disabled={busy || training || !value?.evaluation} onClick={() => void action(async () => saveBlob(await (await request("/persona/export")).blob(), "MacBot-persona-adapter.zip"))}>Export adapter</button>
    </div>
    <p role="status">{value?.status.error || (training ? "Training on this device. You can keep this panel open for updates." : "")}</p>
  </div>;
}
