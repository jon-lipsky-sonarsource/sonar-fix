# Setting up sonar-fix with OpenAI Codex

This page walks you through installing `sonar-fix` end-to-end using **OpenAI Codex** as the AI coding agent. By the end you'll have:

- A central `sonar-fix` repo in your org
- All required org-level secrets and variables
- A caller workflow on a test repo that fires on `/sonar-fix` comments and on SonarCloud quality-gate comments
- A first successful fix commit on a real PR

If you'd rather use Claude Code or Copilot, see [claude.md](claude.md) or [copilot.md](copilot.md). Each page is self-contained — pick the one for your agent and follow it top to bottom.

## Prerequisites

- A **GitHub organization** where you can create a central repo and grant other repos access to its workflows
- **SonarCloud** (or SonarQube Cloud / Server) already running on your PRs, with the bot posting summary comments
- An **OpenAI API key**, *or* access to an OpenAI-compatible API gateway like Portkey
- A **test repo** with known SonarQube issues you can pilot on

> **Pricing.** Codex is billed per token at OpenAI's standard API rates ([Codex rate card](https://help.openai.com/en/articles/20001106-codex-rate-card) · [Codex pricing](https://developers.openai.com/codex/pricing)). Subscription / ChatGPT plan credits don't apply in API-key CI mode.

---

## Step 1 — Create the central repo

Fork or copy this repository into your org. The recommended name is `sonar-fix`, but anything works.

Then go to **Settings → Actions → General** on the new repo and set "Access" to allow other repos in the org to use workflows and actions from this repo.

## Step 2 — Add org-level secrets

Go to **Organization Settings → Secrets and variables → Actions** → **New organization secret**:

| Secret              | Description                                |
|---------------------|--------------------------------------------|
| `SONAR_TOKEN`       | SonarQube user token. The triage script and (optionally) the scan step use it. |
| `OPENAI_API_KEY`    | OpenAI API key. Skip if you'll only ever route through a virtual-key gateway like Portkey (see Step 4) — the workflow uses a placeholder. Set to your real key for direct OpenAI or for Helicone-style observability proxies that forward to OpenAI. |
| `AGENT_PUSH_TOKEN`  | **Required for the auto-fix loop to keep iterating.** A GitHub PAT used to push the agent's fix commits. Pushes via PAT trigger your build/Sonar workflow on the new commit, which posts a fresh SonarCloud bot comment, which re-fires `sonar-fix`. Without this secret, pushes use the default `GITHUB_TOKEN` — the commit lands on the PR but downstream workflows don't auto-run, so the loop stalls after one fix unless someone re-comments `/sonar-fix`. Classic PAT with `repo` scope, or fine-grained PAT with `Contents: write` + `Pull requests: write`. See [gotchas.md](gotchas.md#5-agent_push_token-exists-because-of-githubs-recursive-trigger-protection) for why this is necessary. |

## Step 3 — Add org-level variables

Same screen → **Variables** tab → **New organization variable**:

| Variable         | Description                             |
|------------------|-----------------------------------------|
| `SONAR_HOST_URL` | Your Sonar host — e.g. `https://sonarcloud.io`, `https://sonarcloud.us`, or a self-hosted SonarQube Server URL. |
| `SONAR_ORG`      | SonarQube Cloud org key. Skip for self-hosted SonarQube Server. |
| `CODEX_MODEL`    | Optional. Codex model identifier (e.g. `gpt-5.5`). Leave unset to let Codex pick its current default. |

> **Auth is API-key only.** Unlike the Claude path, Codex doesn't need a GitHub App or OIDC token exchange — `OPENAI_API_KEY` is the whole authentication story.

## Step 4 — (Optional) Route Codex through an API gateway

If your org accesses OpenAI via Portkey, Helicone, or an internal proxy instead of calling `api.openai.com` directly, see [gateways.md](gateways.md) for the full setup. Skip this step if you're using direct OpenAI.

---

## Step 5 — Install the caller workflow in your test repo

Copy `examples/caller-comment-triggered.yml` from the central repo into your test repo at:

```
your-repo/
└── .github/
    └── workflows/
        └── sonar-fix.yml
```

In `sonar-fix.yml`, replace `my-org` with your org name.

The example caller already declares the `permissions:` block (`contents: write`, `pull-requests: write`, `issues: write`, `id-token: write`) needed for the agent to push commits and post review comments. The `id-token: write` permission isn't strictly required for the Codex path (it's used by `claude-code-action`'s OIDC flow), but the example caller is agent-agnostic so it grants all four — harmless, since unused permissions only authorize jobs that don't run for the current agent. See [gotchas.md](gotchas.md#1-the-caller-workflow-declares-the-same-permissions-as-the-reusable-workflow) for details.

> **Tell the workflow to use Codex.** Make sure your `sonar-fix-config.yml` (or the central default at `config/default.yml`) has `agent: codex` — otherwise the wrong dispatch job will run. See [configuration.md](configuration.md).

> **Optional override.** If you want different rules, severities, or path exclusions than the central default, also create `.github/sonar-fix-config.yml` in your repo (copy from `config/default.yml` in the central repo as a starting point). Without it, the workflow falls back to the central default.

> **No `AGENTS.md` to copy.** The reusable workflow injects its agent prompt into your repo's `AGENTS.md` at run time, shielded from being committed back. See [gotchas.md](gotchas.md#6-the-agent-prompt-is-injected-at-run-time-not-committed-to-consumer-repos).

> **If your build already runs the Sonar scanner**, set `run-sonar-scan: false` in the caller. The triage job will skip the scan step and fetch existing issues straight from the SonarQube API. The example caller already sets this — the comment trigger fires *after* SonarCloud's analysis completes, so re-running the scan would be redundant.

## Step 6 — Add the per-repo variable

In the test repo: **Settings → Secrets and variables → Actions → Variables**:

| Variable            | Description                       |
|---------------------|-----------------------------------|
| `SONAR_PROJECT_KEY` | Your SonarQube project key. |

## Step 7 — Trigger your first fix

1. Pick (or open) a PR on the test repo that has known SonarQube issues
2. Comment **`/sonar-fix`** on the PR

The slash command is gated by `author_association` — only `OWNER`, `MEMBER`, or `COLLABORATOR` can trigger it. This prevents drive-by commenters from running billable agent jobs on public repos.

### What success looks like

In the **Actions** tab of the test repo you should see:

- A workflow run titled **"SonarQube Fix (Comment Triggered)"**
- **Detect Trigger & Resolve PR** — completes, output `trigger=slash-command`
- **Fix / Scan & Triage** — fetches issues from SonarQube, splits them into auto-fix and review-only
- **Fix / Post Triage Comment** — posts a PR comment listing what's queued for auto-fix and what needs human review
- **Fix / Codex Fix** — pulls the SonarQube MCP Docker image, runs the OpenAI Codex CLI in the runner, pushes a commit
- A new commit on the PR with subject **`fix: resolve SonarQube issues (automated)`**

The agent's commit must use that subject prefix exactly — the loop guard counts these to enforce its attempt cap.

If something doesn't work, see [troubleshooting.md](troubleshooting.md).

> **Runaway protection.** Codex has no `--max-turns` equivalent; runaway protection comes from `approval_policy = "never"` in the workflow-generated `config.toml` plus the natural completion of the task. If Codex token cost ever becomes an issue, look at adding `model_auto_compact_token_limit` to the generated `config.toml` in `fix.yml`.

---

## How the workflow gets triggered

Now that the workflow is installed, it fires on:

- **A `/sonar-fix` comment** from a reviewer (gated by `author_association`)
- **A SonarCloud bot comment** after analysis — the workflow listens for both `sonarqubecloud[bot]` (the QG summary bot) and `sonarclouddev<N>[bot]` (the reviewer-guide bot). See [gotchas.md](gotchas.md#3-the-workflow-listens-for-two-sonarcloud-bots-not-one) for why both.

When the agent pushes a fix commit, SonarCloud re-analyzes, the bots post again, and the workflow fires another iteration. The loop terminates when:

- **QG passes** — the next bot trigger sees `qualityGateStatus = passed` and exits without dispatching the agent, or
- **The loop guard trips** — the workflow counts prior commits on the PR whose subject starts with `fix: resolve SonarQube issues`. If that count exceeds `MAX_FIX_ATTEMPTS` (default `3`), bot-triggered runs are skipped. A reviewer commenting `/sonar-fix` always bypasses the cap.

Knobs at the top of `sonar-fix.yml`:

```yaml
env:
  SONAR_BOT_LOGIN: ${{ vars.SONAR_BOT_LOGIN || 'sonarqubecloud[bot]' }}
  MAX_FIX_ATTEMPTS: "3"
  FIX_COMMIT_PREFIX: "fix: resolve SonarQube issues"
```

If your Sonar product uses a different QG bot login than `sonarqubecloud[bot]`, set a repo variable `SONAR_BOT_LOGIN` — both the job filter and the env reference it, so one variable change is enough.

The workflow uses `concurrency: cancel-in-progress: true` keyed on PR number — if a new comment arrives while a previous run is in flight, the previous run is cancelled. Newer Sonar state always wins.

---

## Roll out to more repos

Once the test repo is humming through both manual and automatic runs:

1. **Copy `sonar-fix.yml`** to each additional repo and edit `my-org` to match your org.
2. **Add `SONAR_PROJECT_KEY`** as a repo variable in each new repo.
3. **(Optional) Override the central config** per repo by creating `.github/sonar-fix-config.yml`.

The agent prompt and default config live centrally, so most repos only need steps 1 and 2 — one file and one variable to opt in.

---

## Where to go next

- [configuration.md](configuration.md) — `sonar-fix-config.yml` schema for filtering which issues get auto-fixed
- [gateways.md](gateways.md) — routing Codex through Portkey, Helicone, or an internal proxy
- [gotchas.md](gotchas.md) — non-obvious design choices, useful if you fork
- [architecture.md](architecture.md) — how the reusable workflow is structured internally
- [troubleshooting.md](troubleshooting.md) — common misconfigurations and their fixes
