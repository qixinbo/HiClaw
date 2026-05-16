# Changelog (Unreleased)

Record image-affecting changes to `manager/`, `worker/`, `copaw/`, `hermes/`, `openclaw-base/`, `hiclaw-controller/` here before the next release.

---

- fix(manager): agent docs and jq examples use `roomID` for `hiclaw get workers` / `hiclaw create worker` JSON (CLI field name), not `room_id`
- fix(controller): add `+kubebuilder:subresource:status` on CR types; patch Worker finalizers instead of full `Update`; exponential backoff on REST update conflict retries
- fix(manager): document runtime-aware Worker dispatch (avoid @worker text in admin DM only); update task-management references, AGENTS.md, HEARTBEAT.md, channel-management skill
- fix(manager): separate runtime-specific AGENTS/HEARTBEAT for OpenClaw vs CoPaw; remove cross-runtime references from manager agent docs
- refactor(api)!: restructure `spec.mcpServers` on Worker/Manager/Team CRDs to `[]{name,url,transport}`; drop controller-side MCP gateway authorization; `mcporter-servers.json` is written from the CRD (see `docs/declarative-resource-management.md`)
- fix(copaw): extract Matrix `human_id` from sender, propagate it through MatrixChannel payload/request metadata, and keep replies routed by `room_id`
- feat(copaw): route per-human sessions and ReMe working directories under `users/<human-id>/`, so `memory.md` and session state no longer share the workspace root
- feat(copaw): isolate auto memory summary queues, memory search, and per-user ReMe index startup by `human_id`, so background memory jobs do not mix users in the same workspace
- feat(copaw): repair mirrored `users/` workspaces on worker startup and keep `.copaw/workspaces/default/users/**` in the normal push cycle, so user memory/session state survives sync and restart recovery
