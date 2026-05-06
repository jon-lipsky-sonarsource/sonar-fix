# Routing through an API gateway

If your org accesses Anthropic or OpenAI via a virtual-key gateway (Portkey, internal proxy) or an observability proxy (Helicone, etc.) instead of calling the providers directly, this page covers the setup. Most users won't need any of this — skip if you're calling Anthropic or OpenAI directly.

This page is referenced from [claude.md](claude.md) and [codex.md](codex.md). The Copilot path doesn't apply — Copilot's coding agent is GitHub-hosted, so there's no per-call HTTP endpoint to redirect.

## Two flavors of gateway

The configuration depends on how the gateway authenticates:

- **Virtual-key gateways (Portkey, internal proxies)** — the gateway has its own keys, and your real provider credentials live on the gateway side. You give the workflow a virtual key; the gateway resolves it server-side.
- **Observability proxies (Helicone, etc.)** — the proxy forwards your request to the real provider, so you still need your real provider key in addition to the proxy's auth.

The setup differs slightly per provider because Anthropic and OpenAI use different auth headers (`x-api-key` vs `Authorization: Bearer`), and Portkey hijacks the latter for native virtual-key resolution.

---

## Variable vs Secret matters

`ANTHROPIC_BASE_URL` and `OPENAI_BASE_URL` must be **Variables**, not Secrets. The caller workflow reads them via `vars.X`, which can't pull from secrets.

Why this is fine: URLs aren't sensitive (Portkey's, Helicone's, etc. are in their public docs). If you put a `BASE_URL` in Secrets by mistake, the workflow fails at the "Validate proxy config" step with an error pointing back at this page.

---

## Routing Claude (Anthropic)

### Portkey-style virtual-key gateway

The gateway holds your real Anthropic key; you give the workflow a virtual key. Anthropic's auth header is `x-api-key`, which Portkey **can't** intercept the same way it does for OpenAI's bearer header — so the virtual key has to ride alongside the request as a custom header.

| Name | Type | Example |
|---|---|---|
| `ANTHROPIC_BASE_URL` | variable | `https://api.portkey.ai` |
| `ANTHROPIC_CUSTOM_HEADERS` | secret | `x-portkey-api-key: pk_xxxxx` (one header per line for multiple) |

Skip `ANTHROPIC_API_KEY` from the [Claude setup Step 2](claude.md#step-2--add-org-level-secrets) — the workflow auto-substitutes a placeholder when proxying, and the gateway ignores it. The Claude Code GitHub App from [Step 4](claude.md#step-4--install-the-claude-code-github-app) is still required (it gates the action's run, not the API call itself).

### Helicone-style observability proxy

The proxy forwards your request to Anthropic, so you still need your real Anthropic key in addition to the proxy's auth header.

| Name | Type | Example |
|---|---|---|
| `ANTHROPIC_BASE_URL` | variable | `https://api.helicone.ai` |
| `ANTHROPIC_API_KEY` | secret | your real Anthropic key (gets forwarded to Anthropic) |
| `ANTHROPIC_CUSTOM_HEADERS` | secret | `Helicone-Auth: Bearer hk_xxxxx` |

The reusable workflow exports `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN=dummy` (when proxying with no real key), and `ANTHROPIC_CUSTOM_HEADERS` to the Claude step's environment only when `ANTHROPIC_BASE_URL` is set. Direct-Anthropic users can ignore this entire section.

---

## Routing Codex (OpenAI)

OpenAI's auth header is `Authorization: Bearer …`, which Portkey hijacks for virtual-key resolution natively. That makes the Portkey case **simpler than Anthropic** — no custom headers needed.

The reusable workflow writes a `[model_providers.gateway]` entry into Codex's `config.toml` and overrides `model_provider` so Codex bypasses the built-in `openai` provider entirely. See [gotchas.md](gotchas.md#4-codexs-model_provider-is-set-via-a-cli-flag-not-the-config-file) for the implementation detail of how the override is applied.

### Portkey-style virtual-key gateway

Put your Portkey virtual key directly in `OPENAI_API_KEY`. Portkey resolves it to your real OpenAI key on its end.

| Name | Type | Example |
|---|---|---|
| `OPENAI_BASE_URL` | variable | `https://api.portkey.ai/v1` |
| `OPENAI_API_KEY` | secret | `pk_xxxxx` — your Portkey virtual key for the OpenAI route (a *different* `pk_xxxxx` than your Anthropic virtual key, since Portkey scopes virtual keys per provider) |

> **`/v1` suffix matters.** Codex talks to `<base>/v1/responses`; without `/v1` you'll see 404s in the proxy logs.

### Helicone-style observability proxy

| Name | Type | Example |
|---|---|---|
| `OPENAI_BASE_URL` | variable | `https://oai.helicone.ai/v1` |
| `OPENAI_API_KEY` | secret | your real OpenAI key (gets forwarded to OpenAI) |
| `OPENAI_CUSTOM_HEADERS` | secret | `Helicone-Auth: Bearer hk_xxxxx` |

---

## Validating the setup

Both `ANTHROPIC_BASE_URL` and `OPENAI_BASE_URL` are validated by an early step in the reusable workflow (`Validate proxy config`). It catches the common mistakes:

- BASE_URL set as a Secret instead of a Variable → caller can't read it via `vars.X`
- CUSTOM_HEADERS set without a BASE_URL
- BASE_URL points at OpenAI but is missing the `/v1` suffix

If your run fails at that step, the error message identifies which knob is wrong.
