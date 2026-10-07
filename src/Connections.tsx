import { useEffect, useState } from "react";
import { api, externalLink } from "./api";

type Server = { id: string; name: string; transport: string; command: string; args: string[]; env: Record<string, string>; url: string; headers: Record<string, string>; enabled: boolean; trusted: boolean; cwd: string };
type Capability = { models: { id: string; repo: string; ready: boolean }[] };

export function Connections({ onError }: { onError: (message: string) => void }) {
  const [servers, setServers] = useState<Server[]>([]);
  const [capability, setCapability] = useState<Capability | null>(null);
  const [text, setText] = useState("");
  const [working, setWorking] = useState(false);
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [transport, setTransport] = useState("http");
  const [address, setAddress] = useState("");
  const [argumentsText, setArgumentsText] = useState("[]");
  const [credentials, setCredentials] = useState("{}");
  const [trusted, setTrusted] = useState(false);
  const [cwd, setCwd] = useState("");
  async function refresh() {
    const [connections, features] = await Promise.all([api<Server[]>("/mcp/servers"), api<Capability>("/capabilities")]);
    setServers(connections); setCapability(features);
  }
  useEffect(() => { void refresh().catch((error: Error) => onError(error.message)); }, []);
  async function action(run: () => Promise<unknown>) {
    setWorking(true); setStatus("");
    try { await run(); await refresh(); }
    catch (error) { onError((error as Error).message); }
    finally { setWorking(false); }
  }
  async function importConnection() {
    const value = JSON.parse(text) as { mcpServers?: Record<string, Partial<Server>> } & Partial<Server>;
    if (value.mcpServers) {
      for (const [name, config] of Object.entries(value.mcpServers)) {
        await api("/mcp/servers", { method: "POST", body: JSON.stringify({ ...config, name, transport: config.url ? "http" : "stdio", enabled: false, trusted: false }) });
      }
    } else await api("/mcp/servers", { method: "POST", body: JSON.stringify({ ...value, transport: value.url ? "http" : "stdio", enabled: false, trusted: false }) });
    setText(""); setStatus("Connection saved. Inspect its tools, then enable it when you are ready.");
  }
  async function saveConnection() {
    const values = JSON.parse(credentials) as Record<string, string>;
    const args = JSON.parse(argumentsText) as string[];
    const body = {name, transport, command: transport === "stdio" ? address : "", url: transport === "http" ? address : "",
      args, headers: transport === "http" ? values : {}, env: transport === "stdio" ? values : {}, enabled: false, trusted, cwd};
    await api(editing ? `/mcp/servers/${editing}` : "/mcp/servers", {method: editing ? "PUT" : "POST", body: JSON.stringify(body)});
    setEditing(null); setName(""); setAddress(""); setCredentials("{}"); setArgumentsText("[]"); setTrusted(false); setCwd("");
    setStatus("Connection saved. Inspect its tools, then enable it.");
  }
  return <>
    <div className="settings-section">
      <h3>Your creative toolkit</h3>
      <p>Search text and media with EmbeddingGemma 2. Create images locally and record an English voice with Piper.</p>
      <div className="capability-list">{capability ? capability.models.map((model) => <div key={model.id}>
        <span>{model.id === "embedding" ? "Multimodal search" : model.id === "image" ? "Image creation" : model.id === "voice" ? "Voice creation" : model.id === "whisper" ? "Speech recognition" : "Evidence review"}</span>
        <small>{model.ready ? "Ready on this device" : "Downloads when needed"}</small>
      </div>) : <p role="status">Checking your models…</p>}</div>
      <p>Audio attachments stay in the conversation. Dictation turns speech into an editable message. Video analysis uses sampled frames.</p>
    </div>
    <div className="settings-section">
      <h3>Connected tools</h3>
      <p>Add an MCP connection to work with your own tools. MacBot shows the proposed action and asks you to approve every call.</p>
      <button onClick={() => void externalLink("https://github.com/mcp").catch((failure: Error) => onError(failure.message))}>Browse public MCP servers ↗</button>
      {servers.map((server) => <div className="connection-card" key={server.id}>
        <strong>{server.name}</strong><small>{server.enabled ? "Enabled" : "Disabled"}</small>
        <div className="connection-actions">
          <button disabled={working || server.transport !== "workspace" && !server.trusted} onClick={() => void action(async () => {
            const result = await api<{ tools: { name: string }[] }>(`/mcp/servers/${server.id}/inspect`, { method: "POST" });
            setStatus(result.tools.length ? `Available tools: ${result.tools.map((tool) => tool.name).join(", ")}` : "This connection has no tools.");
          })}>Inspect tools</button>
          <button disabled={working || !server.enabled && server.transport !== "workspace" && !server.trusted} onClick={() => void action(() => api(`/mcp/servers/${server.id}`, { method: "PUT", body: JSON.stringify({ ...server, enabled: !server.enabled }) }))}>{server.enabled ? "Disable" : "Enable"}</button>
          {server.id !== "workspace" && <button disabled={working} onClick={() => { setEditing(server.id); setName(server.name); setTransport(server.transport); setAddress(server.transport === "http" ? server.url : server.command); setArgumentsText(JSON.stringify(server.args)); setCredentials(JSON.stringify(server.transport === "http" ? server.headers : server.env)); setTrusted(!!server.trusted); setCwd(server.cwd || ""); }}>Edit</button>}
          {server.id !== "workspace" && <button disabled={working} onClick={() => void action(() => api(`/mcp/servers/${server.id}`, { method: "DELETE" }))}>Remove</button>}
        </div>
        {server.transport !== "workspace" && <label className="connection-trust"><input type="checkbox" checked={!!server.trusted} disabled={working} onChange={event => void action(() => api(`/mcp/servers/${server.id}`, {method:"PUT", body:JSON.stringify({...server, trusted:event.target.checked, enabled: event.target.checked && server.enabled})}))} /> I trust this server and allow MacBot to connect.</label>}
      </div>)}
      <p className="model-guidance">The built-in connection stays inside your MacBot workspace. An external local server runs with your Windows permissions, including during tool inspection. A remote server receives the arguments you approve. Trust its publisher before connecting.</p>
      <div className="connection-fields">
        <h4>{editing ? "Edit connection" : "Add your connection"}</h4>
        <label htmlFor="mcp-name">Connection name</label><input id="mcp-name" value={name} onChange={event => setName(event.target.value)} placeholder="My creative tools" />
        <label htmlFor="mcp-kind">Connection type</label><select id="mcp-kind" value={transport} onChange={event => {setTransport(event.target.value); setAddress(""); setCredentials("{}");}}><option value="http">Remote or local HTTP service</option><option value="stdio">Local program or public npm package</option></select>
        <label htmlFor="mcp-address">{transport === "http" ? "MCP endpoint URL (Streamable HTTP)" : "Program command"}</label><input id="mcp-address" value={address} onChange={event => setAddress(event.target.value)} placeholder={transport === "http" ? "https://example.com/mcp" : "npx, python, or a full path to your server"} />
        {transport === "stdio" && <><label htmlFor="mcp-args">Arguments (JSON list)</label><input id="mcp-args" value={argumentsText} onChange={event => setArgumentsText(event.target.value)} placeholder={'["-y", "your-package@1.0.0"]'} /><label htmlFor="mcp-cwd">Working folder (optional)</label><input id="mcp-cwd" value={cwd} onChange={event => setCwd(event.target.value)} /></>}
        <label htmlFor="mcp-secrets">{transport === "http" ? "Headers (JSON; optional credentials)" : "Environment variables (JSON; optional credentials)"}</label><input id="mcp-secrets" type="password" autoComplete="off" value={credentials} onChange={event => setCredentials(event.target.value)} />
        <small>Credentials are encrypted for your Windows profile. Re-enter them after moving to a different user or computer.</small>
        <label className="connection-trust"><input type="checkbox" checked={trusted} onChange={event => setTrusted(event.target.checked)} /> I trust this server’s publisher and permissions.</label>
        <button disabled={working || !name.trim() || !address.trim()} onClick={() => void action(saveConnection)}>{editing ? "Save changes" : "Save connection"}</button>
        {editing && <button onClick={() => {setEditing(null); setName(""); setAddress(""); setCredentials("{}");}}>Cancel edit</button>}
      </div>
      <details><summary>Paste an existing MCP configuration</summary>
      <label htmlFor="mcp-config">MCP connection JSON</label>
      <textarea id="mcp-config" rows={5} value={text} onChange={(event) => setText(event.target.value)} placeholder={'{"mcpServers":{"My tools":{"command":"path/to/server.exe","args":[]}}}'} />
      <button className="save-button" disabled={working || !text.trim()} onClick={() => void action(importConnection)}>{working ? "Connecting…" : "Add connection"}</button>
      </details>
      {status && <p className="connection-result" role="status">{status}</p>}
    </div>
  </>;
}
