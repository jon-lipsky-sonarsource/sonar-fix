# Setting up sonar-fix with GitHub Copilot

This page walks you through installing `sonar-fix` end-to-end using **GitHub Copilot**'s coding agent. By the end you'll have:

- A central `sonar-fix` repo in your org
- All required org-level secrets and variables
- Copilot's coding agent configured per-repo with the SonarQube MCP server
- A caller workflow on a test repo that fires on `/sonar-fix` comments and on SonarCloud quality-gate comments
- A first successful fix commit on a real PR

If you'd rather use Claude Code or Codex, see [claude.md](claude.md) or [codex.md](codex.md). Each page is self-contained — pick the one for your agent and follow it top to bottom.

> **Heads up — Copilot setup has more per-repo manual steps than the other agents.** Unlike Claude and Codex, which run inside the GitHub Actions runner with the SonarQube MCP server attached as a Docker sidecar, Copilot's coding agent runs out-of-band on GitHub's infrastructure. That means MCP server configuration is a per-repo UI step on github.com — there's no API or file-based mechanism for it.

## Prerequisites

- A **GitHub organization** with **GitHub Copilot Business or Enterprise** enabled (the coding agent is a Copilot feature)
- **SonarCloud** (or SonarQube Cloud / Server) already running on your PRs, with the bot posting summary comments
- A **classic PAT with `repo` scope** from a Copilot-licensed user — used to post `@copilot` comments that wake the coding agent
- A **test repo** with known SonarQube issues you can pilot on

---

## Step 1 — Create the central repo

Fork or copy this repository into your org. The recommended name is `sonar-fix`, but anything works.

Then go to **Settings → Actions → General** on the new repo and set "Access" to allow other repos in the org to use workflows and actions from this repo.

## Step 2 — Add org-level secrets (Actions)

Go to **Organization Settings → Secrets and variables → Actions** → **New organization secret**:

| Secret                      | Description                                |
|-----------------------------|--------------------------------------------|
| `COPILOT_MCP_SONAR_TOKEN`   | SonarQube user token. The `COPILOT_MCP_` prefix is mandated by GitHub — Copilot's MCP config can only see secrets prefixed this way. The workflow's triage/scan steps fall back to this when the unprefixed `SONAR_TOKEN` is unset, so a Copilot setup uses one secret name everywhere. |
| `COPILOT_PAT`               | Classic PAT with `repo` scope from a Copilot subscriber. Used by the workflow to post the `@copilot` comment that triggers the coding agent. |

> **Why no `AGENT_PUSH_TOKEN`?** Copilot pushes its fix commit via its own GitHub App identity, which bypasses GHA's recursive-trigger protection — downstream workflows fire automatically without a user-owned PAT. The Claude and Codex paths need `AGENT_PUSH_TOKEN` because their pushes come from `github-actions[bot]`. See [gotchas.md](gotchas.md#5-agent_push_token-exists-because-of-githubs-recursive-trigger-protection).

## Step 3 — Add org-level variables (Actions)

Same screen → **Variables** tab → **New organization variable**:

| Variable                      | Description                             |
|-------------------------------|-----------------------------------------|
| `COPILOT_MCP_SONAR_HOST_URL`  | Your Sonar host — e.g. `https://sonarcloud.io`, `https://sonarcloud.us`, or a self-hosted SonarQube Server URL. The workflow falls back to this when the unprefixed `SONAR_HOST_URL` is unset. |
| `COPILOT_MCP_SONAR_ORG`       | SonarQube Cloud org key. Skip for self-hosted SonarQube Server. The workflow falls back to this when the unprefixed `SONAR_ORG` is unset. |

---

## Step 4 — Install the caller workflow in your test repo

Copy `examples/caller-comment-triggered.yml` from the central repo into your test repo at:

```
your-repo/
└── .github/
    └── workflows/
        └── sonar-fix.yml
```

In `sonar-fix.yml`, replace `my-org` with your org name.

The example caller already declares the `permissions:` block (`contents: write`, `pull-requests: write`, `issues: write`, `id-token: write`) needed for the workflow to post the `@copilot` comment and review comments. Some of these (`contents: write`, `id-token: write`) aren't strictly required for the Copilot path, but the example caller is agent-agnostic so it grants all four — harmless, since unused permissions only authorize jobs that don't run for the current agent. See [gotchas.md](gotchas.md#1-the-caller-workflow-declares-the-same-permissions-as-the-reusable-workflow) for details.

> **Tell the workflow to use Copilot.** Make sure your `sonar-fix-config.yml` (or the central default at `config/default.yml`) has `agent: copilot` — otherwise the wrong dispatch job will run. See [configuration.md](configuration.md).

> **Optional override.** If you want different rules, severities, or path exclusions than the central default, also create `.github/sonar-fix-config.yml` in your repo (copy from `config/default.yml` in the central repo as a starting point). Without it, the workflow falls back to the central default.

> **No `AGENTS.md` to copy.** The reusable workflow inlines the agent prompt into the `@copilot` comment body alongside the issue list at run time. There's nothing to commit to your repo. See [gotchas.md](gotchas.md#6-the-agent-prompt-is-injected-at-run-time-not-committed-to-consumer-repos).

## Step 5 — Configure Copilot's coding agent on the test repo

This step is **per-repo** and must be repeated for each repo you roll `sonar-fix` out to (Step 9).

### 5a. Configure the SonarQube MCP server

In the test repo: **Settings → Code & automation → Copilot → Coding agent → MCP configuration**.

Paste the JSON from `examples/copilot-mcp-setup.json` (in the central repo) into the MCP configuration field. The contents wire `mcp/sonarqube` (a Docker image) to the env vars Copilot will provide:

```json
{
  "mcpServers": {
    "sonarqube": {
      "type": "local",
      "command": "docker",
      "args": [
        "run", "--rm", "-i", "--init", "--pull=always",
        "-e", "SONARQUBE_TOKEN=$SONAR_TOKEN",
        "-e", "SONARQUBE_ORG=$SONAR_ORG",
        "-e", "SONARQUBE_URL=$SONAR_HOST_URL"
      ],
      "env": {
        "SONAR_TOKEN": "COPILOT_MCP_SONAR_TOKEN",
        "SONAR_ORG": "COPILOT_MCP_SONAR_ORG",
        "SONAR_HOST_URL": "COPILOT_MCP_SONAR_HOST_URL"
      },
      "tools": ["*"]
    }
  }
}
```

### 5b. Set Copilot environment secrets

The MCP config above references variable names like `COPILOT_MCP_SONAR_TOKEN`. These resolve from **Copilot's environment**, which is a different scope than Actions secrets. Set them at: **Settings → Code & automation → Copilot → Coding agent → Environment**.

| Name                          | Type       | Value                                           |
|-------------------------------|------------|-------------------------------------------------|
| `COPILOT_MCP_SONAR_TOKEN`     | Secret     | Same SonarQube token you used in Step 2         |
| `COPILOT_MCP_SONAR_HOST_URL`  | Variable   | Same Sonar host URL you used in Step 3          |
| `COPILOT_MCP_SONAR_ORG`       | Variable   | Same SonarQube Cloud org key from Step 3        |

> **Yes, the same names are set in two places** — once at the org level for Actions (Step 2/3) and once per-repo for Copilot's environment (here). GitHub's design intentionally isolates Copilot's secrets from the Actions runner. The matching names just save you from inventing two parallel naming schemes.

### 5c. Allow Copilot's workflow runs to skip approval

Same Coding-agent settings page → uncheck **"Require approval for workflow runs"**.

Without this flip, GitHub treats every Copilot push/comment like an outside-contributor event and queues the downstream `build.yml` and `sonar-fix.yml` runs in `action_required` state. The fix commit lands on the PR, but the loop stalls waiting for a human to click "Approve and run."

The trade-off (per the [March 2026 changelog](https://github.blog/changelog/2026-03-13-optionally-skip-approval-for-copilot-coding-agent-actions-workflows/)): Copilot's runs can use secrets and consume Actions minutes without manual approval. That's the whole point for an auto-fix loop, but you're trading approval-time security for autonomy. Decide whether that's acceptable for your org.

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

The Copilot path is **two-stage** — the workflow posts an `@copilot` comment, then Copilot's coding agent runs out-of-band and pushes commits itself.

In the **Actions** tab of the test repo you should see:

- A workflow run titled **"SonarQube Fix (Comment Triggered)"**
- **Detect Trigger & Resolve PR** — completes, output `trigger=slash-command`
- **Fix / Scan & Triage** — fetches issues from SonarQube, splits them into auto-fix and review-only
- **Fix / Post Triage Comment** — posts a PR comment listing what's queued for auto-fix and what needs human review
- **Fix / Copilot Fix** — posts the `@copilot` comment with the issue list and agent prompt, then exits

Then, **separately**, on the PR's timeline:
- An `@copilot` mention from your `COPILOT_PAT` user
- Copilot's coding agent picks it up (visible as a Copilot session in the PR sidebar) and pushes a fix commit with subject **`fix: resolve SonarQube issues (automated)`**

The agent's commit must use that subject prefix exactly — the loop guard counts these to enforce its attempt cap.

If something doesn't work, see [troubleshooting.md](troubleshooting.md).

---

## How the workflow gets triggered

Now that the workflow is installed, it fires on:

- **A `/sonar-fix` comment** from a reviewer (gated by `author_association`)
- **A SonarCloud bot comment** after analysis — the workflow listens for both `sonarqubecloud[bot]` (the QG summary bot) and `sonarclouddev<N>[bot]` (the reviewer-guide bot). See [gotchas.md](gotchas.md#3-the-workflow-listens-for-two-sonarcloud-bots-not-one) for why both.

When Copilot pushes a fix commit, SonarCloud re-analyzes, the bots post again, and the workflow fires another iteration. The loop terminates when:

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
3. **Repeat Step 5** for each new repo — MCP config, Copilot environment secrets, and the workflow-approval flip are all per-repo.
4. **(Optional) Override the central config** per repo by creating `.github/sonar-fix-config.yml`.

The Copilot per-repo Step 5 is the most labor-intensive part of rollout — there's no API for it, so it has to be done in the github.com UI on each repo.

---

## Where to go next

- [configuration.md](configuration.md) — `sonar-fix-config.yml` schema for filtering which issues get auto-fixed
- [gotchas.md](gotchas.md) — non-obvious design choices, useful if you fork
- [architecture.md](architecture.md) — how the reusable workflow is structured internally
- [troubleshooting.md](troubleshooting.md) — common misconfigurations and their fixes

> **Note.** API gateway routing (Portkey, Helicone, etc.) doesn't apply to the Copilot path — Copilot's coding agent is GitHub-hosted and uses GitHub's billing relationship with its underlying model providers. There's no per-call HTTP endpoint to redirect. If your org needs gateway routing, use [claude.md](claude.md) or [codex.md](codex.md) instead.
