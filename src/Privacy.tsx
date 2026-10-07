import { useEffect, useState } from "react";
import { api } from "./api";

type State = {directory: string; conversations: number; attachments: number};
export function Privacy({ active, onChange, onError }: {active: string | null; onChange: () => void; onError: (message: string) => void}) {
  const [state, setState] = useState<State | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [working, setWorking] = useState(false);
  useEffect(() => {void api<State>("/privacy").then(setState).catch(failure => onError(failure.message));}, []);
  async function remove(all: boolean) {
    if (!all && !window.confirm("Delete this conversation, its attachments and generated files? Shared files and your tool workspace are kept.")) return;
    setWorking(true);
    try {
      await api(all ? "/privacy/erase" : `/chats/${active}`, {method: all ? "POST" : "DELETE", ...(all ? {body: JSON.stringify({confirmation})} : {})});
      onChange();
    } catch (failure) {onError((failure as Error).message);} finally {setWorking(false);}
  }
  return <section className="settings-section privacy-section">
    <h3>Your data & privacy</h3>
    <p>Your conversations, attachments, recordings and generated files stay in your Data folder. MacBot has no analytics or automatic uploads. This folder is not encrypted; anyone with access to it can read your files.</p>
    <p>Web research sends search queries to a search provider. External MCP services receive the arguments you approve. Model hosts receive download requests; they do not receive your conversations.</p>
    {state && <><small>{state.conversations} conversations · {state.attachments} attachments</small><code className="data-location">{state.directory}</code><button onClick={() => void navigator.clipboard.writeText(state.directory).catch(failure => onError(failure.message))}>Copy folder location</button></>}
    {active && <button disabled={working} onClick={() => void remove(false)}>Delete current conversation</button>}
    <details><summary>Erase your personal workspace</summary><p>This removes conversations, uploads, generated files, workspace files and connections. Downloaded public models stay available. Runtime logs and backups may retain filenames; this does not guarantee secure disk erasure.</p>
      <label htmlFor="erase-confirmation">Type ERASE MY WORKSPACE</label><input id="erase-confirmation" value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="off" />
      <button className="danger-action" disabled={working || confirmation !== "ERASE MY WORKSPACE"} onClick={() => void remove(true)}>{working ? "Erasing…" : "Erase workspace"}</button>
    </details>
  </section>;
}
