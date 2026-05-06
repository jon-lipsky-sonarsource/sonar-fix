# Configuration

Two pieces of configuration matter for `sonar-fix`:

1. **`sonar-fix-config.yml`** — controls which issues get auto-fixed vs flagged for human review, and which agent runs.
2. **The agent prompt** (`prompts/sonar-fix-agent.md` in the central repo) — defines the Guide → Fix → Verify protocol every agent follows.

This page documents both.

---

## `sonar-fix-config.yml`

The config file lives at one of two paths:

- **`config/default.yml`** in the central `sonar-fix` repo — the org-wide default. Used by every consumer that doesn't ship its own.
- **`.github/sonar-fix-config.yml`** in a consumer repo — overrides the central default for that one repo.

Most consumers won't need to override. Copy the central default as a starting point only if you need different rules, severities, or path exclusions for a specific repo.

### Schema

The triage script applies a priority chain:

```
deny list → allow list → path exclusions → severity/type match
```

Issues passing all filters land in the `auto_fix` bucket; everything else goes to `review_only`.

```yaml
# Which agent handles the fix dispatch. One of: claude, copilot, codex.
agent: claude

# Issues matching ALL of these criteria are eligible for automatic fixing.
auto_fix:
  severities: [BLOCKER, CRITICAL, MAJOR]    # BLOCKER, CRITICAL, MAJOR, MINOR, INFO
  types: [BUG, CODE_SMELL]                  # BUG, CODE_SMELL, VULNERABILITY, SECURITY_HOTSPOT

  rules:
    allow:                       # Always fix these (overrides severity/type filter)
      - "java:S1481"
      - "python:S1481"
    deny:                        # Never fix these (overrides everything)
      - "java:S2076"             # OS command injection — needs human review
      - "SECURITY_HOTSPOT:*"     # wildcard — never fix any security hotspot

# Files matching these globs are never fixed, even if their issues qualify.
paths:
  exclude:
    - "**/*.generated.*"
    - "**/generated/**"
    - "**/migrations/**"
    - "**/vendor/**"
    - "**/node_modules/**"

# Issues matching these criteria are posted as review-only PR comments.
review_only:
  severities: [MINOR, INFO]
  types: [VULNERABILITY, SECURITY_HOTSPOT]

# Safety guardrails.
guardrails:
  max_issues_per_run: 15         # Hard cap on issues sent to the agent (cost control)
  max_iterations: 3              # Max fix-verify cycles per file (agentic analysis)
  max_turns: 25                  # Max Claude Code agent turns per run
  run_tests_after_fix: false     # Run project tests post-fix; revert on failure
  verify_fixes: true             # Require run_advanced_code_analysis after every fix

notifications:
  post_summary: true
  tag_author_on_review_only: false
```

### Field reference

| Field | Used by | Purpose |
|---|---|---|
| `agent` | triage | Selects which fix-dispatch job runs. Exactly one of `claude`, `copilot`, `codex`. |
| `auto_fix.severities` | triage | SonarQube severities eligible for automatic fixing. |
| `auto_fix.types` | triage | SonarQube issue types eligible for automatic fixing. |
| `auto_fix.rules.allow` | triage | Rule keys ALWAYS fixed, even if severity/type would exclude them. |
| `auto_fix.rules.deny` | triage | Rule keys NEVER fixed; sent to review-only. Wildcards like `SECURITY_HOTSPOT:*` supported. |
| `paths.exclude` | triage | File globs to skip entirely. Overrides allow-list. |
| `review_only.severities` / `review_only.types` | triage | Issues that should be flagged in the triage comment but not fixed. |
| `guardrails.max_issues_per_run` | triage | Hard cap on issues sent to the agent. Overflow goes to review-only. |
| `guardrails.max_iterations` | agent prompt | Max fix-verify cycles per modified file when agentic analysis is enabled. |
| `guardrails.max_turns` | Claude path | Max agent turns per run. Codex has no equivalent — see [codex.md](codex.md). |
| `guardrails.run_tests_after_fix` | agent prompt | If `true`, agent runs project tests after fixing and reverts on failure. Requires the test suite to be runnable in the Actions runner. |
| `guardrails.verify_fixes` | agent prompt | If `true`, agent must call `run_advanced_code_analysis` after every fix. Requires `enable-agentic-analysis: true` in the workflow. |
| `notifications.post_summary` | post-triage-comment | Post a unified triage comment on the PR. |
| `notifications.tag_author_on_review_only` | post-triage-comment | Tag the PR author when issues are flagged for human review. |

### Switching agents

Switching from one agent to another is a one-line edit:

```yaml
agent: codex             # was: claude
```

The caller workflow doesn't change — `fix.yml` reads `agent` from the config and dispatches the matching job.

### Where filtering lives in the workflow

The triage script (`triage-action/triage_sonar_issues.py`) loads this config, fetches PR issues from the SonarQube API, applies the filter chain, and emits a structured JSON output that the dispatch jobs read. Issues that pass land in the `auto_fix` bucket; everything else lands in `review_only` and shows up in the post-triage PR comment without being routed to an agent.

---

## The agent prompt

`prompts/sonar-fix-agent.md` in the central repo is the single source of truth for how every agent approaches a `sonar-fix` run. It defines the **Guide → Fix → Verify → Commit** protocol:

- **Guide** — gather context via `get_guidelines`, `search_by_signature_patterns`, `get_current_architecture`, etc. before editing
- **Fix** — for each issue, call `show_rule` to read the rationale, then apply a minimal targeted change
- **Verify** — after every file modification, call `run_advanced_code_analysis` to catch regressions; max 3 fix-verify cycles per file
- **Commit** — subject must start with `fix: resolve SonarQube issues` (the loop guard depends on this prefix)

### How it reaches the agent

- **Claude path** — the workflow appends this file to the consumer's `AGENTS.md` at run time, shielded from being committed back via `git update-index --skip-worktree` (or `.git/info/exclude` if no `AGENTS.md` existed). Claude Code reads `AGENTS.md` from the working tree as it normally would.
- **Codex path** — same `AGENTS.md` injection step. Codex CLI walks up the working tree looking for `AGENTS.md` and follows the same convention as Claude, so the central prompt requires no agent-specific branching.
- **Copilot path** — the workflow inlines the file's contents into the `@copilot` PR comment body alongside the issue list.

See [gotchas.md](gotchas.md#6-the-agent-prompt-is-injected-at-run-time-not-committed-to-consumer-repos) for why we inject at run time rather than committing the prompt to consumer repos.

### Editing the prompt

Improvements to the agent prompt — new MCP tools, updated rule lookups, refined fix patterns — go in `prompts/sonar-fix-agent.md` in the central repo. Every consumer picks up the change on their next run with no per-repo update needed.

The prompt is intentionally agent-agnostic; if you're adding agent-specific guidance, prefer extending the workflow over branching the prompt.

### Required commit-subject prefix

The agent prompt requires every fix commit's subject line to start with:

```
fix: resolve SonarQube issues
```

The reusable workflow's loop guard counts commits matching this prefix to enforce `MAX_FIX_ATTEMPTS` (default `3`). Don't change this prefix without also changing the loop guard's `FIX_COMMIT_PREFIX` env var in your caller workflow.
