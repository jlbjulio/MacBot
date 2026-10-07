# MacBot

A local workspace for chat, files and creative projects. Built for Windows PCs (x64), with no paid model API or required account.

[Download portable](https://github.com/jlbjulio/MacBot/releases/latest) · [Privacy](PRIVACY.md)

![MacBot](assets/macbot-logo.svg)

## Get started

1. Download the portable ZIP from Releases and extract it.
2. Open **MacBot.exe**. Keep the **MacBot** support folder beside it.
3. Let first-time preparation finish. Downloads support pause, resume and retry, and reuse compatible cached files.

You need Windows x64, [WebView2](https://learn.microsoft.com/en-us/microsoft-edge/webview2/), internet for preparation and around **16 GB of free space**. Local chat and creation work offline afterward; web research and remote tools need internet. The executable is unsigned.

## What you can do

- Chat with attached documents, images, audio and video.
- Research questions on the web with sources and supporting excerpts.
- Generate images, spoken audio, Word/PDF documents, spreadsheets and slides.
- Dictate messages, transcribe audio and read replies aloud.
- Connect local or remote MCP tools in **Settings** and approve their actions.

Choose a task beside the message box. Describe the content, style and format you want—for example, “Create a purple and cream proposal as Word and PDF.”

Files support **20 MB each**, up to **five per message**. Audio supports ten minutes and video five minutes. Video uses sampled frames. Large image exports are resized from a render of up to 512 pixels; generated speech is English. Review generated content before using it.

## Your workspace

Your history, attachments, models and generated files stay in **MacBot/Data**. Back up that folder, or copy it into a fresh portable when updating. **Settings → Your data & privacy** provides deletion controls.

MacBot has no analytics or automatic conversation uploads. Local history is not encrypted. Research sends searches online; connected tools receive approved arguments. Only trust MCP servers you recognize: local servers run with your Windows permissions. Connections support local commands and Streamable HTTP; browser OAuth and legacy SSE are not included.

The default chat model is **Qwen3.5 4B**. Larger models and an optional style adapter are available in Settings. MacBot's relaxed, creative tone takes inspiration from Mac Miller; it uses an original synthetic voice.

## Built with

| Area | Technologies |
| --- | --- |
| Desktop | Tauri, Rust, React, TypeScript, Vite and Windows WebView2 |
| Backend | Python, FastAPI, Uvicorn, LangGraph and SQLite |
| Local AI | Ollama, Qwen3.5, EmbeddingGemma 2, Qdrant, PyTorch, Transformers and Sentence Transformers |
| Image & voice | Tiny-SD/Diffusers, Piper, faster-whisper and RapidOCR |
| Files | python-docx, ReportLab, openpyxl and python-pptx |
| Tools & preparation | MCP, Node.js and uv; optional style training with PEFT/LoRA |

## Build the portable

Use Windows x64 with Node.js 22+, Python 3.11, uv 0.12.21, Rust/MSVC, and Visual Studio 2022 Build Tools with C++ and the Windows SDK.

```powershell
npm ci
uv sync --locked --directory backend --extra dev --python 3.11
npm run build:desktop
backend/.venv/Scripts/python.exe scripts/package-portable.py
```

The ZIP is created in **release/**. Upload it as the single download in a GitHub Release. Models, personal data and build output are excluded from the source repository.
