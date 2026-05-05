# CLAUDE.md — sonar-fix Development Guide

## Project Overview

This is `sonar-fix`, a central GitHub repo that provides org-wide reusable
workflows and a composite action for automatically fixing SonarQube issues on
pull requests using AI coding agents (Claude Code, GitHub Copilot, or
OpenAI Codex).

The architecture has three layers:

1. **Caller workflows** (live in each consuming repo): Handle the trigger event
   (PR comment from SonarCloud, or PR open/push), resolve PR context, and call
   the reusable workflow. Examples are in `examples/`.

2. **Reusable workflows** (live in this central repo at `.github/workflows/`):
   Trigger-agnostic orchestration — scan (optional), triage, post review
   comments, and dispatch to AI agent. They receive `pr-number` and `pr-branch`
   as explicit inputs so they work with any trigger type.

3. **Composite triage action** (`triage-action/`): A Python script + action.yml
   that fetches SonarQube issues, categorizes them against the repo's fix
   config (severity, type, rule allow/deny lists, path exclusions), and outputs
   structured JSON.

## Repo Structure

```
.github/workflows/
  fix.yml                       # Unified reusable workflow. Inner jobs:
                                #   scan-and-triage → post-triage-comment →
                                #   claude-fix  (if contains(agent,'claude'))
                                #   copilot-fix (if contains(agent,'copilot'))
                                #   codex-fix   (if contains(agent,'codex'))

triage-action/
  action.yml                    # Composite action definition
  triage_sonar_issues.py        # Python triage engine (fetches + categorizes issues)

examples/
  caller-comment-triggered.yml  # Caller: fires on SonarCloud bot PR comment + /sonar-fix
  caller-pr-triggered.yml       # Caller: fires on PR open/push
  copilot-mcp-setup.json        # MCP config for Copilot coding agent settings

prompts/
  sonar-fix-agent.md            # Central agent prompt — injected into the consumer's
                                # AGENTS.md (Claude) or @copilot comment (Copilot) at
                                # run time. Single source of truth for the protocol.

config/
  default.yml                   # Central default sonar-fix-config used when the
                                # consumer doesn't ship .github/sonar-fix-config.yml.
```

## Key Design Decisions

- Reusable workflows take `pr-number` and `pr-branch` as inputs — they never
  read `github.event.pull_request` directly. This makes them work with both
  `pull_request` and `issue_comment` trigger events.

- The recommended trigger is `issue_comment` on SonarCloud's bot comment
  (`caller-comment-triggered.yml`). This guarantees analysis is complete,
  eliminates scan duplication, and works with both CI-based and automatic
  analysis.

- The triage script (`triage_sonar_issues.py`) applies a priority chain:
  deny list → allow list → path exclusions → severity/type match. Issues that
  pass are `auto_fix`; everything else is `review_only`.

- The `agent` field is a comma-separated list of any of `claude`, `copilot`,
  `codex`. The triage script normalizes input — `all` expands to all three,
  unknown tokens hard-fail. Each fix job's `if:` filter uses `contains()` to
  test membership, which is safe because no agent name is a substring of
  another.

- When `enable-agentic-analysis: true`, the MCP server is configured with
  `SONARQUBE_TOOLSETS=cag,projects,analysis,issues,quality-gates,rules` and
  `SONARQUBE_ADVANCED_ANALYSIS_ENABLED=true`. The workspace is volume-mounted
  at `/app/mcp-workspace:rw`. The agent prompt uses the Guide → Fix → Verify
  loop: `get_guidelines` before coding, `run_advanced_code_analysis` after.

- The SonarQube MCP server runs via `docker run mcp/sonarqube` inside the
  GitHub Actions runner. Claude Code receives the config via
  `--mcp-config /tmp/sonar-mcp-config.json`. Codex CLI has no such flag —
  it reads MCP servers from `$CODEX_HOME/config.toml`, so the codex-fix
  job pre-creates a tmpdir, writes a `[mcp_servers.sonarqube]` table into
  it (TOML, not JSON), and passes that dir to `openai/codex-action` via
  the `codex-home:` input.

- Codex has no `--max-turns` equivalent. Runaway protection on the codex-fix
  job comes from `--ask-for-approval=never` + the natural completion of the
  task. The `guardrails.max_turns` config field is consulted only by the
  Claude path. If Codex token cost becomes an issue, look at adding
  `model_auto_compact_token_limit` to the generated config.toml.

## Testing Plan

### Phase 1: Unit test the triage script locally

The triage script (`triage_sonar_issues.py`) is pure Python with no external
dependencies beyond PyYAML. Test it first:

```bash
cd triage-action
pip install pyyaml

# Create a mock GITHUB_OUTPUT file
export GITHUB_OUTPUT=$(mktemp)

# Create a test config
cp ../config/default.yml /tmp/test-config.yml

# Run with mock SonarQube API responses
# (you'll need to either mock the API or point at a real instance)
export SONAR_TOKEN="your-token"
export SONAR_HOST_URL="https://sonarcloud.io"
python3 triage_sonar_issues.py \
  --config /tmp/test-config.yml \
  --project-key "your-org_your-repo" \
  --branch main
```

Verify:
- Issues are correctly categorized as auto_fix vs review_only
- Deny-listed rules always go to review_only
- Allow-listed rules override severity/type filters
- Path exclusions work
- max_issues_per_run cap is applied
- GITHUB_OUTPUT file contains valid JSON
- The `agent` field is normalized: `all` expands to `claude,copilot,codex`,
  unknown tokens hard-fail with a clear error, comma lists round-trip
  cleanly, ordering is deterministic (first-occurrence dedupe).

Consider writing pytest unit tests that mock the SonarQube API responses
and verify the categorization logic in isolation.

### Phase 2: Test the comment-triggered caller in a real repo

1. Create this central repo in your org and push all files
2. Go to Settings → Actions → General and enable "Allow other repos in the org"
3. Pick a test repo that already has SonarCloud configured with PR comments
4. Add to the test repo:
   - `.github/workflows/sonar-fix.yml` (from `examples/caller-comment-triggered.yml`)
   - `.github/sonar-fix-config.yml` — optional; only if you want to override the central default at `config/default.yml`
   - **No AGENTS.md** — the workflow injects `prompts/sonar-fix-agent.md` into AGENTS.md at run time, shielded from being committed back
5. Set org-level secrets: `SONAR_TOKEN`, plus the agent-specific secret(s):
   `ANTHROPIC_API_KEY` (Claude), `OPENAI_API_KEY` (Codex), `COPILOT_PAT` (Copilot)
6. Set repo variable: `SONAR_PROJECT_KEY`
7. Open a PR that introduces code with known SonarQube issues
8. Wait for SonarCloud to post its comment → verify the fix workflow triggers

Watch for:
- Does the `issue_comment` filter correctly match the SonarCloud bot? Check
  the exact bot username (default is `sonarqubecloud[bot]`; override via the
  `SONAR_BOT_LOGIN` repo variable).
- Does the quality gate pass/fail detection work? The comment body parsing
  looks for "Quality Gate passed/failed" and ✅/❌ emoji.
- Does the triage job correctly fetch issues from the SonarQube API?
- Does the MCP server Docker image pull and start correctly?
- Does Claude Code receive the issues and produce meaningful fixes?

### Phase 3: Validate the Guide → Fix → Verify loop

With `enable-agentic-analysis: true`:
- Does `get_guidelines` return relevant context before fixes?
- Does `run_advanced_code_analysis` work on modified files?
- Does the agent iterate when a fix introduces new issues?
- Does it respect the 3-cycle max per file?

### Phase 4: Test edge cases

- PR with zero issues (quality gate passes) — workflow should not trigger
- PR with only review-only issues (all denied/excluded) — agent job should skip
- PR with more issues than `max_issues_per_run` — overflow goes to review-only
- Closed PR receiving a late SonarCloud comment — should skip
- Multiple SonarCloud comments on the same PR — concurrency group should cancel
- SonarQube Server (not Cloud) — verify MCP config without org/agentic analysis

## Known Issues and TODO

- [ ] The `sleep 15` in the PR-triggered scan path is fragile. Consider polling
  `api/ce/task` or using `sonarqube-quality-gate-action` to wait properly.
- [ ] The comment body parsing for quality gate status is brittle — SonarCloud
  may change its comment format. Consider using the SonarQube API
  (`api/qualitygates/project_status`) as the source of truth instead.
- [ ] The triage script uses `urllib` directly. Consider switching to `requests`
  or at minimum adding retry logic for transient API failures.
- [ ] No tests exist yet for `triage_sonar_issues.py`. Write pytest tests with
  mocked API responses.
- [ ] The `--allowedTools` list in the Claude Code step may need adjustment
  based on what tools the SonarQube MCP server actually exposes. Verify the
  tool names match.
- [ ] The Copilot workflow hasn't been updated with `enable-agentic-analysis`
  support. The MCP config in `copilot-mcp-setup.json` should be updated to
  include the agentic analysis env vars.
- [ ] Validate at end-to-end run time that `openai/codex-action`'s internal
  "Write Codex proxy config" step doesn't clobber our pre-written
  `$CODEX_HOME/config.toml`. The action's bundled JS is opaque; if it
  overwrites instead of appending, the SonarQube MCP entry gets lost and
  Codex runs without MCP. If observed, work around by passing the MCP
  server entries via `codex-args: '["--config", "mcp_servers..."]'`
  scalar overrides instead of (or in addition to) the TOML file.
- [ ] Codex's per-MCP-server `enabled_tools` allowlist isn't set on the
  `[mcp_servers.sonarqube]` block (we permit all). If Codex burns tokens
  invoking irrelevant SonarQube tools, tighten to the Guide → Fix →
  Verify protocol's set (`get_guidelines`, `show_rule`,
  `run_advanced_code_analysis`, `search_by_signature_patterns`,
  `get_source_code`, etc.).
- [ ] Add a concurrency group to the comment-triggered caller to prevent
  multiple fix runs on the same PR if SonarCloud posts multiple comments.
- [ ] Consider adding a "re-scan after fix" step that triggers a new SonarCloud
  analysis on the agent's commits, creating a feedback loop until the quality
  gate passes.
- [x] The `SONAR_BOT_LOGIN` env var in the comment-triggered caller now
  defaults to `sonarqubecloud[bot]` and is overridable via the
  `SONAR_BOT_LOGIN` repo variable. Both the job filter and the env read
  from the same source.
- [ ] Add observability: workflow annotations, summary output, or Slack
  notifications for fix results.
- [ ] Consider making the triage script available as a standalone CLI tool
  (with `argparse` already in place, it's close) so developers can preview
  what would be fixed locally before pushing.

## Development Commands

```bash
# Validate YAML syntax
yamllint .github/workflows/*.yml examples/*.yml

# Lint the Python script
ruff check triage-action/triage_sonar_issues.py
mypy triage-action/triage_sonar_issues.py

# Run the triage script locally (requires SONAR_TOKEN and SONAR_HOST_URL)
cd triage-action && python3 triage_sonar_issues.py \
  --config ../config/default.yml \
  --project-key "your-org_your-repo" \
  --branch main

# Test the MCP server Docker image locally
docker run --rm -i --init \
  -e SONARQUBE_TOKEN="$SONAR_TOKEN" \
  -e SONARQUBE_ORG="your-org" \
  -e SONARQUBE_PROJECT_KEY="your-org_your-repo" \
  -e SONARQUBE_TOOLSETS="cag,projects,analysis,issues,quality-gates,rules" \
  -e SONARQUBE_ADVANCED_ANALYSIS_ENABLED=true \
  -v "$(pwd):/app/mcp-workspace:rw" \
  mcp/sonarqube
```

## Reference Links

- [SonarSource Agentic Analysis Getting Started (Claude Code)](https://github.com/SonarSource/getting-started-agentic-analysis-claude-code)
- [Claude Code Action](https://github.com/anthropics/claude-code-action)
- [SonarQube MCP Server Docker image](https://hub.docker.com/r/mcp/sonarqube)
- [GitHub Reusable Workflows docs](https://docs.github.com/en/actions/sharing-automations/reusing-workflows)
- [GitHub Composite Actions docs](https://docs.github.com/en/actions/sharing-automations/creating-actions/creating-a-composite-action)
