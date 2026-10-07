# Your data in MacBot

MacBot is a local Windows application. No account, analytics service or automatic conversation upload is configured. Its local AI engines process conversations and attachments on your device.

## What stays on your device

The portable MacBot/Data folder contains history, attachments, generated files, transcripts, search vectors, task checkpoints, tool-call records and settings. Ordinary history and files are not encrypted. Anyone who can read this folder can access them. Protect it with your Windows account and device encryption, and include it in your own backup policy.

The portable browser profile also stays inside MacBot/Data/cache/webview. Private API responses use Cache-Control: no-store. Workspace erasure keeps the running browser profile; close the application before removing that cache folder manually.

Values in MCP environment-variable and authentication-header fields use Windows DPAPI protection. The application can decrypt them while running under your Windows account. They must be entered again on another Windows account. Keep secrets in those fields: URLs, commands and ordinary arguments are stored as normal settings. Do not share the Data folder or an archive of your personal workspace.

## When the network is used

- First-run setup prepares Python through uv/Astral, Node from nodejs.org, pinned Python wheels from PyPI and PyTorch, then public models from Hugging Face. The workspace opens only after all six essential model groups are ready. Downloads can be paused and resumed. These services receive network requests and your IP address, without conversation contents. Compatible local model caches are checked by pinned version and checksum before copying files into Data; Hugging Face file metadata may still be requested even when cached weights are reused. Existing model caches and system installations are left intact. A compatible existing uv package cache can be reused and populated with public package downloads; installed libraries are copied into the private runtime rather than linked to that cache. If no existing package cache is found, MacBot uses MacBot/Data/cache/uv.
- Deep Research sends generated searches to the configured search provider and downloads public pages. Searches may contain details from your question. Run local chat instead when those details must stay offline.
- External MCP services receive the exact tool arguments you approve. Trusting and inspecting a connection can also contact its provider. A local MCP program can make its own network requests and runs with your Windows user's permissions.
- Opening a link contacts the selected website through your browser. Markdown remote images are not fetched automatically.

Provider privacy policies apply to those network requests. MacBot does not promise that third-party providers retain no data.

## Delete or move your data

Settings shows the data folder. Deleting a conversation removes its records, generated files, orphaned attachments, related search vectors and task checkpoints. Files shared with another conversation and the tool workspace are kept.

Erasing the personal workspace requires typing **ERASE MY WORKSPACE**. It removes conversations, attachments, generated files, checkpoints, tool records, connections and workspace files. Public model downloads and basic settings are kept. Stop active work first.

These operations remove application data; they do not guarantee secure disk erasure. Runtime logs may retain filenames or errors, and backups, SSD behaviour and third-party services may preserve copies. Files created outside MacBot by a trusted external tool are outside its deletion scope.

To move MacBot, close it and copy the entire portable folder. Re-enter protected MCP credentials on a different Windows account. To share the app, use the clean release ZIP, which excludes all Data contents.
