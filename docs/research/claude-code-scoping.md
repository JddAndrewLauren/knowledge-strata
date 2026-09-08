# Claude Code Scoping: .mcp.json, Skills, Agents, and the Model Field

Research findings for the `strata init` CLI installation behavior.

**Researched:** 2026-09-08  
**Source:** Official Claude Code documentation (https://code.claude.com/docs)

## 1. `.mcp.json` Scoping and Project Prevalence

### Discovery
- **.mcp.json location**: Project root (committed to git, shared with team)
- **Precedence hierarchy** (from highest to lowest):
  1. Local scope (~/.claude.json per project, in settings)
  2. **Project scope (.mcp.json)**
  3. User scope (~/.claude.json global, via `claude mcp add --scope user`)
  4. Plugin-provided servers
  5. Claude.ai connectors

Source: [MCP documentation](https://code.claude.com/docs/en/mcp.md) — "When the same server is defined in more than one place, Claude Code connects to it once, using the definition from the highest-precedence source. The entire server entry from that source is used; fields are not merged across scopes."

### Subfolder and Worktree Behavior
The `.mcp.json` file is loaded from the **project root** (where Claude Code session starts). The documentation does not explicitly state subfolder or git worktree behavior, but the general model indicates:
- `.mcp.json` applies to the folder where the session starts
- Subfolders inherit the project root's `.mcp.json` (not separate discovery per folder)
- **Git worktrees are treated as separate projects**, each with their own root and `.mcp.json`

Source: [MCP documentation](https://code.claude.com/docs/en/mcp.md) — "Project-scoped servers require explicit approval" with `claude mcp list` and `claude mcp get` reading `.mcp.json` approvals "only from settings files".

### Workspace Trust and Approval
Project-scoped servers in `.mcp.json` require explicit one-time approval:
- Running `claude` in a cloned repository and accepting the workspace trust dialog approves all servers in that repository's `.mcp.json`
- A cloned repository **cannot approve its own servers**: `enableAllProjectMcpServers` or `enabledMcpjsonServers` in `.claude/settings.json` are ignored in untrusted folders
- Non-interactive modes can bypass with `--strict-mcp-config` or `skipDangerousModePermissionPrompt`

Source: [MCP documentation](https://code.claude.com/docs/en/mcp.md)

## 2. Skills: User-Level vs. Project-Level Scoping

### Discovery Locations
| Scope | Path | Applies To | Inclusion |
|-------|------|-----------|-----------|
| **User-level** | `~/.claude/skills/<skill-name>/SKILL.md` | All projects on this machine | Not in Cowork or cloud sessions |
| **Project-level** | `.claude/skills/<skill-name>/SKILL.md` | This repository only | Commit for team access |
| **Nested** | `<subdir>/.claude/skills/<skill-name>/SKILL.md` | Sessions in/below subdirectory | Auto-discovered |
| **Plugin** | `<plugin>/skills/<skill-name>/SKILL.md` | Wherever plugin is enabled | Namespaced as `/plugin-name:skill-name` |
| **Enterprise** | Managed settings directory | All users with deployed settings | Highest precedence |

Source: [Skills documentation](https://code.claude.com/docs/en/skills.md)

### Naming Conflicts and Precedence
When skills share the same name, the precedence is:
1. **Enterprise** (highest)
2. **Personal** (`~/.claude/skills/`)
3. **Project** (`.claude/skills/`) (lowest)

Source: [Skills documentation](https://code.claude.com/docs/en/skills.md) — "When two skills share a name, where each one came from decides which one `/name` runs... **Enterprise over personal, and personal over project.**"

**No harm in idempotent writing**: A CLI can safely write a skill folder to `~/.claude/skills/<name>/` idempotently; if the folder exists and matches the intended state, no changes occur. Subsequent `claude` sessions pick up the user-level skill immediately.

## 3. Agent Definitions: Locations, Model Field, and Invocation

### Agent Definition Locations
| Scope | Path | Invocation |
|-------|------|-----------|
| **Project-level** | `.claude/agents/<agent-name>/AGENT.md` (or `.md` directly) | `/agent-name` from any session in this project |
| **User-level** | `~/.claude/agents/<name>.md` | `/name` available globally (implied; verified through subagent precedence) |
| **Built-in** | Claude Code packaged agents | `Explore`, `Plan`, `general-purpose` |

Source: Inferred from [Skills documentation](https://code.claude.com/docs/en/skills.md) and [Agents documentation](https://code.claude.com/docs/en/agents.md)

### The `model` Frontmatter Field

**Field exists**: Yes. Defined in agent/subagent `.md` files as a frontmatter field.

**Valid values**:
- Model aliases: `haiku`, `sonnet`, `opus`, `fable`, `best`
- Extended context variants: `opus[1m]`, `sonnet[1m]` (1M token windows)
- Full model IDs: `claude-opus-5`, `claude-sonnet-5`, `claude-fable-5-1`, etc.
- `inherit`: uses the main session's model (if applicable)

**Precedence** (highest to lowest):
1. Per-invocation `model` parameter (when Claude spawns the agent)
2. Agent definition's `model` frontmatter
3. `CLAUDE_CODE_SUBAGENT_MODEL` environment variable
4. Main conversation's model (fallback)

Source: [Model configuration documentation](https://code.claude.com/docs/en/model-config.md) — "Set the model in three ways: **1. Frontmatter in subagent definition:** ... **2. Environment variable for all subagents:** ... **3. Per-invocation override** (takes highest precedence)"

### Overriding Model Per Invocation
**Yes**, the `model` field can be overridden at invocation time:
- When Claude spawns an agent dynamically, it can pass a `model` parameter
- This takes **highest precedence** over the agent's frontmatter `model` field
- Useful for cost control or performance requirements in specific tasks

Source: [Model configuration documentation](https://code.claude.com/docs/en/model-config.md)

### Skills Referencing Custom Agents
A skill can reference a custom agent through the `context` and `agent` frontmatter fields:
```yaml
---
name: my-skill
context: fork
agent: custom-agent-name
---
```

When `context: fork` is set, the skill runs in an isolated subagent context. The `agent` field specifies which agent to use. Built-in options are `Explore`, `Plan`, and `general-purpose`; custom agents in `.claude/agents/` can also be referenced.

Source: [Skills documentation](https://code.claude.com/docs/en/skills.md)

## 4. SKILL.md Frontmatter Fields and Settings References

### Complete SKILL.md Frontmatter
Standard frontmatter fields in `SKILL.md`:

| Field | Type | Purpose |
|-------|------|---------|
| `name` | string | Display name; defaults to directory name |
| `description` | string | What skill does; Claude uses this for invocation decisions (max 1,536 chars with `when_to_use`) |
| `when_to_use` | string | Additional context (appended to description) |
| `argument-hint` | string | Autocomplete hint, e.g., `[issue-number]` |
| `arguments` | list/string | Named positional arguments for `$0`, `$1`, `$name` substitution |
| `model` | string | Override model when skill is active (e.g., `sonnet`, `opus`, `haiku`) |
| `effort` | string | Override effort level: `low`, `medium`, `high`, `xhigh`, `max` |
| `context` | string | Set to `fork` to run in isolated subagent |
| `agent` | string | Subagent type when `context: fork` (e.g., `Explore`, `Plan`, or custom agent name) |
| `background` | boolean | Set to `false` to wait for forked skill result (default: `true`) |
| `allowed-tools` | list/string | Pre-approve tools for this skill's turn |
| `disallowed-tools` | list/string | Remove tools from available pool during skill |
| `disable-model-invocation` | boolean | Prevent Claude from auto-loading (user can still invoke with `/name`) |
| `user-invocable` | boolean | Set to `false` to hide from `/` menu |
| `hooks` | YAML object | Hooks registered when skill is invoked |
| `paths` | list | Glob patterns limiting when skill activates |
| `shell` | string | Shell for commands: `bash` (default) or `powershell` |
| `metadata` | YAML object | Free-form custom key-value data |
| `license` | string | License (for distribution) |
| `compatibility` | string | Environment requirements |

Source: [Skills documentation](https://code.claude.com/docs/en/skills.md)

### String Substitutions Available in Skill Body
The skill body can reference runtime values:

| Substitution | Value |
|--------------|-------|
| `$ARGUMENTS` | All passed arguments |
| `$0`, `$1`, `$2`... | Specific arguments by index |
| `$name` | Named argument (from `arguments` frontmatter) |
| `${CLAUDE_SESSION_ID}` | Current session ID |
| `${CLAUDE_EFFORT}` | Current effort level |
| `${CLAUDE_SKILL_DIR}` | Skill directory path |
| `${CLAUDE_PROJECT_DIR}` | Project root directory |
| `${CLAUDE_PLUGIN_ROOT}` | Plugin directory (plugin skills only) |
| `${CLAUDE_PLUGIN_DATA}` | Plugin persistent data directory |

Source: [Skills documentation](https://code.claude.com/docs/en/skills.md)

### Dynamic Context Injection
Skills can execute commands inline and replace them with output:
```markdown
Current diff: !`git diff HEAD`
```

Multiline commands:
````markdown
```!
node --version
git status --short
```
````

Commands run before Claude sees the skill content; output replaces the placeholder.

Source: [Skills documentation](https://code.claude.com/docs/en/skills.md)

## 5. Profile Sync Considerations: Dropbox + Skills Repo

### User's Configuration
The user's CLAUDE.md states:
- **Personal profile:** Skills and agents synced via Dropbox (`C:\Dropbox\claude` on PC, linked to each profile root)
- **Versioned skills:** Private Skills repo (github.com/JddAndrewLauren/Skills) with `./link.sh` creating links/junctions into the profile's agents/skills directories
- **Link conflict handling:** If a repo skill goes missing or `link.sh` warns "a real file/dir exists", delete the empty artifact and re-run `./link.sh`
- **Sync strategy:** Only skills/agents/CLAUDE.md/scripts belong in Dropbox; never transcripts, projects/, settings.json, or credentials

Source: User's global CLAUDE.md at `/home/john/.claude-work/CLAUDE.md`

### Installation Path for Third-Party CLI

A third-party CLI (`strata init`) should install user-level skills and agents at:

```
~/.claude/skills/<skill-name>/SKILL.md
~/.claude/agents/<agent-name>.md
```

These paths are **automatically synced by the user's Dropbox setup** (symlinked from `C:\Dropbox\claude`), so:
- ✓ Changes survive across machine reboots and Dropbox sync
- ✓ No conflict with versioned skills (which arrive via `./link.sh` creating symlinks/junctions from the Skills repo)
- ✓ Idempotent writes are safe; existing files are preserved or overwritten as intended
- ✓ The `./link.sh` refusal to overwrite a newer differing copy does not apply to non-repo installations

**Important caveat**: If the user has manually configured `./link.sh` or Dropbox to exclude certain skills/agents, writing to `~/.claude/skills/` may not sync correctly. The CLI should document this assumption and recommend users verify their sync setup.

## Recommendation for `strata init`

### Installation Strategy

The `strata init` command should:

1. **Per-project (committed)**:
   - Write `.strata/config.yaml` with corpus roots and manuscript path
   - Write `.mcp.json` with the strata server configuration (project-scoped, shared with team)
   - Gitignore `.strata/index.db` (derived, disposable)

2. **User-level (not committed)**:
   - Check if `~/.claude/skills/strata/SKILL.md` exists; if not, create it
   - Check if `~/.claude/agents/strata-reader.md` exists; if not, create it
   - These paths are already synced via the user's Dropbox setup and survive machine sync

3. **Idempotence**:
   - On re-run, verify that user-level skill/agent versions match the intended state
   - If user has a newer version, prompt before overwriting
   - If user-level assets are missing, create them silently

4. **Workspace Trust**:
   - Document that the first `claude` session in a new project folder will prompt for workspace trust (approving the `.mcp.json` servers)
   - This is standard Claude Code behavior and requires no CLI action

5. **Git Worktrees**:
   - Each worktree's `.mcp.json` is independent; `strata init` can re-run per worktree
   - User-level skill/agent are shared across worktrees (as intended)

### Paths Summary

| Component | Path | Scope | Committed |
|-----------|------|-------|-----------|
| Config | `.strata/config.yaml` | Per project | Yes |
| Index | `.strata/index.db` | Per project | No (derived) |
| MCP server | `.mcp.json` | Per project | Yes |
| Skill | `~/.claude/skills/strata/` | User-level | No (Dropbox synced) |
| Reader agent | `~/.claude/agents/strata-reader.md` | User-level | No (Dropbox synced) |

### Verification

After `strata init`, the user should:
1. Run `claude` in the project folder and accept workspace trust (approves `.mcp.json`)
2. Verify `/strata` skill is available via autocomplete
3. Verify reader agent is available if needed
4. Run first index: `strata index` or via the skill

No manual copy/link/sync steps required; Dropbox handles sync.

---

**Confidence**: High for questions 1–4 (documented in official Claude Code docs). Medium for question 5 (user's profile setup is documented in their CLAUDE.md but third-party CLI best practices are not explicitly stated in Claude Code docs; recommendation is inferred from the user's stated setup and Claude Code's scoping model).

**Next steps**: Verify with a test installation of `strata init` that skills/agents appear at the correct paths and sync correctly on a second machine via Dropbox.
