# Troubleshooting

Common misconfigurations and how to fix them. The Actions tab is the source of truth — open the failed run, click the failed job, expand the failed step. Each entry below maps a visible symptom to its underlying cause and the fix.

If you don't see your symptom here, check [gotchas.md](gotchas.md) — non-obvious behaviors that are *expected* are documented there.

---

## SonarCloud comments don't trigger any workflow run

**Symptom.** SonarCloud's bot posts on the PR (you can see the comment), but the **Actions** tab shows no `sonar-fix` run at all — not even a startup failure.

**Cause.** Bot login mismatch. The workflow's filter expects `sonarqubecloud[bot]` by default; your Sonar product may use a different login (older `sonarcloud[bot]`, on-prem variant, etc.).

**Fix.** Open a recent PR comment from the Sonar bot and copy the exact author login from the GitHub UI. Set it as a repo variable `SONAR_BOT_LOGIN` (Settings → Secrets and variables → Actions → Variables) — both the job filter and the workflow env reference it, so one variable change covers both.

---

## `/sonar-fix` comment posted but no workflow runs

**Cause.** The commenter's `author_association` isn't `OWNER`, `MEMBER`, or `COLLABORATOR`. The workflow filter silently rejects everyone else to prevent drive-by commenters from running billable agent jobs on public repos.

**Fix.** Have someone with write access to the repo post the comment.

---

## "Run Claude Code" step fails: *"Claude Code is not installed on this repository"*

**Symptom.** The exact error message:

> 401 Unauthorized — Claude Code is not installed on this repository. Please install the Claude Code GitHub App at <https://github.com/apps/claude>

**Cause.** The OIDC → app-token exchange that `claude-code-action@v1` performs requires the **Claude Code GitHub App** to be installed on the consuming repo (or its parent account), regardless of whether you use a direct API key or a proxy.

**Fix.** Install the App per [claude.md Step 4](claude.md#step-4--install-the-claude-code-github-app). Re-trigger with `/sonar-fix` — no workflow changes needed.

---

## "Validate proxy config" step fails: *"ANTHROPIC_CUSTOM_HEADERS is set, but the anthropic-base-url input is empty"*

**Cause.** You set `ANTHROPIC_BASE_URL` as a repo Secret rather than a Variable. The caller workflow reads it via `vars.X`, which can't see secrets — so from the workflow's perspective, the URL is empty.

**Fix.** Delete the Secret and recreate as a Variable: Settings → Secrets and variables → Actions → **Variables** → New. The URL value (e.g. `https://api.portkey.ai`) is non-sensitive — Variable is the correct classification. See [gateways.md](gateways.md#variable-vs-secret-matters).

---

## "Validate proxy config" step fails: *"OPENAI_CUSTOM_HEADERS is set but openai-base-url is empty"*

Same root cause and fix as the Anthropic version above, but for the Codex path. Move `OPENAI_BASE_URL` from Secrets to Variables.

---

## "Run Claude Code" step fails: *"ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN is required"*

**Cause.** Either:
- (a) The workflow couldn't detect your proxy because `ANTHROPIC_BASE_URL` is set as a Secret instead of a Variable, OR
- (b) You're not proxying and `ANTHROPIC_API_KEY` simply isn't set.

**Fix.**
- (a) See the "Validate proxy config" entry above.
- (b) Set the `ANTHROPIC_API_KEY` org-level secret per [claude.md Step 2](claude.md#step-2--add-org-level-secrets).

---

## "Run Codex to fix issues" step fails: *401 Unauthorized* / *invalid_api_key*

**Cause.** `OPENAI_API_KEY` is missing, mistyped, or scoped to the wrong project. When proxying through Portkey, this often means you put the Portkey virtual key in `OPENAI_CUSTOM_HEADERS` as `x-portkey-api-key:` (the Anthropic pattern) instead of putting it directly in `OPENAI_API_KEY`. Portkey resolves OpenAI virtual keys from the bearer Authorization header, not from `x-portkey-api-key`.

**Fix.** Confirm `OPENAI_API_KEY` is a repo or org Secret (not a Variable). For direct OpenAI, use your real key. For Portkey, set it to your `pk_xxxxx` virtual key directly. For Helicone, use your real OpenAI key plus `OPENAI_CUSTOM_HEADERS: Helicone-Auth: Bearer hk_xxxxx`. See [gateways.md](gateways.md#routing-codex-openai).

---

## "Scan & Triage" fails fetching issues from SonarQube

**Cause.** `SONAR_TOKEN` is missing, scoped to the wrong project, or `SONAR_PROJECT_KEY` (the per-repo variable) is wrong.

**Fix.**
1. If your repo already has a build workflow that runs the Sonar scanner, confirm it can authenticate to SonarCloud — `sonar-fix` uses the same token.
2. Cross-check `vars.SONAR_PROJECT_KEY` against the `sonar.projectKey` in your build's `sonar-project.properties` (or equivalent). They must match exactly.

---

## MCP server container fails to start

**Symptom.** A step like "Pull MCP server image" or the agent step fails with Docker errors, or the agent hangs early in the run.

**Cause.** Either the runner can't reach Docker Hub (network restriction), or the MCP container's `SONARQUBE_TOKEN` env var came through empty (token fallback chain returned nothing).

**Fix.**
1. Check the "Pull MCP server image" step logs — confirm the runner has internet access to Docker Hub for `mcp/sonarqube`.
2. Confirm `SONAR_TOKEN` *or* `COPILOT_MCP_SONAR_TOKEN` is set as a secret (the workflow falls back from one to the other).

---

## Agent runs but no commit appears on the PR

**Symptom.** The fix-dispatch job completes successfully, the run log shows the agent burned a non-trivial number of turns, but `git log` on the PR branch doesn't show a `fix: resolve SonarQube issues` commit.

**Most likely cause.** The agent decided nothing was fixable (everything was filtered out, or the agent reasoned through the issues and skipped them all). The workflow's "Push agent commits" step always runs after the agent and pushes any local commits — so if there's no commit on the PR, the agent didn't make one.

**Fix.**
1. Inspect the triage step's output — `has_auto_fix` should be `true`. If `false`, your `sonar-fix-config.yml` filtered everything out. Loosen `auto_fix.severities` or add specific rules to `auto_fix.rules.allow`. See [configuration.md](configuration.md).
2. For the Claude path, set `vars.SHOW_FULL_OUTPUT=true` and re-trigger. The verbose log will show the agent's reasoning. If it ran `git commit` but the "Push agent commits" step reported "No new commits to push," something rebased history away (rare).
3. For the Codex path, inspect the "Run Codex to fix issues" step's `final-message` output — Codex prints a summary of what it did/didn't fix.

---

## Copilot path: fix lands on the PR but the loop doesn't re-fire

**Symptom.** Copilot pushed a fix commit, but `build.yml` shows `action_required` in the Actions tab (queued, waiting for approval) and no follow-up `sonar-fix` run kicked off.

**Cause.** GitHub treats Copilot's pushes/comments like outside-contributor events; downstream workflows queue in `action_required` until a maintainer approves them. This is the default per-repo setting since March 2026.

**Fix.** Settings → Code & automation → Copilot → Coding agent → uncheck **"Require approval for workflow runs"**. Now `build.yml` (and the next `sonar-fix.yml`) will run automatically on Copilot's pushes. See [copilot.md Step 5c](copilot.md#5c-allow-copilots-workflow-runs-to-skip-approval) for the trade-off.

---

## Claude/Codex fix lands on the PR but the build never re-fires

**Symptom.** Agent commit appears on the PR, but the build/Sonar workflow doesn't run on it. The `sonar-fix` loop appears stuck.

**Cause.** The push got attributed to `github-actions[bot]` instead of your `AGENT_PUSH_TOKEN` user, so GHA's recursive-trigger protection silently dropped the resulting `pull_request: synchronize` event.

**Fix.** Confirm `AGENT_PUSH_TOKEN` is set as an org-level secret per [claude.md Step 2](claude.md#step-2--add-org-level-secrets) or [codex.md Step 2](codex.md#step-2--add-org-level-secrets). The current `fix.yml` also explicitly clears the `http.extraheader` config before pushing (which is what causes the attribution bug when missing) — see [gotchas.md](gotchas.md#2-the-push-step-explicitly-unsets-httpextraheader-before-pushing) for the full mechanism.
