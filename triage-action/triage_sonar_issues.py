#!/usr/bin/env python3
"""
SonarQube Issue Triage Script
==============================
Fetches issues from SonarQube for a given PR branch, categorizes them
against the fix configuration, and outputs structured JSON for
downstream agent dispatch.

Usage (in GitHub Actions):
    python3 .github/scripts/triage_sonar_issues.py \
        --config .github/sonar-fix-config.yml \
        --project-key my-org_my-repo \
        --branch feature/my-branch \
        --pr-number 42

Environment variables:
    SONAR_TOKEN      - SonarQube API token
    SONAR_HOST_URL   - SonarQube server URL (e.g. https://sonarcloud.io)
    GITHUB_OUTPUT    - GitHub Actions output file path
"""

import argparse
import fnmatch
import json
import os
import sys
import time
import urllib.request
import urllib.error
import yaml


def deep_merge(base: dict, override: dict) -> dict:
    """
    Recursively merge `override` into `base`. Nested dicts are merged
    key-by-key; everything else (lists, scalars) is replaced wholesale
    by the override. So a consumer can override `auto_fix.severities`
    without having to also redeclare `auto_fix.types`, but if they do
    set `severities`, their list replaces the base's list (rather than
    being appended to it).
    """
    result = dict(base)
    for key, value in (override or {}).items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


VALID_AGENTS = {"claude", "copilot", "codex"}


def validate_agent(raw: object) -> str:
    """
    Validate the `agent` field. Must be a single token, one of
    {claude, copilot, codex}.
    """
    if not isinstance(raw, str) or not raw.strip():
        print(
            "::error::No agent specified — set `agent:` in your "
            f"sonar-fix-config.yml to one of {sorted(VALID_AGENTS)}.",
            file=sys.stderr,
        )
        sys.exit(1)

    agent = raw.strip().lower()
    if agent not in VALID_AGENTS:
        print(
            f"::error::Unknown agent {raw!r} — must be one of "
            f"{sorted(VALID_AGENTS)}.",
            file=sys.stderr,
        )
        sys.exit(1)

    return agent


def load_config(config_path: str, overrides_path: str | None = None) -> dict:
    """
    Load fix configuration. `config_path` is the base (typically the
    central default at config/default.yml in the sonar-fix repo);
    `overrides_path`, if provided AND the file exists, is the consumer
    repo's per-repo override deep-merged on top.
    """
    with open(config_path, "r") as f:
        config = yaml.safe_load(f) or {}

    if overrides_path and os.path.exists(overrides_path):
        with open(overrides_path, "r") as f:
            overrides = yaml.safe_load(f) or {}
        config = deep_merge(config, overrides)

    # Backstop defaults for keys still missing after the merge. These
    # only fire if NEITHER the base nor the override declare the key,
    # which shouldn't happen with the shipped central default — kept
    # as a safety net so a malformed central config doesn't crash.
    config.setdefault("agent", "claude")
    config["agent"] = validate_agent(config["agent"])
    config.setdefault("auto_fix", {})
    config["auto_fix"].setdefault("severities", ["BLOCKER", "CRITICAL"])
    config["auto_fix"].setdefault("types", ["BUG", "CODE_SMELL"])
    config["auto_fix"].setdefault("rules", {})
    config["auto_fix"]["rules"].setdefault("allow", [])
    config["auto_fix"]["rules"].setdefault("deny", [])
    config.setdefault("paths", {})
    config["paths"].setdefault("exclude", [])
    config.setdefault("review_only", {})
    config["review_only"].setdefault("severities", ["MINOR", "INFO"])
    config["review_only"].setdefault("types", ["VULNERABILITY", "SECURITY_HOTSPOT"])
    config.setdefault("guardrails", {})
    config["guardrails"].setdefault("max_issues_per_run", 15)
    config["guardrails"].setdefault("max_iterations", 3)
    config["guardrails"].setdefault("max_turns", 25)
    config["guardrails"].setdefault("run_tests_after_fix", False)
    config["guardrails"].setdefault("verify_fixes", False)

    return config


def fetch_issues(
    host_url: str,
    token: str,
    project_key: str,
    branch: str,
    pull_request: str | None = None,
) -> list[dict]:
    """
    Fetch new issues from SonarQube for a branch or pull request.

    Uses the api/issues/search endpoint. For PRs in SonarCloud, the
    pullRequest parameter filters to issues introduced in that PR.
    """
    base_url = host_url.rstrip("/")
    issues = []
    page = 1
    page_size = 100

    while True:
        params = {
            "componentKeys": project_key,
            "resolved": "false",
            "statuses": "OPEN,CONFIRMED,REOPENED",
            "ps": str(page_size),
            "p": str(page),
        }

        # Use pullRequest param if available (SonarCloud), otherwise branch
        if pull_request:
            params["pullRequest"] = pull_request
        elif branch:
            params["branch"] = branch

        query_string = "&".join(f"{k}={urllib.parse.quote(v)}" for k, v in params.items())
        url = f"{base_url}/api/issues/search?{query_string}"

        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            print(f"::error::SonarQube API returned HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
            sys.exit(1)
        except urllib.error.URLError as e:
            print(f"::error::Failed to connect to SonarQube: {e.reason}", file=sys.stderr)
            sys.exit(1)

        issues.extend(data.get("issues", []))

        total = data.get("total", 0)
        if page * page_size >= total:
            break
        page += 1

    return issues


def matches_glob_list(filepath: str, patterns: list[str]) -> bool:
    """Check if a filepath matches any of the given glob patterns."""
    for pattern in patterns:
        if fnmatch.fnmatch(filepath, pattern):
            return True
    return False


def is_rule_denied(rule_key: str, issue_type: str, deny_list: list[str]) -> bool:
    """Check if a rule is in the deny list, including wildcard patterns."""
    for pattern in deny_list:
        # Exact match
        if rule_key == pattern:
            return True
        # Type-based wildcard (e.g., "SECURITY_HOTSPOT:*")
        if ":" in pattern:
            ptype, prule = pattern.split(":", 1)
            if ptype == issue_type and fnmatch.fnmatch(rule_key, f"*:{prule}"):
                return True
        # Glob match on rule key
        if fnmatch.fnmatch(rule_key, pattern):
            return True
    return False


def is_rule_allowed(rule_key: str, allow_list: list[str]) -> bool:
    """Check if a rule is explicitly in the allow list."""
    for pattern in allow_list:
        if rule_key == pattern or fnmatch.fnmatch(rule_key, pattern):
            return True
    return False


def categorize_issue(issue: dict, config: dict) -> str:
    """
    Categorize an issue as 'auto_fix', 'review_only', or 'skip'.

    Priority:
    1. Deny list → always review_only
    2. Allow list → always auto_fix (unless path-excluded)
    3. Path exclusions → review_only
    4. Severity + type match → auto_fix
    5. Everything else → review_only
    """
    rule_key = issue.get("rule", "")
    severity = issue.get("severity", "")
    issue_type = issue.get("type", "")
    component = issue.get("component", "")

    # Extract file path from component (format: "project-key:src/main/Foo.java")
    filepath = component.split(":", 1)[-1] if ":" in component else component

    auto_fix_cfg = config["auto_fix"]
    rules_cfg = auto_fix_cfg["rules"]

    # 1. Deny list always wins
    if is_rule_denied(rule_key, issue_type, rules_cfg["deny"]):
        return "review_only"

    # 2. Allow list overrides severity/type (but not path exclusions)
    explicitly_allowed = is_rule_allowed(rule_key, rules_cfg["allow"])

    # 3. Path exclusions
    if matches_glob_list(filepath, config["paths"]["exclude"]):
        return "review_only"

    # 4. If explicitly allowed, fix
    if explicitly_allowed:
        return "auto_fix"

    # 5. Severity + type match
    if severity in auto_fix_cfg["severities"] and issue_type in auto_fix_cfg["types"]:
        return "auto_fix"

    # 6. Default to review-only
    return "review_only"


def format_issue(issue: dict) -> dict:
    """Extract the fields we need for downstream consumption."""
    component = issue.get("component", "")
    filepath = component.split(":", 1)[-1] if ":" in component else component

    return {
        "key": issue.get("key", ""),
        "rule": issue.get("rule", ""),
        "severity": issue.get("severity", ""),
        "type": issue.get("type", ""),
        "message": issue.get("message", ""),
        "component": filepath,
        "line": issue.get("line"),
        "text_range": issue.get("textRange"),
        "effort": issue.get("effort", ""),
        "tags": issue.get("tags", []),
    }


def main():
    parser = argparse.ArgumentParser(description="Triage SonarQube issues for fix")
    parser.add_argument("--config", required=True, help="Path to base sonar-fix-config.yml (typically the central default)")
    parser.add_argument(
        "--overrides",
        required=False,
        default="",
        help="Optional path to a per-repo sonar-fix-config.yml whose keys "
             "are deep-merged on top of --config. Missing/non-existent path "
             "is treated as 'no overrides'.",
    )
    parser.add_argument("--project-key", required=True, help="SonarQube project key")
    parser.add_argument("--branch", required=False, help="Branch name")
    parser.add_argument("--pr-number", required=False, help="PR number (for SonarCloud PR analysis)")
    args = parser.parse_args()

    # Load environment
    token = os.environ.get("SONAR_TOKEN")
    host_url = os.environ.get("SONAR_HOST_URL", "https://sonarcloud.io")

    if not token:
        print("::error::SONAR_TOKEN environment variable is required", file=sys.stderr)
        sys.exit(1)

    # Load config
    config = load_config(args.config, args.overrides or None)

    # Fetch issues
    print(f"Fetching issues for project={args.project_key}, branch={args.branch}, pr={args.pr_number}")
    issues = fetch_issues(host_url, token, args.project_key, args.branch, args.pr_number)
    print(f"Found {len(issues)} open issues")

    # Categorize
    auto_fix_issues = []
    review_only_issues = []

    for issue in issues:
        category = categorize_issue(issue, config)
        formatted = format_issue(issue)

        if category == "auto_fix":
            auto_fix_issues.append(formatted)
        elif category == "review_only":
            review_only_issues.append(formatted)

    # Apply guardrail: cap fix count
    max_issues = config["guardrails"]["max_issues_per_run"]
    overflow = []
    if len(auto_fix_issues) > max_issues:
        overflow = auto_fix_issues[max_issues:]
        auto_fix_issues = auto_fix_issues[:max_issues]
        review_only_issues.extend(overflow)
        print(f"Capped fix at {max_issues} issues; {len(overflow)} moved to review-only")

    # Build output
    output = {
        "auto_fix": auto_fix_issues,
        "review_only": review_only_issues,
        "config": {
            "agent": config["agent"],
            "max_turns": config["guardrails"]["max_turns"],
            "max_iterations": config["guardrails"]["max_iterations"],
            "run_tests_after_fix": config["guardrails"]["run_tests_after_fix"],
            "verify_fixes": config["guardrails"]["verify_fixes"],
        },
        "summary": {
            "total_issues": len(issues),
            "auto_fix_count": len(auto_fix_issues),
            "review_only_count": len(review_only_issues),
            "overflow_count": len(overflow),
        },
    }

    output_json = json.dumps(output, separators=(",", ":"))

    # Write to GitHub Actions outputs
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"has_issues={'true' if issues else 'false'}\n")
            f.write(f"has_auto_fix={'true' if auto_fix_issues else 'false'}\n")
            f.write(f"agent={config['agent']}\n")
            # Use delimiter for multiline JSON
            f.write(f"issues_json<<ISSUES_EOF\n{json.dumps(output, indent=2)}\nISSUES_EOF\n")

    # Also print summary for the Actions log
    print("\n=== Triage Summary ===")
    print(f"Total issues:      {len(issues)}")
    print(f"Fix eligible: {len(auto_fix_issues)}")
    print(f"Review-only:       {len(review_only_issues)}")
    print(f"Agent:             {config['agent']}")

    if auto_fix_issues:
        print("\nFix issues:")
        for i in auto_fix_issues:
            print(f"  - [{i['severity']}] {i['rule']}: {i['message']} ({i['component']}:{i['line']})")

    if review_only_issues:
        print("\nReview-only issues:")
        for i in review_only_issues:
            print(f"  - [{i['severity']}] {i['rule']}: {i['message']} ({i['component']}:{i['line']})")


if __name__ == "__main__":
    main()
