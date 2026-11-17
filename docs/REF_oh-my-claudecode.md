# Reference: oh-my-claudecode (OMC)

External reference saved from the GitHub page https://github.com/Yeachan-Heo/oh-my-claudecode (MIT license, ~40.8k stars, latest release v5.8.4/v5.8.6 at time of capture). GitHub navigation chrome and the repository file listing were dropped; the README content is kept. This is not project code and nothing in this repo depends on it.

> Multi-agent orchestration for Claude Code. Zero learning curve.
> For Codex users: oh-my-codex. Lighter alternative: gajae-code.

Core maintainer: Yeachan Heo (@Yeachan-Heo). Package naming: repo/plugin/commands are `oh-my-claudecode`; the npm package is `oh-my-claude-sisyphus` (installs both `oh-my-claudecode` and `omc`).

## Quick start

1. Install (Claude Code slash commands, enter one at a time):
   - `/plugin marketplace add https://github.com/Yeachan-Heo/oh-my-claudecode`
   - `/plugin install oh-my-claudecode`
   - or npm: `npm i -g oh-my-claude-sisyphus@latest`
2. Setup: `/omc-setup` in a session, or `omc setup` in a terminal. With `--plugin-dir <path>`, add `--plugin-dir-mode` (or export `OMC_PLUGIN_ROOT`).
3. Build: `/autopilot "build a REST API for managing tasks"`.

Named autopilot stage profiles: `/autopilot --workflow plan-build-qa "<task>"`. Profiles live under `autopilot.workflows` in `.claude/omc.jsonc` (project) or `~/.config/claude-omc/config.jsonc` (user), v1 shape `{ "version": 1, "stages": [...] }`. Allowed stage sequences: `[ralplan, execution]`, `[ralplan, execution, ralph]`, `[ralplan, execution, qa]`, `[ralplan, execution, ralph, qa]`. A project profile replaces a same-named user profile. Requires Linux with `flock`.

## CLI vs in-session

| Feature | Terminal CLI | In-session skill |
|---|---|---|
| Setup | `omc setup` | `/omc-setup` |
| Ask providers | `omc ask codex "..."` | `/ask codex "..."` (providers: claude, codex, gemini, antigravity, grok, cursor, muse) |
| Team | `omc team 2:codex "..."` (tmux CLI workers) | `/team 3:executor "..."` (native in-session team) |
| Pre-flight scan | `omc lookout scan --brief "..." [--json] [--strict]` | none |
| Autopilot / Ralph / Execute / Deep Interview | none | `/autopilot`, `/ralph`, `/execute`, `/deep-interview` |

For CI/headless use prefer deterministic terminal commands; do not rely on slash commands there.

## Modes

- **Team** (recommended): staged pipeline `team-plan -> team-prd -> team-exec -> team-verify -> team-fix (loop)`. Enable with `"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"}` in `~/.claude/settings.json`.
- **omc team (CLI)**: tmux workers (`N:codex`, `N:gemini`, `N:antigravity`, `N:grok`, `N:cursor`, `N:claude`). Workers spawn on demand and exit when done. Needs the CLI installed and an active tmux session.
- **Tri-model advisor**: `/ask codex` + `/ask antigravity`, Claude synthesizes.
- **Autopilot**: single lead agent, end-to-end.
- **Execute**: persistent execution with verify/fix loops.
- **Verify**: evidence-based completion checks.
- Claude Code `/goal` and artifact-only Ultragoal: use only one primary loop authority per session.

## Features

- 19 specialized agents with tier variants; model routing (Haiku for simple, Opus for complex); automatic delegation.
- Prompt triggers: `ralph`, `ralplan`, `deepsearch`, `ultrathink`, `cancelomc`/`stopomc`. Team is explicit via `/team`.
- HUD statusline (`/oh-my-claudecode:hud setup`, `omc hud`).
- Custom skills: `.omc/skills/` (project, higher priority) and `~/.omc/skills/` (user). Skill files carry frontmatter (`name`, `description`, `triggers`, `source`). Manage with `/skill list|add|remove|edit|search`; `/skillify` extracts patterns.
- `.omc/` holds runtime state and is gitignored except `.omc/skills/**`. Set `OMC_STATE_DIR` to keep state outside a worktree; a `.omc-workspace` marker shares one state root across sibling repos.

## Utilities

- `omc ask <provider> --prompt "..."` saves a markdown artifact under `.omc/artifacts/ask/`.
- Autoresearch: `/deep-interview --autoresearch ...` then `/oh-my-claudecode:autoresearch` (`omc autoresearch` is a deprecated shim).
- Rate-limit wait: `omc wait`, `omc wait --start`, `omc wait --stop` (needs tmux).
- Observability: `.omc/sessions/*.json`, `.omc/state/agent-replay-*.jsonl`, `omc session friction report --since 24h [--json]`.
- Jev judgment (off by default, zero egress): `TYPESAFE_API_KEY`, `OMC_JEV` (`shadow` | `active` | `off`), `OMC_JEV_TIMEOUT_MS`.
- Stop-callback notifications: `omc config-stop-callback telegram|discord|slack ...` with `--tag-list`, `--add-tag`, `--remove-tag`, `--clear-tags`.
- OpenClaw gateway forwarding: config `~/.claude/omc_config.openclaw.json`, env `OMC_OPENCLAW=1`, hook events `session-start`, `stop`, `keyword-detector`, `ask-user-question`, `pre-tool-use`, `post-tool-use`.

## Requirements

Claude Code CLI; Claude Max/Pro or an Anthropic API key. tmux for `omc team` and rate-limit detection (macOS `brew install tmux`; Windows `psmux` or WSL). On Windows use Node 22+ for provider spawns. Optional external providers: Antigravity CLI (`agy`), Gemini CLI, Codex CLI, Grok Build.

## Documentation links

Full Reference, CLI Reference, Notifications Guide, Workflows, Release Notes, Migration Guide, Architecture, Performance Monitoring, Model x Agent Compatibility Matrix, Security Guide, all in the repository `docs/` folder.
