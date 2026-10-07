import { invoke, isTauri } from "@tauri-apps/api/core";

type Connection = { baseUrl: string; token?: string };
let connection: Promise<Connection> | undefined;
function connect() {
  connection ??= isTauri()
    ? invoke<Connection>("connection").catch((error) => {
        connection = undefined;
        throw error;
      })
    : Promise.resolve({ baseUrl: "/api" });
  return connection;
}
export async function request(path: string, options: RequestInit = {}) {
  const { baseUrl, token } = await connect();
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(baseUrl + path, { ...options, headers });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      detail?: unknown;
    };
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : `Error ${response.status}`,
    );
  }
  return response;
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  return (await request(path, options)).json() as Promise<T>;
}
export type Source = {
  id: number;
  url: string;
  title: string;
  status: string;
  error?: string;
  kind?: string;
  tag?: string;
  filename?: string;
  document_id?: string;
};
export type RunEvent = {
  type: string;
  text?: string;
  label?: string;
  agent?: string;
  sources?: Source[];
  message?: string;
  proposal?: { server_name: string; tool: string; arguments: Record<string, unknown> };
};

export type Artifact = { id: string; kind: string; name: string; metadata?: { width?: number; height?: number; format?: string; upscaled?: boolean } };
export async function artifactBlob(id: string) {
  return (await request(`/artifacts/${id}`)).blob();
}
export async function saveArtifact(artifact: Artifact) {
  saveBlob(await artifactBlob(artifact.id), artifact.name);
}
export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
export async function streamRun(
  id: string,
  onEvent: (event: RunEvent) => void,
  signal: AbortSignal,
) {
  const response = await request(`/runs/${id}/events`, { signal });
  if (!response.body) throw new Error("The response stream could not be opened.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const line = block.split("\n").find((line) => line.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)) as RunEvent);
    }
    if (done) break;
  }
}
export async function externalLink(url: string) {
  if (!/^https?:\/\//i.test(url)) return;
  if (isTauri()) {
    const { openUrl } = await import("@tauri-apps/plugin-opener");
    await openUrl(url);
  } else window.open(url, "_blank", "noopener,noreferrer");
}
export async function downloadMessage(id: string, format: string) {
  const blob = await (
    await request(`/messages/${id}/export/${format}`, { method: "POST" })
  ).blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `MacBot.${format}`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

export async function downloadUpload(source: Source) {
  if (!source.document_id) return;
  const blob = await (await request(`/uploads/${source.document_id}`)).blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = source.filename ?? "document";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
