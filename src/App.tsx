import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArtifactView, ReadAloud } from "./Artifacts";
import { Connections } from "./Connections";
import { SetupScreen, usePreparation } from "./Setup";
import { Privacy } from "./Privacy";
import {
  ArrowDown,
  ArrowUp,
  AudioLines,
  Check,
  ChevronDown,
  Compass,
  Download,
  FileText,
  Globe2,
  Headphones,
  Menu,
  MessageCircle,
  Mic,
  Plus,
  Search,
  Settings2,
  Square,
  X,
} from "lucide-react";
import {
  api,
  downloadMessage,
  downloadUpload,
  externalLink,
  streamRun,
  type Source,
  type Artifact,
  type RunEvent,
} from "./api";

type Chat = { id: string; title: string };
type Message = { id: string; role: string; content: string; sources: Source[]; artifacts?: Artifact[] };
type Mode = "chat" | "research" | "image" | "audio" | "spreadsheet" | "presentation" | "document" | "tools";
type Upload = { id: string; name: string; kind: string };
type Config = {
  model: string;
  reasoning: "low" | "medium" | "high";
  whisper_model: "tiny" | "base" | "small";
  language: "es" | "en" | "auto";
};
type ModelStatus = {
  available: boolean;
  models: { name: string; size: number }[];
};
const initialConfig: Config = {
  model: "macbot-4b",
  reasoning: "medium",
  whisper_model: "base",
  language: "en",
};

function Mark({ small = false }: { small?: boolean }) {
  return (
    <span className={`mark ${small ? "small" : ""}`} aria-hidden="true">
      <span />
      <span />
      <span />
      <span />
    </span>
  );
}

export default function App() {
  const [chats, setChats] = useState<Chat[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<Mode>("chat");
  const [approval, setApproval] = useState<{ id: string; proposal: NonNullable<RunEvent["proposal"]> } | null>(null);
  const [recoverable, setRecoverable] = useState<string | null>(null);
  const [config, setConfig] = useState<Config>(initialConfig);
  const [settingsConfig, setSettingsConfig] = useState<Config>(initialConfig);
  const [models, setModels] = useState<ModelStatus>({
    available: false,
    models: [],
  });
  const [connected, setConnected] = useState(false);
  const [workspaceReady, setWorkspaceReady] = useState(false);
  const preparation = usePreparation(workspaceReady);
  const ready = (feature: string) => preparation.state?.capabilities[feature] === true;
  const [connecting, setConnecting] = useState(true);
  const [busy, setBusy] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [stage, setStage] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [settings, setSettings] = useState(false);
  const [sidebar, setSidebar] = useState(false);
  const [search, setSearch] = useState("");
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [saving, setSaving] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const audioInput = useRef<HTMLInputElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const audioStream = useRef<MediaStream | null>(null);
  const recordingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abort = useRef<AbortController | null>(null);
  const cancelRequested = useRef(false);
  const previousFocus = useRef<HTMLElement | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);

  async function refresh() {
    setConnecting(true);
    try {
      const [chatList, cfg, status] = await Promise.all([
        api<Chat[]>("/chats"),
        api<Config>("/settings"),
        api<ModelStatus>("/models"),
      ]);
      setChats(chatList);
      setConfig(cfg);
      setModels(status);
      setConnected(true);
    } finally {
      setConnecting(false);
    }
  }
  useEffect(() => {
    return () => {
      abort.current?.abort();
      audioStream.current?.getTracks().forEach((track) => track.stop());
      if (recordingTimer.current) clearTimeout(recordingTimer.current);
    };
  }, []);
  useEffect(() => {
    if (preparation.state?.capabilities.chat) {
      void api<ModelStatus>("/models").then(setModels).catch(failure => setError(failure.message));
    }
  }, [preparation.state?.capabilities.chat]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "instant", block: "end" });
  }, [messages, draft, stage]);
  useEffect(() => {
    if (settings) {
      setSettingsConfig(config);
      previousFocus.current = document.activeElement as HTMLElement;
      dialog.current?.showModal();
    } else {
      dialog.current?.close();
      previousFocus.current?.focus();
    }
  }, [settings]);

  async function selectChat(chat: Chat) {
    if (busy) return;
    try {
      const result = await api<{
        messages: Message[];
        jobs: { id: string; status: string }[];
      }>(`/chats/${chat.id}`);
      setActive(chat.id);
      setMessages(result.messages);
      setSources([]);
      setDraft("");
      setSidebar(false);
      setUploads([]);
      const last = result.jobs[0];
      setRecoverable(last && ["interrupted", "failed"].includes(last.status) ? last.id : null);
      setApproval(null);
      if (last?.status === "awaiting_approval") {
        const pending = await api<{ result: { proposal: NonNullable<RunEvent["proposal"]> } }>(`/runs/${last.id}`);
        setApproval({ id: last.id, proposal: pending.result.proposal });
      }
      setError(
        last?.status === "interrupted"
          ? "The last task was interrupted. Resume it or send a new message."
          : "",
      );
    } catch (err) {
      setError((err as Error).message);
    }
  }
  function newChat() {
    if (busy) return;
    setActive(null);
    setMessages([]);
    setUploads([]);
    setSources([]);
    setDraft("");
    setError("");
    setSidebar(false);
    setPrompt("");
    setApproval(null);
    setRecoverable(null);
    textarea.current?.focus();
  }
  async function send() {
    if (!ready(mode) || !modelInstalled || !prompt.trim() || busy || uploading || recording || transcribing)
      return;
    const text = prompt.trim();
    cancelRequested.current = false;
    setError("");
    setBusy(true);
    setDraft("");
    setSources([]);
    setApproval(null);
    setRecoverable(null);
    setStage("Connecting to MacBot");
    let chatId = active;
    try {
      if (!chatId) {
        const chat = await api<Chat>("/chats", { method: "POST" });
        chatId = chat.id;
        setActive(chat.id);
      }
      const result = await api<{ id: string }>("/runs", {
        method: "POST",
        body: JSON.stringify({
          chat_id: chatId,
          prompt: text,
          mode,
          model: config.model,
          reasoning: config.reasoning,
          uploads: uploads.map((upload) => upload.id),
        }),
      });
      setJobId(result.id);
      if (cancelRequested.current) {
        await api(`/runs/${result.id}/cancel`, { method: "POST" });
      }
      setPrompt("");
      setUploads([]);
      setMessages((previous) => [
        ...previous,
        { id: crypto.randomUUID(), role: "user", content: text, sources: [] },
      ]);
      const controller = new AbortController();
      abort.current = controller;
      await streamRun(
        result.id,
        (event) => {
          if (event.type === "token")
            setDraft((previous) => previous + (event.text ?? ""));
          if (event.type === "stage") setStage(event.label ?? "Working");
          if (event.type === "sources") setSources(event.sources ?? []);
          if (event.type === "reset") setDraft("");
          if (event.type === "approval" && event.proposal) setApproval({ id: result.id, proposal: event.proposal });
          if (event.type === "error") {
            setError(event.message ?? "The task could not be completed.");
            setPrompt(text);
          }
          if (event.type === "cancelled") setStage("Task stopped");
        },
        controller.signal,
      );
      const conversation = await api<{ messages: Message[] }>(
        `/chats/${chatId}`,
      );
      setMessages(conversation.messages);
      setDraft("");
      setChats(await api<Chat[]>("/chats"));
    } catch (err) {
      if ((err as Error).name !== "AbortError")
        setError((err as Error).message);
    } finally {
      setBusy(false);
      setJobId(null);
      setStage("");
    }
  }
  async function stop() {
    cancelRequested.current = true;
    if (jobId) {
      try {
        await api(`/runs/${jobId}/cancel`, { method: "POST" });
      } catch (err) {
        setError((err as Error).message);
      }
    }
  }
  async function attach(files: FileList | File[]) {
    if (!ready("attachments")) { setError("File analysis is still preparing. You can chat while its model downloads."); return; }
    if (busy || uploading || transcribing || recording) return;
    setUploading(true);
    setError("");
    try {
      const remaining = 5 - uploads.length;
      if (files.length > remaining)
        throw new Error("You can attach up to five files per message.");
      for (const file of Array.from(files)) {
        const body = new FormData();
        body.append("file", file);
        const upload = await api<Upload>("/uploads", { method: "POST", body });
        setUploads((previous) => [...previous, upload]);
      }
      setMode("chat");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setUploading(false);
    }
  }
  async function continueTask(identifier: string, approved?: boolean) {
    setBusy(true); setError(""); setDraft(""); setJobId(identifier); setApproval(null); setRecoverable(null);
    const controller = new AbortController(); abort.current = controller;
    try {
      await api(`/runs/${identifier}/${approved === undefined ? "resume" : "approve"}`, {
        method: "POST", ...(approved === undefined ? {} : { body: JSON.stringify({ approved }) }),
      });
      await streamRun(identifier, (event) => {
        if (event.type === "reset") setDraft("");
        if (event.type === "token") setDraft((value) => value + (event.text ?? ""));
        if (event.type === "stage") setStage(event.label ?? "Working");
        if (event.type === "sources") setSources(event.sources ?? []);
        if (event.type === "approval" && event.proposal) setApproval({ id: identifier, proposal: event.proposal });
        if (event.type === "error") setError(event.message ?? "The task could not be completed.");
      }, controller.signal);
      if (active) setMessages((await api<{ messages: Message[] }>(`/chats/${active}`)).messages);
      setChats(await api<Chat[]>("/chats"));
    } catch (error) { setError((error as Error).message); }
    finally { setBusy(false); setDraft(""); setJobId(null); setStage(""); }
  }
  async function transcribe(blob: Blob, name: string) {
    if (!ready("dictation")) { setError("Speech recognition is still preparing."); return; }
    setTranscribing(true);
    setError("");
    try {
      const body = new FormData();
      body.append("file", blob, name);
      const result = await api<{ text: string }>("/transcribe", {
        method: "POST",
        body,
      });
      setPrompt(
        (previous) => `${previous}${previous ? " " : ""}${result.text}`,
      );
      textarea.current?.focus();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setTranscribing(false);
    }
  }
  async function toggleRecording() {
    if (!ready("dictation")) return;
    if (recording) {
      recorder.current?.stop();
      return;
    }
    if (busy || transcribing) return;
    try {
      setError("");
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioStream.current = stream;
      const mimeType = [
        "audio/webm;codecs=opus",
        "audio/ogg;codecs=opus",
        "audio/mp4",
      ].find((type) => MediaRecorder.isTypeSupported(type));
      const next = new MediaRecorder(
        stream,
        mimeType ? { mimeType } : undefined,
      );
      const chunks: Blob[] = [];
      next.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      next.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        setRecording(false);
        if (recordingTimer.current) clearTimeout(recordingTimer.current);
        const extension = next.mimeType.includes("ogg")
          ? "ogg"
          : next.mimeType.includes("mp4")
            ? "mp4"
            : "webm";
        void transcribe(
          new Blob(chunks, { type: next.mimeType }),
          `dictado.${extension}`,
        );
      };
      recorder.current = next;
      next.start(1000);
      setRecording(true);
      recordingTimer.current = setTimeout(() => {
        if (next.state === "recording") next.stop();
      }, 120000);
    } catch (err) {
      setError(`Your microphone is unavailable: ${(err as Error).message}`);
    }
  }
  async function saveConfig() {
    setSaving(true);
    try {
      const saved = await api<Config>("/settings", {
        method: "PUT",
        body: JSON.stringify(settingsConfig),
      });
      setConfig(saved);
      setModels(await api<ModelStatus>("/models"));
      setSettings(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }
  const hasConversation = messages.length > 0 || busy;
  const modelInstalled = models.models.some(
    (model) => model.name === config.model,
  );

  if (!workspaceReady) return <SetupScreen onReady={() => { setWorkspaceReady(true); void refresh().catch(failure => setError(failure.message)); }} />;
  return (
    <div
      className="app-shell"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        if (event.dataTransfer.files.length)
          void attach(event.dataTransfer.files);
      }}
    >
      {sidebar && (
        <button
          className="sidebar-scrim"
          aria-label="Close navigation"
          onClick={() => setSidebar(false)}
        />
      )}
      <aside className={`sidebar ${sidebar ? "open" : ""}`}>
        <button
          className="icon-button sidebar-close"
          aria-label="Close sidebar"
          onClick={() => setSidebar(false)}
        >
          <X size={18} />
        </button>
        <button className="brand" onClick={newChat} disabled={busy}>
          <Mark small />
          <span>
            macbot<span className="brand-dot">.</span>
          </span>
        </button>
        <button className="new-chat" onClick={newChat} disabled={busy}>
          <Plus size={18} /> New conversation <span>↗</span>
        </button>
        <label className="search-box">
          <Search size={15} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search conversations"
            aria-label="Search conversations"
          />
        </label>
        <div className="history-heading">YOUR SPACE</div>
        <nav className="history" aria-label="Conversations">
          {chats
            .filter((chat) =>
              chat.title.toLowerCase().includes(search.toLowerCase()),
            )
            .map((chat) => (
              <button
                key={chat.id}
                className={active === chat.id ? "selected" : ""}
                disabled={busy}
                onClick={() => void selectChat(chat)}
              >
                <MessageCircle size={16} />
                <span>{chat.title}</span>
              </button>
            ))}
          {!chats.length && (
            <p className="empty-history">Your conversations will live here.</p>
          )}
          {!!chats.length &&
            !chats.some((chat) =>
              chat.title.toLowerCase().includes(search.toLowerCase()),
            ) && <p className="empty-history">No matches.</p>}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-note">
            <span className={connected ? "status-dot" : "status-dot offline"} />
            <span>
              {connecting
                ? "Starting MacBot…"
                : connected
                  ? "Your space stays local"
                  : "Workspace disconnected"}
              <small>Files and history on your device</small>
            </span>
          </div>
          <button className="settings-button" onClick={() => setSettings(true)}>
            <Settings2 size={17} /> Settings <span>⌘</span>
          </button>
        </div>
      </aside>
      <main className="workspace">
        <div className="atmosphere" aria-hidden="true">
          <div className="sun" />
          <div className="horizon" />
          <div className="grain" />
        </div>
        <header className="topbar">
          <div className="topbar-left">
            <button
              className="icon-button menu-toggle"
              aria-label="Open navigation"
              onClick={() => setSidebar(true)}
            >
              <Menu size={20} />
            </button>
            <span>A place for your ideas</span>
          </div>
          <button className="model-pill" onClick={() => setSettings(true)}>
            <span
              className={modelInstalled ? "status-dot" : "status-dot offline"}
            />
            {config.model}
            <ChevronDown size={14} />
          </button>
        </header>
        <div
          className={`conversation ${hasConversation ? "has-messages" : ""}`}
        >
          {!hasConversation ? (
            <section className="welcome">
              <div className="welcome-label">
                <Headphones size={14} /> TUNE INTO AN IDEA
              </div>
              <Mark />
              <h1>What's on your mind?</h1>
              <p>
                A thought, a file, a bigger question.
                <br />
                Let's give it shape.
              </p>
              <div className="suggestions">
                <button
                  disabled={!ready("research")}
                  onClick={() => {
                    setMode("research");
                    setPrompt("Research ");
                    textarea.current?.focus();
                  }}
                >
                  <Compass size={22} />
                  <span>
                    Go a little further<small>Follow a question deeper</small>
                  </span>
                  <span className="suggestion-arrow">↗</span>
                </button>
                <button disabled={!ready("attachments")} onClick={() => fileInput.current?.click()}>
                  <FileText size={22} />
                  <span>
                    Connect the pieces<small>Explore your files</small>
                  </span>
                  <span className="suggestion-arrow">↗</span>
                </button>
                <button
                  onClick={() => {
                    setMode("chat");
                    setPrompt("Help me develop this idea: ");
                    textarea.current?.focus();
                  }}
                >
                  <AudioLines size={22} />
                  <span>
                    Find your rhythm<small>Give an idea some shape</small>
                  </span>
                  <span className="suggestion-arrow">↗</span>
                </button>
              </div>
            </section>
          ) : (
            <div className="message-list">
              {messages.map((message) => (
                <article className={`message ${message.role}`} key={message.id}>
                  <div className="message-avatar">
                    {message.role === "assistant" ? <Mark small /> : "YOU"}
                  </div>
                  <div className="message-body">
                    <div className="message-label">
                      {message.role === "assistant" ? "MacBot" : "You"}
                    </div>
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        img: ({ alt }) => <span>{alt || "External image"} (open its link to view)</span>,
                        a: ({ href, children }) => (
                          <a
                            href={href}
                            onClick={(event) => {
                              event.preventDefault();
                              if (href) void externalLink(href);
                            }}
                          >
                            {children}
                          </a>
                        ),
                      }}
                    >
                      {message.content}
                    </ReactMarkdown>
                    {message.sources.length > 0 && (
                      <SourceList
                        sources={message.sources}
                        onError={setError}
                      />
                    )}
                    {message.role === "assistant" && (
                      <div className="message-actions">
                        <Download size={14} />
                        <span>Save as</span>
                        {["md", "pdf", "docx"].map((format) => (
                          <button
                            key={format}
                            onClick={() =>
                              void downloadMessage(message.id, format).catch(
                                (err) => setError((err as Error).message),
                              )
                            }
                          >
                            {format.toUpperCase()}
                          </button>
                        ))}
                      </div>
                    )}
                    {message.artifacts?.map((artifact) => <ArtifactView key={artifact.id} artifact={artifact} onError={setError} />)}
                    {message.role === "assistant" && <ReadAloud messageId={message.id} available={ready("read_aloud")} onError={setError} />}
                  </div>
                </article>
              ))}
              {busy && (
                <article className="message assistant">
                  <div className="message-avatar">
                    <Mark small />
                  </div>
                  <div className="message-body">
                    <div className="message-label">MacBot</div>
                    <div className="run-stage" role="status">
                      <span className="working-dot" />
                      {stage}
                    </div>
                    {draft && (
                      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
                        img: ({alt}) => <span>{alt || "External image"}</span>,
                        a: ({href, children}) => <a href={href} onClick={(event) => {
                          event.preventDefault(); if (href) void externalLink(href);
                        }}>{children}</a>,
                      }}>
                        {draft}
                      </ReactMarkdown>
                    )}
                    {sources.length > 0 && (
                      <SourceList sources={sources} onError={setError} />
                    )}
                  </div>
                </article>
              )}
              <div ref={bottom} />
            </div>
          )}
        </div>
        <section className="composer-area" aria-label="Write a message">
          {approval && <div className="approval-card" role="region" aria-label="Approve a tool call">
            <strong>{approval.proposal.server_name} / {approval.proposal.tool}</strong>
            <p>MacBot will send these arguments to your connected tool.</p>
            <pre>{JSON.stringify(approval.proposal.arguments, null, 2)}</pre>
            <div><button disabled={busy} onClick={() => void continueTask(approval.id, true)}>Approve this call</button>
              <button disabled={busy} onClick={() => void continueTask(approval.id, false)}>Decline</button></div>
          </div>}
          {recoverable && <div className="resume-card"><span>This task can continue from its saved progress.</span><button disabled={busy} onClick={() => void continueTask(recoverable)}>Resume task</button></div>}
          {error && (
            <div className="error-notice" role="alert">
              <span>{error}</span>
              <button
                className="icon-button"
                aria-label="Dismiss error"
                onClick={() => setError("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {!hasConversation && connected && !modelInstalled && preparation.state?.status === "ready" && (
            <div className="setup-note">
              {models.available
                ? "Choose a local model to get started."
                : "Connect a local model to start chatting."}
              <button onClick={() => setSettings(true)}>
                Set up <ArrowUp size={12} />
              </button>
            </div>
          )}
          <div className={`composer ${recording ? "recording" : ""}`}>
            {uploads.length > 0 && (
              <div className="attachments">
                {uploads.map((upload) => (
                  <span key={upload.id}>
                    <FileText size={14} />
                    {upload.name}
                    <button
                      aria-label={`Remove ${upload.name}`}
                      onClick={() =>
                        setUploads((previous) =>
                          previous.filter((item) => item.id !== upload.id),
                        )
                      }
                    >
                      <X size={13} />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <textarea
              ref={textarea}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault();
                  void send();
                }
              }}
              placeholder={
                recording
                  ? "Listening… press the microphone to finish"
                  : mode === "research"
                    ? "What would you like to research?"
                    : "Write something. It starts here."
              }
              rows={2}
              aria-label="Message MacBot"
              disabled={busy}
            />
            <div className="composer-tools">
              <div className="composer-left">
                <select className="creation-mode" aria-label="MacBot task" value={mode} disabled={busy} onChange={(event) => setMode(event.target.value as Mode)}>
                  {([['chat', 'Chat'], ['research', 'Deep Research'], ['image', 'Create image'], ['audio', 'Create audio'], ['spreadsheet', 'Create spreadsheet'], ['presentation', 'Create slides'], ['document', 'Create document'], ['tools', 'Connected tools']] as const).map(([value, label]) => <option key={value} value={value} disabled={!ready(value)}>{label}{ready(value) ? "" : " · Preparing"}</option>)}
                </select>
                <button
                  className="icon-button"
                  title={ready("attachments") ? "Attach files" : "File analysis is preparing"}
                  aria-label="Attach files"
                  disabled={busy || uploading || !ready("attachments")}
                  onClick={() => fileInput.current?.click()}
                >
                  <Plus size={21} />
                </button>
                {uploading && (
                  <span className="tool-status">Reading file…</span>
                )}
                {transcribing && (
                  <span className="tool-status" role="status">
                    Transcribing…
                  </span>
                )}
                {recording && (
                  <span className="tool-status" role="status">
                    Recording
                  </span>
                )}
              </div>
              <div className="composer-right">
                <button
                  className="icon-button audio-import"
                  aria-label="Transcribe an audio file"
                  title={ready("dictation") ? "Transcribe audio" : "Speech recognition is preparing"}
                  disabled={busy || transcribing || recording || !ready("dictation")}
                  onClick={() => audioInput.current?.click()}
                >
                  <AudioLines size={19} />
                </button>
                <button
                  className={`icon-button mic-button ${recording ? "active" : ""}`}
                  aria-label={recording ? "Finish dictation" : "Dictate a message"}
                  title={!ready("dictation") ? "Speech recognition is preparing" : recording ? "Finish dictation" : "Dictate a message"}
                  disabled={busy || transcribing || !ready("dictation")}
                  onClick={() => void toggleRecording()}
                >
                  {recording ? <Square size={16} /> : <Mic size={19} />}
                </button>
                <button
                  className="send-button"
                  aria-label={busy ? "Stop response" : "Send message"}
                  disabled={
                    !busy &&
                    (!prompt.trim() ||
                      uploading ||
                      recording ||
                      transcribing ||
                      !ready(mode) || !modelInstalled ||
                      !connected)
                  }
                  onClick={() => void (busy ? stop() : send())}
                >
                  {busy ? (
                    <Square size={16} fill="currentColor" />
                  ) : (
                    <ArrowUp size={21} />
                  )}
                </button>
              </div>
            </div>
          </div>
          <div className="composer-caption">
            <span>
              {mode === "research"
                ? "Web research · sources and citations · needs internet"
                : "Local models. Room for ideas."}
            </span>
            <span>MacBot can make mistakes. Check what matters.</span>
          </div>
        </section>
        <input
          ref={fileInput}
          type="file"
          multiple
          accept=".pdf,.docx,.xlsx,.pptx,.txt,.md,.csv,.json,.jsonl,.png,.jpg,.jpeg,.webp,.py,.ts,.js,.webm,.wav,.mp3,.m4a,.ogg,.flac,.mp4,.mov,.mkv,.aac"
          hidden
          onChange={(event) => {
            if (event.target.files) void attach(event.target.files);
            event.target.value = "";
          }}
        />
        <input
          ref={audioInput}
          type="file"
          accept=".webm,.wav,.mp3,.m4a,.ogg,.flac,.mp4"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void transcribe(file, file.name);
            event.target.value = "";
          }}
        />
      </main>
      <dialog
        ref={dialog}
        className="settings-dialog"
        aria-labelledby="settings-title"
        onCancel={() => setSettings(false)}
        onClick={(event) => {
          if (event.target === event.currentTarget) setSettings(false);
        }}
      >
        <div className="dialog-header">
          <div>
            <span className="eyebrow">YOUR WAY</span>
            <h2 id="settings-title">MacBot settings</h2>
          </div>
          <button
            className="icon-button"
            aria-label="Close settings"
            onClick={() => setSettings(false)}
          >
            <X size={21} />
          </button>
        </div>
        <div className="settings-section">
          {error && (
            <p className="error-notice" role="alert">
              {error}
            </p>
          )}
          <h3>Conversation model</h3>
          <p>
            Your models run on this device. No paid model APIs.
          </p>
          <p>MacBot 4B</p>
          <div className="connection-status">
            <span className={models.available ? "status-dot" : "status-dot offline"} />
            {models.available ? "Ready on this PC" : "Preparation needed"}
          </div>
        </div>
        <div className="settings-section">
          <h3>Thinking</h3>
          <label htmlFor="reasoning-level">Reasoning level</label>
          <select id="reasoning-level" value={settingsConfig.reasoning}
            onChange={(event) => setSettingsConfig({ ...settingsConfig,
              reasoning: event.target.value as Config["reasoning"] })}>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </div>
        <div className="settings-section">
          <h3>Speech to text</h3>
          <p>
            Dictation becomes an editable message. Audio attachments remain in your conversation.
          </p>
          <div className="settings-grid">
            <div>
              <label htmlFor="whisper">Whisper model</label>
              <select
                id="whisper"
                value={settingsConfig.whisper_model}
                onChange={(event) =>
                  setSettingsConfig({
                    ...settingsConfig,
                    whisper_model: event.target
                      .value as Config["whisper_model"],
                  })
                }
              >
                <option value="tiny">Tiny · lighter</option>
                <option value="base">Base · balanced</option>
                <option value="small">Small · more accurate</option>
              </select>
            </div>
            <div>
              <label htmlFor="language">Audio language</label>
              <select
                id="language"
                value={settingsConfig.language}
                onChange={(event) =>
                  setSettingsConfig({
                    ...settingsConfig,
                    language: event.target.value as Config["language"],
                  })
                }
              >
                <option value="es">Spanish</option>
                <option value="en">English</option>
                <option value="auto">Detect automatically</option>
              </select>
            </div>
          </div>
          <p className="model-guidance">
            Dictation records up to two minutes. Audio files support up to ten minutes and 20 MB. Your recordings are processed locally.
          </p>
        </div>
        {settings && <><Connections onError={setError} /><Privacy active={active} onError={setError} onChange={() => {setSettings(false); newChat(); void refresh();}} /></>}
        <div className="dialog-footer">
          <button
            className="text-button"
            onClick={() =>
              void refresh().catch((err) => setError((err as Error).message))
            }
          >
            Refresh connection
          </button>
          <button
            className="save-button"
            disabled={saving || !settingsConfig.model.trim()}
            onClick={() => void saveConfig()}
          >
            <Check size={16} />
            {saving ? "Saving…" : "Save settings"}
          </button>
        </div>
      </dialog>
    </div>
  );
}

function SourceList({
  sources,
  onError,
}: {
  sources: Source[];
  onError: (message: string) => void;
}) {
  const readCount = sources.filter((source) => source.status === "read").length;
  return (
    <details className="source-list">
      <summary>
        <Globe2 size={14} />
        {readCount} {readCount === 1 ? "source read" : "sources read"} ·{" "}
        {sources.length} {sources.length === 1 ? "found" : "found"}
        <ChevronDown size={14} />
      </summary>
      <div>
        {sources.map((source) => (
          <button
            key={source.id}
            onClick={() =>
              void (
                source.kind === "document"
                  ? downloadUpload(source)
                  : externalLink(source.url)
              ).catch((error) => onError((error as Error).message))
            }
          >
            <span className="source-number">{source.tag ?? source.id}</span>
            <span>
              {source.title}
              <small>
                {source.kind === "document"
                  ? "Local attachment"
                  : source.url.replace(/^https?:\/\//, "").split("/")[0]}{" "}
                ·{" "}
                {source.status === "read"
                  ? "Read"
                  : source.status === "pending"
                    ? "Pending"
                    : "Unavailable"}
              </small>
            </span>
            <ArrowDown size={13} />
          </button>
        ))}
      </div>
    </details>
  );
}
