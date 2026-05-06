# Implementation gotchas

This page documents the non-obvious design choices in `sonar-fix` — things that look strange in the code or config and would tempt a future maintainer (or forker) to "clean up." Each entry explains the symptom we saw, the underlying cause, and why the current shape is the right one.

If you're just installing `sonar-fix`, you don't need any of this. Start at the [README](../README.md) instead.

---

## 1. The caller workflow declares the same permissions as the reusable workflow

In `examples/caller-comment-triggered.yml` you'll see:

```yaml
permissions:
  contents: write
  pull-requests: write
  issues: write
  id-token: write
```

Each one matches a permission `fix.yml` requests internally. The duplication looks redundant — surely the reusable workflow can grant its own permissions?

It can't. GitHub Actions reusable workflows don't inherit permissions; the calling workflow's `permissions:` block sets the **cap**, and the reusable workflow requests within that cap. If the caller is more restrictive (or doesn't declare a block at all and the repo defaults to read-only), the call fails at validation:

> Error calling workflow … is requesting `contents: write, id-token: write, …` but is only allowed `contents: read, …`

Listing it on the caller is the only way to keep "Default workflow permissions" set to read-only repo-wide while still letting `sonar-fix` push commits and post comments. It also keeps the auth surface visible in the consumer's repo — anyone reviewing `sonar-fix.yml` can see exactly what the workflow can do without chasing into the central repo.

The unified `fix.yml` requests all four permissions even when only one agent is active. Granting unused permissions is harmless: they only authorize jobs that don't run for the current agent. The Codex path doesn't actually need `id-token: write` (no OIDC flow), but leaving it in the caller's block keeps the permissions block agent-agnostic, so switching `agent:` in your config is a one-line edit.

---

## 2. The push step explicitly unsets `http.extraheader` before pushing

In the "Push agent commits" step of both the Claude and Codex paths in `fix.yml`:

```yaml
git config --local --unset-all http.https://github.com/.extraheader || true
```

This looks defensive and pointless. It's neither.

`actions/checkout@v4` sets a persistent `http.https://github.com/.extraheader` config in the local git repo, carrying `AUTHORIZATION: basic <encoded GITHUB_TOKEN>`. That header is sent on every git request from the runner. When our push step adds a PAT (`AGENT_PUSH_TOKEN`) into the URL, there are now **two competing auths**: the PAT in the URL, and the `GITHUB_TOKEN` in the extraheader. The auth-resolution race is non-deterministic.

When the `GITHUB_TOKEN` wins, the push is effectively `github-actions[bot]`-attributed. GHA's recursive-trigger protection then **silently drops** the resulting `pull_request: synchronize` event — no error, no notification. The downstream build/Sonar workflow simply doesn't run, and the post-fix loop stalls with a stale triage report.

`claude-code-action` clears this header during its own auth dance (which is why Claude pushes worked unaided for a while), but `codex-action` doesn't, so we clear it explicitly. We apply it to both paths for consistency — it's a no-op when the extraheader is already absent. **If a fourth agent gets added, this same pattern is required.**

---

## 3. The workflow listens for two SonarCloud bots, not one

The `if:` filter in `caller-comment-triggered.yml` accepts comments from `sonarqubecloud[bot]` **and** `sonarclouddev<N>[bot]`. Why both?

SonarCloud posts via two distinct GitHub App identities:

- `sonarqubecloud[bot]` posts the **Quality Gate summary**. It only **edits** its summary comment when QG status changes.
- `sonarclouddev<N>[bot]` posts the **reviewer guide**. It posts a fresh comment on every analysis, regardless of QG state.

A single-bot listener that only watches the QG bot stalls in a specific case: a partial fix that addresses some but not all issues leaves QG still failed. The QG bot has no status change to report, so it doesn't edit its comment, so no `issue_comment` event fires, so the workflow never re-runs. The remaining-issues triage report never lands and the loop dies silently after one fix iteration.

Listening for the reviewer-guide bot too keeps the loop alive: it posts on every analysis whether or not QG flipped. Both bots share the same concurrency group, so when both fire within ~3s of each other (the common case), the second supersedes the first via `cancel-in-progress: true` — newer analysis always wins.

---

## 4. Codex's `model_provider` is set via a CLI flag, not the config file

The Codex path writes `$CODEX_HOME/config.toml` with a `[model_providers.gateway]` table, but the active provider is selected via:

```yaml
codex-args: |
  -c model_provider=gateway
```

passed to `openai/codex-action@v1`. Why not just write `model_provider = "gateway"` at the top of `config.toml` like everything else?

Because `codex-action` has its own internal "Write Codex proxy config" step that **prepends a top-level `model_provider = "codex-action-responses-proxy"` line to the file** before `codex exec` runs. If we pre-write our own `model_provider = "gateway"`, the result is two top-level keys with the same name — TOML parse error, run fails.

The `-c` flag overrides at request time. Our `[model_providers.gateway]` table still lives in `config.toml` (the table-form definition doesn't collide), and the `-c` flag points the active provider at it. Action-level mutation of the file is no longer a problem.

---

## 5. `AGENT_PUSH_TOKEN` exists because of GitHub's recursive-trigger protection

The reusable workflow asks for an optional `AGENT_PUSH_TOKEN` PAT used to push the agent's fix commit. Why not just use the default `GITHUB_TOKEN` that's already in the runner?

Because pushes authenticated with `GITHUB_TOKEN` don't trigger downstream workflows. This is a deliberate GHA safety feature — without it, a workflow that pushes a commit could trivially trigger itself in an infinite loop. The downside is that it's exactly what we *want* here: the fix push needs to re-run `build.yml` so SonarCloud re-analyzes, posts a fresh comment, and (if needed) fires another `sonar-fix` iteration.

A user-owned PAT bypasses the protection because the push is attributed to the PAT's owner, not `github-actions[bot]`. The Copilot path doesn't need this token — Copilot pushes via its own GitHub App identity, which is also exempt from the protection.

This is also why item #2 above matters: even with a PAT in the URL, if the extraheader's `GITHUB_TOKEN` wins the auth race, the push gets re-attributed to `github-actions[bot]` and the recursive-trigger protection kicks back in.

---

## 6. The Copilot loop guard counts dispatch comments, not commits

The caller workflow's loop guard works by counting commits on the PR whose message starts with `fix: resolve SonarQube issues`. That prefix is mandated by the agent prompt, and for the Claude and Codex paths it works perfectly — the workflow owns the commit and enforces the prefix.

Copilot runs out-of-band: the workflow posts an `@copilot` comment and Copilot's coding agent applies fixes and pushes commits via its own GitHub App identity using whatever commit message Copilot chooses. Those commits never start with `fix: resolve SonarQube issues`, so the counter stays at zero. The loop guard never trips, and every subsequent SonarCloud bot comment re-dispatches Copilot indefinitely.

The fix: the `count-attempts` step in the caller also counts how many times the workflow has already posted an `@copilot` dispatch comment on this PR (by checking PR comment bodies for the `@copilot Please fix the following SonarQube` marker). The final attempt count is `max(fix_commits, copilot_dispatches)`, so the same `MAX_FIX_ATTEMPTS` cap applies uniformly across all three agent paths.

---

## 7. The agent prompt is injected at run time, not committed to consumer repos

The reusable workflow appends `prompts/sonar-fix-agent.md` to the consumer repo's `AGENTS.md` at the start of every run, then shields the file from being committed back via `git update-index --skip-worktree` (or `.git/info/exclude` if no `AGENTS.md` existed). The Claude and Codex paths read it from the working tree as they normally would; the Copilot path inlines the same file's contents into the `@copilot` comment body.

The naive alternative — have each consumer repo commit its own copy of the prompt — would mean every prompt improvement requires a PR to every consumer repo. Run-time injection makes the central `prompts/sonar-fix-agent.md` the single source of truth: edit one file, every consumer picks it up on their next run, no per-repo update needed.

If a consumer repo already has a project-specific `AGENTS.md`, our content is appended after a separator for the duration of the run only; the file is unchanged outside `sonar-fix` runs.
