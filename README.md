# sonar-fix

Org-wide reusable workflows and actions for automatically fixing SonarQube issues on pull requests using AI coding agents.

## Overview

When a PR is opened, these workflows:

1. **Scan** the PR with SonarQube
2. **Triage** the issues against your repo's fix config (which severities, types, and rules to fix vs. flag)
3. **Dispatch** eligible issues to an AI agent (Claude Code or GitHub Copilot) for automatic fixing
4. **Comment** on the PR with issues that need human review

```
Calling Repo                          Central Repo (this repo)
┌─────────────────────┐               ┌──────────────────────────┐
│ .github/workflows/  │               │ .github/workflows/       │
│   sonar-fix.yml     │──── calls ───▶│   path2-claude-fix.yml   │
│                     │               │   path3-copilot-fix.yml  │
│ .github/            │               │                          │
│   sonar-fix-        │               │ triage-action/           │
│     config.yml      │               │   action.yml             │
└─────────────────────┘               │   triage_sonar_issues.py │
                                      │                          │
                                      │ examples/                │
                                      │   (starter files)        │
                                      └──────────────────────────┘
```

## Quick Start (Test on one repo first)

### 1. Set up this central repo

Create a repo in your org called `sonar-fix` (or any name). Push the contents of this repository to it. Under **Settings > Actions > General**, set "Access" to allow other repos in the org to use workflows and actions from this repo.

### 2. Set up secrets at the org level

Go to **Organization Settings > Secrets and variables > Actions** and create:

| Secret              | Required By | Description                                |
|---------------------|-------------|--------------------------------------------|
| `SONAR_TOKEN`       | Both paths  | SonarQube user token                       |
| `ANTHROPIC_API_KEY` | Path 2      | Anthropic API key for Claude Code          |
| `COPILOT_PAT`       | Path 3      | GitHub PAT (classic, `repo` scope) from a Copilot subscriber |

Also create org-level **variables**:

| Variable            | Description                             |
|---------------------|-----------------------------------------|
| `SONAR_HOST_URL`    | e.g. `https://sonarcloud.io`            |
| `SONAR_ORG`         | SonarQube Cloud org key (if applicable) |

### 3. Add to your test repo

Copy these files from `examples/` into your test repo:

```
your-repo/
├── .github/
│   ├── workflows/
│   │   └── sonar-fix.yml          ← from examples/caller-path2-claude.yml
│   └── sonar-fix-config.yml       ← from examples/sonar-fix-config.yml
└── AGENTS.md                          ← from examples/AGENTS.md (cross-agent)
```

Edit `sonar-fix.yml` to replace `my-org` with your actual org name.

Add a **repo-level variable** `SONAR_PROJECT_KEY` with your SonarQube project key.

### 4. Open a PR and watch it work

The workflow will trigger on `pull_request` events. Check the Actions tab to see the scan, triage, and fix jobs.

### 5. Roll out to more repos

Once you're happy with the results:

- Tag this repo (`git tag v1 && git push --tags`) so consuming repos pin to a stable version
- Copy the caller workflow and config to additional repos
- Customize `sonar-fix-config.yml` per-repo as needed (different rules, severities, etc.)
- Or keep the config identical across repos by not including it and relying on defaults

## Available Workflows

### Path 2: Claude Code Action

**File:** `.github/workflows/path2-claude-fix.yml`

Claude Code runs directly in the GitHub Actions runner with the SonarQube MCP server as a Docker sidecar. It reads files, queries SonarQube for rule details, applies fixes, and pushes commits.

**Inputs:**

| Input               | Required | Default                  | Description                    |
|---------------------|----------|--------------------------|--------------------------------|
| `sonar-project-key` | Yes      | —                        | SonarQube project key          |
| `sonar-org`         | No       | `""`                     | SonarQube Cloud org key        |
| `config-path`       | No       | `.github/sonar-fix-config.yml` | Path to fix config |
| `sonar-host-url`    | No       | `https://sonarcloud.io`  | SonarQube host URL             |
| `claude-model`      | No       | `claude-sonnet-4-6`      | Claude model to use            |
| `run-sonar-scan`    | No       | `true`                   | Set `false` if scan runs elsewhere |

**Secrets:** `SONAR_TOKEN`, `ANTHROPIC_API_KEY`

### Path 3: Copilot Coding Agent

**File:** `.github/workflows/path3-copilot-fix.yml`

Posts an `@copilot` comment on the PR with the triaged issues. Copilot picks it up and pushes fixes.

**Inputs:**

| Input               | Required | Default                  | Description                    |
|---------------------|----------|--------------------------|--------------------------------|
| `sonar-project-key` | Yes      | —                        | SonarQube project key          |
| `config-path`       | No       | `.github/sonar-fix-config.yml` | Path to fix config |
| `sonar-host-url`    | No       | `https://sonarcloud.io`  | SonarQube host URL             |
| `run-sonar-scan`    | No       | `true`                   | Set `false` if scan runs elsewhere |

**Secrets:** `SONAR_TOKEN`, `COPILOT_PAT`

**Additional setup:** Each consuming repo must have the SonarQube MCP server configured in Copilot's settings. See `examples/copilot-mcp-setup.json`.

## Configuration

Each consuming repo has its own `.github/sonar-fix-config.yml` that controls:

- **`agent`** — `claude`, `copilot`, or `both`
- **`auto_fix.severities`** — Which severities to fix
- **`auto_fix.types`** — Which issue types (BUG, CODE_SMELL, etc.)
- **`auto_fix.rules.allow`** — Rules always fixed
- **`auto_fix.rules.deny`** — Rules never fixed (security-sensitive, etc.)
- **`paths.exclude`** — File globs to skip
- **`guardrails.max_issues_per_run`** — Cost cap
- **`guardrails.max_turns`** — Agent iteration cap

See `examples/sonar-fix-config.yml` for a fully annotated starter config.

## Architecture

```
                  ┌─────────────────────────────────┐
                  │       Calling Repo PR Event      │
                  └──────────────┬──────────────────┘
                                 │
                  ┌──────────────▼──────────────────┐
                  │  Job 1: sonar-scan-and-triage    │
                  │  ┌─────────────────────────────┐ │
                  │  │ SonarQube Scan Action        │ │
                  │  └─────────────┬───────────────┘ │
                  │  ┌─────────────▼───────────────┐ │
                  │  │ Triage Composite Action      │ │
                  │  │ (from central repo)          │ │
                  │  │ - Fetches issues via API     │ │
                  │  │ - Applies config filters     │ │
                  │  │ - Outputs categorized JSON   │ │
                  │  └─────────────────────────────┘ │
                  └──────┬───────────────┬──────────┘
                         │               │
          ┌──────────────▼───┐   ┌───────▼──────────────┐
          │ Job 2: Review    │   │ Job 3: Fix      │
          │ Comments         │   │                      │
          │ (PR comment with │   │ Path 2: Claude Code  │
          │  human-review    │   │   runs in-runner     │
          │  issues)         │   │   with MCP sidecar   │
          │                  │   │                      │
          │                  │   │ Path 3: @copilot     │
          │                  │   │   comment triggers   │
          │                  │   │   coding agent       │
          └──────────────────┘   └──────────────────────┘
```

## Versioning

Tag releases on this repo (`v1`, `v1.1`, etc.). Consuming repos reference a tag:

```yaml
uses: my-org/sonar-fix/.github/workflows/path2-claude-fix.yml@v1
```

Use `@main` during development, pin to tags for production.

## If Your SonarQube Scan Already Runs Separately

Set `run-sonar-scan: false` in the caller workflow. The triage job will skip the scan step and just fetch existing issues from SonarQube.

```yaml
uses: my-org/sonar-fix/.github/workflows/path2-claude-fix.yml@v1
with:
  sonar-project-key: ${{ vars.SONAR_PROJECT_KEY }}
  run-sonar-scan: false
secrets: inherit
```
