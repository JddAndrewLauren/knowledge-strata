# Claude Desktop as Host: Bundles, Projects, File Writes, Skills, Sub-agents, Limits

Research findings on whether the strata design (built for Claude Code as host) survives
a move to the Claude Desktop app for a non-technical third party.

**Researched:** 2026-09-08
**Sources:** support.claude.com, claude.com/docs, code.claude.com/docs, modelcontextprotocol.io,
github.com/modelcontextprotocol/mcpb (formerly anthropics/dxt), anthropic.com/engineering,
anthropics/claude-code issue tracker (for behaviour the docs do not cover). Each claim carries
its URL; facts are true as of the page/issue date given. "Not documented" means no official
page found that states it either way.

## 1. Install and update

### Name, spec, repo
- The format is **MCPB (MCP Bundle)**, file extension `.mcpb`. It was renamed from DXT
  (Desktop Extensions); the CLI moved from `dxt` to `mcpb` and the npm package from
  `@anthropic-ai/dxt` to `@anthropic-ai/mcpb`. Existing `.dxt` files "will continue to work".
  https://github.com/modelcontextprotocol/mcpb (README, rename notice)
  https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop
- The repo moved to the MCP org: https://github.com/modelcontextprotocol/mcpb (the old
  anthropics/dxt URL still serves the same MANIFEST.md).
- MANIFEST.md header: "Current version: 0.3, Last updated: 2025-12-02". The same file
  describes a `uv` server type as "v0.4+", so the header lags the content.
  https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md
- Anthropic's own guide calls MCPB "the secondary distribution path" and says remote MCP
  servers are recommended for directory listing; MCPB is positioned "for internal use,
  private distribution, or as a foundation for submission".
  https://claude.com/docs/connectors/building/mcpb

### What the bundle requires
| Item | Fact | Source |
|------|------|--------|
| Container | A zip containing `manifest.json` plus the server | mcpb README |
| Required manifest fields | `manifest_version`, `name`, `version`, `description`, `author` (object with `name`), `server` | MANIFEST.md |
| `server.type` | `node`, `python`, `binary`, and `uv` (v0.4+) | MANIFEST.md |
| Node runtime | "Ships with Claude Desktop on macOS and Windows, so users need no separate runtime"; Node is "strongly recommended" | claude.com/docs/connectors/building/mcpb |
| Python deps | "All dependencies must be bundled in the MCPB. Can use `server/lib` for packages or `server/venv` for full virtual environment." Claude Desktop does NOT ship a Python runtime; the `uv` type is described as "Host application manages Python and dependencies automatically... Works cross-platform without user Python installation" | MANIFEST.md; mcpb README |
| Platforms | `compatibility.platforms`: `darwin`, `win32`, `linux`; `compatibility.claude_desktop`: semver constraint | MANIFEST.md |
| Variables | `${__dirname}` (bundle dir), `${HOME}`, `${user_config.KEY}` | MANIFEST.md |

Sources: https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md ;
https://github.com/modelcontextprotocol/mcpb/blob/main/README.md ;
https://claude.com/docs/connectors/building/mcpb

### user_config
- Types: `string`, `number`, `boolean`, `directory`, `file`. Properties: `title`,
  `description`, `required`, `default`, `multiple`, `sensitive`, `min`/`max`.
  "directory" renders a native directory picker; `default` may use `${HOME}`.
  https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md
- `sensitive: true` values are "encrypted using the operating system's secure storage"
  (Keychain on macOS, Credential Manager on Windows).
  https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop
- Values reach the server by substitution into `mcp_config` either as `env` ("ideal for
  sensitive data") or as `args` ("work well for paths"); `multiple: true` arrays expand
  to one argument each. https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md
- "Define a user_config section in manifest.json and Claude Desktop automatically generates
  a settings UI for your extension." https://claude.com/docs/connectors/building/mcpb

### Install path for the end user
- Three routes: double-click the `.mcpb`, drag-and-drop onto the Claude window, or
  Settings > Extensions > Advanced settings > Install Extension. All open an install UI where
  the user "reviews extension details and permissions, configures required settings, grants
  permissions, and completes installation. Installation is per-user."
  https://claude.com/docs/connectors/building/mcpb
- Sideloaded bundles are logged as "Installing unsigned extension" and labelled unsigned
  (local) in Settings. https://dev.classmethod.jp/en/articles/mcpb-claude-desktop/ (secondary)
- A June 2026 report: on Claude Desktop 1.12603.1.0 (Windows MSIX) the Install Extension
  window "closes and nothing happens" for a valid unsigned `.mcpb`, while "Install Unpacked
  Extension" works; open, no maintainer reply. https://github.com/modelcontextprotocol/mcpb/issues/281
- A separate report of silent install failures on macOS Tahoe 26.5:
  https://github.com/anthropics/claude-code/issues/68484

### Update path for privately distributed bundles
- "Extensions from the official directory update automatically by default. For privately
  distributed extensions, users will need to install updated .mcpb files manually." Updating
  works by "incrementing the version field in manifest.json while leaving the name value
  unchanged". https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop
- No auto-update for sideloaded bundles is documented anywhere.

### Signing / notarization
- `mcpb sign` exists (`--cert`, `--key`, `--intermediate`, `--self-signed`); `mcpb verify`
  and `mcpb info` report signature status and warn on self-signed. The CLI docs call signing
  "(Optional)". https://github.com/modelcontextprotocol/mcpb/blob/main/CLI.md
- No official page states that Claude Desktop refuses unsigned bundles; the support article
  and MANIFEST.md are silent on signing as an install requirement. macOS vs Windows
  differences in signature handling: not documented.
- A bundled *binary* server on macOS is subject to the OS's own Gatekeeper/TCC rules (a
  third-party bundle reports code-signing its Swift binary for stable TCC grants); this is
  an OS matter, not an MCPB one. https://github.com/MichaelAdamGroberman/mac-mcp (secondary)

### Org/admin controls
- Managed keys (MDM plist `com.anthropic.claudefordesktop` on macOS; `HKLM\SOFTWARE\Policies\Claude`
  or `HKCU\...` on Windows): `isDesktopExtensionEnabled` (bool, default true, "Enable/disable
  extensions") and `isDesktopExtensionDirectoryEnabled` (bool, default true).
  https://support.claude.com/en/articles/12622667-enterprise-configuration-for-claude-desktop
- Team/Enterprise in-app allowlist (Organization settings > Connectors, Desktop >= 0.13.91):
  once enabled, "Users will no longer be able to install new desktop extensions that are not
  included within the allowlist", existing installs "will be force-deleted", and users "can
  no longer drag or click to install MCPBs".
  https://support.claude.com/en/articles/12592343-enabling-and-using-the-desktop-extension-allowlist
- The 2025 engineering post lists "Blocklist specific extensions or publishers" and "Deploy
  private extension directories" as enterprise capabilities.
  https://www.anthropic.com/engineering/desktop-extensions
- No key that blocks *unsigned* bundles specifically is documented. Individual Pro/Max users
  have none of these controls applied.

### Raw claude_desktop_config.json and developer toggles
- Still supported. The current MCP quickstart (2026-07-28 docs) walks through Claude menu >
  Settings > Developer > Edit Config, file at `~/Library/Application Support/Claude/claude_desktop_config.json`
  (macOS) / `%APPDATA%\Claude\claude_desktop_config.json` (Windows), restart required.
  https://modelcontextprotocol.io/docs/develop/connect-local-servers
- Claude Code's Desktop docs confirm: "The Desktop app loads MCP servers from
  claude_desktop_config.json into local Code tab sessions... available in both the Desktop
  chat surface and local Code tab sessions." https://code.claude.com/docs/en/desktop
- There is an "Extension Developer" section under Settings > Extensions > Advanced settings
  holding "Install Extension..." (and, per issue #281, "Install Unpacked Extension"). No
  documented toggle named "allow unsigned/dev extensions"; sideloading is simply on by
  default for unmanaged installs. https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop
- The managed key `isLocalDevMcpEnabled` exists to switch off local dev MCP servers.
  https://support.claude.com/en/articles/14479288-claude-cowork-architecture-overview

**Confidence: High** on format/name/manifest/user_config/manual-update/config.json (all
first-party). **Medium** on signing (documented as optional; absence of a block is inferred
from silence plus field reports). **Low** on install reliability on the newest OS builds
(two open bug reports, no official statement).

## 2. Project selection

- Extension/server configuration is **per user, app-wide**. Installation is "per-user; each
  user installs separately". Nothing in the MCPB or Desktop docs ties an extension to a
  Claude Project. https://claude.com/docs/connectors/building/mcpb
- Per-conversation toggle exists: "+" button > Connectors shows configured connectors "with
  toggles allowing you to enable/disable them per conversation"; the "Search and tools" menu
  can disable individual tools for the current conversation.
  https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities
- Tool loading modes: "Tool access" under "+" > Connectors offers Auto (default) and
  On demand; "If you have 10 or more connectors active, consider switching to On demand".
  https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities
- Per-Project pinning of connectors/tools: **not documented**; an open feature request
  (Feb 2026) states users "cannot specify which claude.ai connectors are relevant for a
  given project". https://github.com/anthropics/claude-code/issues/25566
- Installing the same extension twice with different `user_config`: **not documented**. The
  manifest `name` is the identity used for updates ("leaving the name value unchanged"), which
  implies one install per name; two copies would need two distinct `name` values, i.e. two
  differently named bundles. https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop
- Tool namespacing (from a detailed bug report, Desktop 1.3109.0, April 2026): stdio servers
  are namespaced by their config key, remote connectors by connector name, MCP `serverInfo.name`
  is ignored, and tools surface as `{config_key}:{tool_name}`. When a local key equals a
  remote connector name AND tool names overlap, "tools/call never returns" (silent hang).
  Closed as invalid/stale, no fix documented. https://github.com/anthropics/claude-code/issues/50319
- Consequence for strata: the "working directory selects the project" mechanism has no
  Desktop analogue. Desktop has no cwd; a project must be chosen by `user_config` at install
  (one bundle per project, distinct names) or by a tool parameter.

**Confidence: High** that config is global/per-user with per-conversation toggles. **Medium**
on namespacing (single detailed report, consistent with the Code-tab precedence docs).
**Low/not documented** on duplicate installs and Project pinning.

## 3. File writes

### Built-in write capability
- **Chat tab:** no built-in local file tools. The chat surface writes files only through an
  MCP server the user has installed (e.g. the Filesystem server) or by producing artifacts /
  downloadable files. https://modelcontextprotocol.io/docs/develop/connect-local-servers
- **Cowork tab (Pro, Max, Team, Enterprise; macOS and Windows):** "Claude reads and writes
  local files without requiring manual uploads or downloads." Users "attach one or more
  workspace folders to a session; the agent can then read, create, and modify files anywhere
  inside those folders, and run code against them inside the sandbox VM." "Uses the same
  agentic architecture that powers Claude Code, with no terminal required."
  https://claude.com/docs/cowork/overview ; https://claude.com/docs/third-party/claude-desktop/local-access ;
  https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork
- Cowork permission modes: Manual ("pauses and asks for approval for actions"), Auto
  (read-only actions run; writes/deletes reviewed), Skip ("nothing checks its actions").
  https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork
- Cowork folder access is Desktop-only; web/mobile Cowork "reaches these files only while the
  desktop app is open". https://support.claude.com/en/articles/15520349-use-claude-cowork-on-web-desktop-and-mobile
- Local Cowork architecture: "The agent loop runs natively on the device... Code execution
  runs in an isolated virtual machine (VM)" (Virtualization.framework on macOS, Hyper-V on
  Windows); "each local tool call is checked against the member's permissions before it runs".
  https://support.claude.com/en/articles/14479288-claude-cowork-architecture-overview
- Cowork does use connectors: "Claude can use your connectors"; managed key
  `isDesktopExtensionEnabled` is listed among Cowork controls, so desktop extensions reach
  Cowork sessions. https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork ;
  https://support.claude.com/en/articles/14479288-claude-cowork-architecture-overview
- **Code tab:** full Claude Code (Read/Write/Edit/Bash) with a GUI; on Windows requires Git
  for Windows first. https://code.claude.com/docs/en/desktop
- Computer use exists in Desktop (Code tab and Cowork) but is a screen-driving capability,
  not a file API. https://code.claude.com/docs/en/desktop

### Filesystem MCP extension route (for the Chat tab)
- Official quickstart: edit `claude_desktop_config.json`, add `npx -y
  @modelcontextprotocol/server-filesystem <dir> <dir>`, requires a separately installed
  Node.js (the quickstart route uses system `npx`, not Desktop's bundled Node), restart the
  app. Allowed directories are the trailing `args`. Steps: open settings, Developer tab, Edit
  Config, paste JSON with real paths, save, quit and restart = 5-6 steps plus a Node install.
  https://modelcontextprotocol.io/docs/develop/connect-local-servers
- Anthropic's directory lists a Filesystem extension installable from Settings > Extensions >
  Browse extensions (folder scope configured in the extension's settings UI); step count is
  not documented beyond "click on any Anthropic-reviewed tools you want to use".
  https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop
- Per-call prompts: "Before executing any file system operation, Claude will request your
  approval." The prompt offers "Allow once" and "Allow always" ("only click 'Allow always'
  when using a server and tool that you trust to run unsupervised").
  https://modelcontextprotocol.io/docs/develop/connect-local-servers ;
  https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp

### Downloads/artifacts as a workaround
- Chat can produce files (docx, xlsx, pptx, pdf) via code execution/file creation, delivered
  as downloads; this writes to the browser/app download location, not to an arbitrary path.
  Cowork writes "directly to your file system". No official page describes chat artifacts
  landing in a user-chosen folder. https://claude.com/docs/plugins/overview ;
  https://support.claude.com/en/articles/12512180-use-skills-in-claude

**Confidence: High** on Cowork and Code-tab write capability and on the Chat tab lacking
built-in file tools. **Medium** on exact step counts for the Filesystem extension (directory
install flow not itemised officially).

## 4. Skills

- Agent Skills (SKILL.md) are supported in claude.ai and Desktop: "Skills are available for
  users on Free, Pro, Max, Team, and Enterprise plans." Enable under Settings > Capabilities
  (requires code execution on), manage under Customize > Skills; upload "your skill folder as
  a ZIP file". https://support.claude.com/en/articles/12512180-use-skills-in-claude
- Packaging: "The ZIP should contain the skill folder as its root"; frontmatter `name`
  (<= 64 chars) and `description` (<= 200 chars) required. Skills are per user; org-wide
  provisioning is Team/Enterprise only via Organization settings > Skills.
  https://support.claude.com/en/articles/12512198-how-to-create-custom-skills ;
  https://support.claude.com/en/articles/13119606-provision-and-manage-skills-for-your-organization
- Scope: skills apply across all chats once enabled; Claude auto-invokes them ("You don't need
  to explicitly invoke them"). Per-Project scoping: not documented. "Skill sharing works in
  both chat and Cowork." https://support.claude.com/en/articles/12512180-use-skills-in-claude
- Cowork/claude.ai skills and Claude Code skills are separate stores: Cowork "doesn't read the
  Claude Code CLI's ~/.claude directory on your machine. To use a skill or plugin that exists
  only in ~/.claude, add it in Customize." https://claude.com/docs/cowork/overview ;
  https://code.claude.com/docs/en/skills (section "Skills in Cowork and cloud sessions")
- Skill inside an MCPB: **not possible per spec**. MANIFEST.md has no skills field ("The
  manifest only covers tools, prompts, and resources"). https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md
- Skill + server in one package: a **plugin** can bundle skills, connectors ("MCP servers"),
  sub-agents and hooks. Plugins may include "local MCP servers that run on your computer with
  the same permissions as any other program you run." Plugins work in "chat on the web, the
  Chat tab in Claude Desktop, and Claude Cowork"; "Hooks and sub-agents run only in Cowork".
  Install via Customize > Plugins > Browse, "upload a custom plugin file", or "Add
  marketplace" with a GitHub/git URL (public GitLab/Bitbucket also work). Paid plans only.
  https://support.claude.com/en/articles/13837440-use-plugins-in-claude ;
  https://claude.com/docs/cowork/guide/plugins
- Note the claude.com Cowork guide says "Plugins are available in Cowork and Code. They aren't
  used in Chat", while the support article says chat can use them with hooks/sub-agents greyed
  out. The two first-party pages disagree on Chat; treat Chat plugin support as partial.
- Marketplace: Anthropic's official catalog is the default; a plugin can be submitted
  (https://claude.com/docs/plugins/submit). Plugin limits: 200 MB uncompressed, 5,000 files.
  https://claude.com/docs/cowork/guide/plugins
- Sharing to a non-technical user: a skill zip can be emailed and uploaded (Customize > Skills
  > upload); a plugin can be uploaded from a file or pulled from a git URL. Both are per-user
  installs.

**Confidence: High** on skill support, packaging, per-user scope, and separate stores.
**Medium** on plugin behaviour in the Chat tab (conflicting first-party pages).

## 5. Subagents / fan-out

| Mechanism | Available in Desktop? | Cheap-model control? | Source |
|-----------|----------------------|----------------------|--------|
| Chat tab sub-agents | No host-side delegation documented | n/a | support articles are silent |
| Cowork sub-agents | Yes: "Complex work gets divided into smaller tasks with parallel workstreams" | Not documented; one bug report says Cowork forces all sub-agents to Haiku 4.5 | claude.com/docs/cowork/overview ; issue 47488 |
| Code tab (Claude Code) | Yes, same engine as CLI; tasks pane shows subagents; reads `~/.claude/settings.json` etc. | Yes, the CLI agent `model` field | code.claude.com/docs/en/desktop |
| MCP sampling | Claude Desktop: not supported | Spec has modelPreferences hints | clients table; spec |
| MCP tasks / async | Not documented for Desktop | n/a | - |

- Cowork sub-agents: the docs confirm parallel sub-agents and that plugins may ship "Agents:
  Specialized subagents Claude can delegate to". No doc states which model runs them or that a
  user can choose. https://claude.com/docs/cowork/overview ; https://claude.com/docs/cowork/guide/plugins
- Bug report (Windows, April 2026, closed "not planned"): in Cowork "every sub-agent call is
  silently overridden to run as claude-haiku-4-5-20251001, regardless of the model parameter...
  regardless of model: fields in agent frontmatter files"; root cause described as
  `CLAUDE_CODE_SUBAGENT_MODEL` hardcoded in the Electron host. The report also says Cowork read
  `~/.claude/agents/*.md`, which contradicts the later docs statement that Cowork does not read
  `~/.claude`. https://github.com/anthropics/claude-code/issues/47488
- Code tab: "Desktop runs the same underlying engine"; "Settings in ~/.claude.json and
  ~/.claude/settings.json are shared"; the tasks pane shows "subagents, background shell
  commands". Model is chosen "from the dropdown next to the send button". So Claude Code agent
  definitions with a `model` field work in the Code tab as in the CLI.
  https://code.claude.com/docs/en/desktop
- MCP sampling in Desktop: the MCP clients table marks Claude Desktop App with no Sampling
  support (Resources, Prompts, Tools yes; Roots added March 2026). The March 2026 update PR
  added Elicitation to Claude Code, Roots to Claude Desktop, Apps to claude.ai, and nothing
  about sampling for any Anthropic client. Only `fast-agent` is listed with full sampling.
  https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2398 ;
  https://github.com/modelcontextprotocol/docs/blob/main/clients.mdx (older snapshot)
- Spec: `sampling/createMessage` carries `modelPreferences` with `hints[].name` (substring
  match, "advisory - clients make final model selection"), `costPriority`, `speedPriority`,
  `intelligencePriority`. https://modelcontextprotocol.io/specification/latest/client/sampling
- Spec status: **Sampling is deprecated as of protocol 2026-07-28** (SEP-2577). "New
  implementations SHOULD NOT adopt it; existing implementations SHOULD migrate to integrating
  directly with LLM provider APIs." Earliest removal: first revision on/after 2027-07-28.
  Roots and Logging are deprecated in the same revision.
  https://modelcontextprotocol.io/specification/2026-07-28/deprecated
- The Claude Code tracker has an open request for sampling to reuse Max subscriptions and
  avoid API cost: https://github.com/anthropics/claude-code/issues/1785

### Fallback: server calls the Anthropic API itself
- Requires the user to hold an Anthropic API key (Console account, separate billing from the
  Claude subscription); the key can be collected via a `sensitive: true` user_config field and
  stored in the OS keychain. https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md
- List prices (claude-api skill cache, 2026-06-24): Haiku 4.5 $1.00 in / $5.00 out per MTok,
  200K context; Sonnet 5 $2.00 / $10.00, 1M context; Opus 5 $5.00 / $25.00. Message Batches
  run asynchronously at 50% cost. A 226k-token month read once by Haiku costs about $0.23 in
  input tokens at list price. https://platform.claude.com/docs/en/about-claude/pricing
- UX consequence: the user pays twice (subscription plus metered API) and must create/paste an
  API key during install; no first-party doc describes a way for a local server to bill against
  the user's Claude subscription.

**Confidence: High** that Desktop chat has no sub-agent facility and that sampling is both
unsupported in Desktop and deprecated in the spec. **Medium** on Cowork sub-agent model
(docs silent; one closed bug report). **High** on the Code tab inheriting CLI agent definitions.

## 6. Other things a Claude Code-shaped design trips over

| Topic | Desktop fact | Source |
|-------|--------------|--------|
| Tool-result size cap | Claude Code: ~25K-token cap on MCP tool results, overridable via `MAX_MCP_OUTPUT_TOKENS`. Desktop Chat: not documented. Cowork/Code tab run the Claude Code engine, so the same cap is plausible but unconfirmed | https://github.com/anthropics/claude-code/issues/45770 ; https://github.com/anthropics/claude-code/issues/2638 |
| Tool/server count | No hard number documented. Guidance: "If you have 10 or more connectors active, consider switching to On demand". A bug report says "Load tools when needed" did not defer schemas and the context limit was exceeded at ~11 connectors before the first message | https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities ; https://github.com/anthropics/claude-code/issues/62175 |
| MCP resources in UI | Supported; attached manually via the "+" menu as attachments (no browsing UI beyond that) | clients table; https://frontendmasters.com/courses/mcp/add-resource-to-claude-desktop/ (secondary) |
| MCP prompts in UI | Supported; chosen from the "+" menu, arguments prompted on insert | same |
| Roots | Claude Desktop listed as supporting Roots since March 2026; which directories it sends (Cowork workspace folders?) is not documented. Roots is deprecated in spec 2026-07-28 with migration "Pass directories or files via tool parameters, resource URIs, or server configuration" | https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2398 ; https://modelcontextprotocol.io/specification/2026-07-28/deprecated |
| Elicitation | Claude Code yes (since 2.1.76); Claude Desktop no. Feature request closed as out of scope for the Claude Code tracker (March 2026). Spec 2026-07-28 introduces MRTR (`InputRequiredResult`) as the successor pattern | https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2398 ; https://github.com/anthropics/claude-code/issues/41110 ; https://modelcontextprotocol.io/specification/latest/client/sampling |
| Per-tool permission prompts | "Claude wants to use [tool]" with Allow once / Always allow. Cowork: "Always allow" did not persist across sessions (Feb 2026, closed not planned). Connector tool permissions "reset to default after every Claude Desktop upgrade" (report). Team/Enterprise admins can "turn off persistent 'always allow'" | https://github.com/anthropics/claude-code/issues/24433 ; https://github.com/anthropics/claude-code/issues/56954 ; https://support.claude.com/en/articles/14479288-claude-cowork-architecture-overview |
| Model selection | Chat: model picker per conversation (plan-dependent). Code tab: dropdown next to send, changeable mid-session. Cowork: not documented | https://code.claude.com/docs/en/desktop |
| Context window (chat, all paid plans) | Fable 5.1 / Opus 5 / Sonnet 5: 1M; Opus 4.8/4.7/4.6 and Sonnet 4.6: 500K; others 200K. Cowork: Fable 5.1/5, Opus 5, Sonnet 5, Opus 4.8/4.7: 1M; Sonnet 4.6, Opus 4.6, Haiku 4.5: 200K. "Claude automatically manages your conversation context" (summarises) on paid plans with code execution on | https://support.claude.com/en/articles/8606394-how-large-is-the-context-window-on-paid-claude-plans |
| Claude Code from Desktop | Yes: the Code tab is "Claude Code through a graphical interface instead of the terminal" for Pro, Max, Team, Enterprise; Windows needs Git for Windows; shares `~/.claude.json`, `~/.claude/settings.json`, `.mcp.json`, CLAUDE.md, hooks, skills with the CLI; also loads `claude_desktop_config.json` servers | https://code.claude.com/docs/en/desktop |
| Config precedence in Code tab | Same server name in `claude_desktop_config.json` and `~/.claude.json`/`.mcp.json`: Desktop uses the `claude_desktop_config.json` definition. User-scope `~/.claude.json` beats `.mcp.json` in Desktop (departs from CLI order) | https://code.claude.com/docs/en/desktop |
| Windows/WSL | Cowork and Desktop do not run inside WSL; WSL paths (`\\wsl$`) cannot be attached as workspace folders; map to a drive letter or copy | https://claude.com/docs/third-party/claude-desktop/local-access |

For a non-technical user the Code tab changes the picture in one direction only: it makes the
full Claude Code toolset (Write/Edit, agents with `model`, `~/.claude/skills`, `.mcp.json`)
available without a terminal, but it is a software-development surface with permission modes,
diffs and a file editor, and on Windows it needs Git installed first.
https://code.claude.com/docs/en/desktop

**Confidence: High** on context windows, Code-tab config sharing, elicitation/roots status,
resources/prompts surfacing. **Medium** on permission-prompt persistence (bug reports, closed).
**Low/not documented** on Desktop-chat tool-result caps and hard tool-count limits.

## What this means for the design assumptions

Stated as facts against design.md's Claude Code-shaped assumptions:

**Confirmed (works on Desktop as designed)**
- A local stdio server can be packaged as a double-click `.mcpb` with a directory picker and
  keychain-backed secret fields; Node servers need no runtime install (sec. 1).
- MCP tools, resources, and prompts are all surfaced in Desktop chat (sec. 6).
- In the Code tab, everything designed for Claude Code (cwd, Write/Edit, `~/.claude/agents`
  with `model`, `~/.claude/skills`, `.mcp.json`) carries over unchanged (sec. 5, 6).

**Broken (does not hold on Desktop chat or Cowork)**
- "Working directory selects the project": Desktop has no cwd; extensions are per-user and
  app-wide, toggled per conversation, not per Project (sec. 2).
- "No project parameter on tools": with one install per user and no cwd, project identity must
  come from `user_config` (one bundle per project, distinct `name`) or from a tool argument (sec. 2).
- "Agent writes notes/manuscript with the host's Write/Edit": the Chat tab has no file tools;
  writes require Cowork (attached folder, permission modes) or a Filesystem MCP server with
  per-call approval prompts (sec. 3).
- "Skill ships with the server": MCPB cannot carry a SKILL.md; a skill is a separate zip upload
  under Customize > Skills, or the two ship together as a plugin (sec. 4).
- "Haiku reader sub-agent via host agent definitions": Desktop chat has no sub-agents; Cowork
  has sub-agents but no documented model control; MCP sampling is unsupported in Desktop and
  deprecated in the spec; the only server-side route is a user-supplied API key with separate
  billing (sec. 5).
- "Sideloaded bundle updates": no auto-update outside the directory; the user re-installs each
  new `.mcpb` by hand (sec. 1).

**Undocumented (no official statement either way)**
- Whether Desktop chat truncates large tool results and at what size (sec. 6).
- Whether two installs of one bundle with different `user_config` can coexist (sec. 2).
- Which model Cowork sub-agents run on and whether it can be chosen (sec. 5).
- Whether Claude Desktop refuses unsigned bundles on any platform, and whether Desktop passes
  Cowork workspace folders as MCP roots (sec. 1, 6).
- Hard limits on enabled tools or servers per conversation (sec. 6).
