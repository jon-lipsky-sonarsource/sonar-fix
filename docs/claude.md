# Setting up sonar-fix with Claude Code

This page walks you through installing `sonar-fix` end-to-end using **Claude Code** as the AI coding agent. By the end you'll have:

- A central `sonar-fix` repo in your org
- All required org-level secrets and variables
- The Claude Code GitHub App installed
- A caller workflow on a test repo that fires on `/sonar-fix` comments and on SonarCloud quality-gate comments
- A first successful fix commit on a real PR

If you'd rather use Copilot or Codex, see [copilot.md](copilot.md) or [codex.md](codex.md). Each page is self-contained — pick the one for your agent and follow it top to bottom.

## Prerequisites

- A **GitHub organization** where you can create a central repo and grant other repos access to its workflows
- **SonarCloud** (or SonarQube Cloud / Server) already running on your PRs, with the bot posting summary comments
- An **Anthropic API key**, *or* access to a Claude-compatible API gateway like Portkey
- A **test repo** with known SonarQube issues you can pilot on

---

## Step 1 — Create the central repo

Fork or copy this repository into your org. The recommended name is `sonar-fix`, but anything works.

Then go to **Settings → Actions → General** on the new repo and set "Access" to allow other repos in the org to use workflows and actions from this repo.

## Step 2 — Add org-level secrets

Go to **Organization Settings → Secrets and variables → Actions** → **New organization secret**:

| Secret              | Description                                |
|---------------------|--------------------------------------------|
| `SONAR_TOKEN`       | SonarQube user token. The triage script and (optionally) the scan step use it. |
| `ANTHROPIC_API_KEY` | Anthropic API key. Skip if you'll only ever route through a virtual-key gateway like Portkey (see Step 5) — the workflow uses a placeholder. Set to your real key for direct Anthropic or for Helicone-style observability proxies that forward to Anthropic. |
| `AGENT_PUSH_TOKEN`  | **Required for the auto-fix loop to keep iterating.** A GitHub PAT used to push the agent's fix commits. Pushes via PAT trigger your build/Sonar workflow on the new commit, which posts a fresh SonarCloud bot comment, which re-fires `sonar-fix`. Without this secret, pushes use the default `GITHUB_TOKEN` — the commit lands on the PR but downstream workflows don't auto-run, so the loop stalls after one fix unless someone re-comments `/sonar-fix`. Classic PAT with `repo` scope, or fine-grained PAT with `Contents: write` + `Pull requests: write`. See [gotchas.md](gotchas.md#5-agent_push_token-exists-because-of-githubs-recursive-trigger-protection) for why this is necessary. |

## Step 3 — Add org-level variables

Same screen → **Variables** tab → **New organization variable**:

| Variable         | Description                             |
|------------------|-----------------------------------------|
| `SONAR_HOST_URL` | Your Sonar host — e.g. `https://sonarcloud.io`, `https://sonarcloud.us`, or a self-hosted SonarQube Server URL. |
| `SONAR_ORG`      | SonarQube Cloud org key. Skip for self-hosted SonarQube Server. |

## Step 4 — Install the Claude Code GitHub App

The `anthropics/claude-code-action@v1` action exchanges a GitHub OIDC token for a Claude-issued app token before each run, regardless of whether you authenticate via direct API key, OAuth, or a proxy. That exchange requires the **Claude Code GitHub App** to be installed on the repo (or on the org/user account, with access granted to the relevant repos).

1. Go to **<https://github.com/apps/claude>** → **Install**
2. Choose your account (the one that owns the consuming repos)
3. Either grant access to **All repositories** (recommended for org-wide rollout) or pick the specific repos you'll install `sonar-fix` into
4. Confirm

The App is free and only grants the minimum access `claude-code-action` needs. There's no recurring cost or billing relationship — your actual Anthropic billing continues via your `ANTHROPIC_API_KEY` (or via your gateway in Step 5).

## Step 5 — (Optional) Route Claude through an API gateway

If your org accesses Claude via Portkey, Helicone, or an internal proxy instead of calling `api.anthropic.com` directly, see [gateways.md](gateways.md) for the full setup. Skip this step if you're using direct Anthropic.

---

## Step 6 — Install the caller workflow in your test repo

Copy `examples/caller-comment-triggered.yml` from the central repo into your test repo at:

```
your-repo/
└── .github/
    └── workflows/
        └── sonar-fix.yml
```

In `sonar-fix.yml`, replace `my-org` with your org name.

The example caller already declares the `permissions:` block (`contents: write`, `pull-requests: write`, `issues: write`, `id-token: write`) needed for the agent to push commits, post review comments, and obtain the OIDC token used by `claude-code-action`. You don't need to flip the repo-level "Default workflow permissions" setting — the caller grants write per-workflow. See [gotchas.md](gotchas.md#1-the-caller-workflow-declares-the-same-permissions-as-the-reusable-workflow) for why these are duplicated.

> **Optional override.** If you want different rules, severities, or path exclusions than the central default, also create `.github/sonar-fix-config.yml` in your repo (copy from `config/default.yml` in the central repo as a starting point). Without it, the workflow falls back to the central default — most repos won't need to override. See [configuration.md](configuration.md).

> **No `AGENTS.md` to copy.** The reusable workflow injects its agent prompt into your repo's `AGENTS.md` at run time, shielded from being committed back. See [gotchas.md](gotchas.md#6-the-agent-prompt-is-injected-at-run-time-not-committed-to-consumer-repos).

> **If your build already runs the Sonar scanner**, set `run-sonar-scan: false` in the caller. The triage job will skip the scan step and fetch existing issues straight from the SonarQube API. The example caller already sets this — the comment trigger fires *after* SonarCloud's analysis completes, so re-running the scan would be redundant.

## Step 7 — Add the per-repo variable

In the test repo: **Settings → Secrets and variables → Actions → Variables**:

| Variable            | Description                       |
|---------------------|-----------------------------------|
| `SONAR_PROJECT_KEY` | Your SonarQube project key. |

## Step 8 — Trigger your first fix

1. Pick (or open) a PR on the test repo that has known SonarQube issues
2. Comment **`/sonar-fix`** on the PR

The slash command is gated by `author_association` — only `OWNER`, `MEMBER`, or `COLLABORATOR` can trigger it. This prevents drive-by commenters from running billable agent jobs on public repos.

### What success looks like

In the **Actions** tab of the test repo you should see:

- A workflow run titled **"SonarQube Fix (Comment Triggered)"**
- **Detect Trigger & Resolve PR** — completes, output `trigger=slash-command`
- **Fix / Scan & Triage** — fetches issues from SonarQube, splits them into auto-fix and review-only
- **Fix / Post Triage Comment** — posts a PR comment listing what's queued for auto-fix and what needs human review
- **Fix / Claude Fix** — pulls the SonarQube MCP Docker image, runs the agent, pushes a commit
- A new commit on the PR with subject **`fix: resolve SonarQube issues (automated)`**

The agent's commit must use that subject prefix exactly — the loop guard counts these to enforce its attempt cap.

If something doesn't work, see [troubleshooting.md](troubleshooting.md).

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

1. **Tag a release** on the central repo: `git tag v1 && git push --tags`. Have consuming repos pin to the tag so future changes don't break them: `uses: my-org/sonar-fix/.github/workflows/fix.yml@v1`.
2. **Copy `sonar-fix.yml`** to each additional repo and edit `my-org` to match your org.
3. **Add `SONAR_PROJECT_KEY`** as a repo variable in each new repo.
4. **(Optional) Override the central config** per repo by creating `.github/sonar-fix-config.yml`.

The agent prompt and default config live centrally, so most repos only need steps 2 and 3 — one file and one variable to opt in.

---

## Where to go next

- [configuration.md](configuration.md) — `sonar-fix-config.yml` schema for filtering which issues get auto-fixed
- [gateways.md](gateways.md) — routing Claude through Portkey, Helicone, or an internal proxy
- [gotchas.md](gotchas.md) — non-obvious design choices, useful if you fork
- [architecture.md](architecture.md) — how the reusable workflow is structured internally
- [troubleshooting.md](troubleshooting.md) — common misconfigurations and their fixes
