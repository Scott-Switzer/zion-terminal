# Security Policy

## Secrets Management

Zion Terminal uses environment variables for all sensitive configuration.

### Rules

1. **Never commit `.env` files.** The `.gitignore` excludes `.env`, `.env.*`, and `.env.local`. Only `.env.example` (which contains no real values) is tracked.
2. **API keys are optional.** The system runs without any API keys by default (`LLM_PROVIDER=none`). Yahoo Finance requires no key.
3. **FRED and SEC EDGAR** require free credentials but are optional features.
4. **OpenAI API key** is only needed if you explicitly set `LLM_PROVIDER=openai`.

### Sensitive Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENAI_API_KEY` | No | OpenAI LLM (only if LLM_PROVIDER=openai) |
| `FRED_API_KEY` | No | FRED macroeconomic data |
| `EDGAR_IDENTITY` | No | SEC EDGAR access (name + email) |

### If You Accidentally Commit Secrets

1. Rotate the exposed key immediately.
2. Use `git filter-branch` or `BFG Repo-Cleaner` to remove the commit from history.
3. Force-push the cleaned history.

## Reporting Vulnerabilities

If you discover a security issue, please open a private issue or contact the maintainer directly. Do not open a public issue for security vulnerabilities.

## Dependencies

- All dependencies are pinned with minimum versions in `pyproject.toml`.
- `openai` and `tiktoken` are optional dependencies, not installed by default.
- Run `pip audit` periodically to check for known vulnerabilities.
