# Runbook: Adding a New Data Source Adapter

This runbook supplements the original guide at `docs/runbooks/adding-a-data-source.md`. Follow both documents when adding a new adapter.

---

## Overview

Adapters are the boundary between Zion Terminal's internal data model and external APIs or data files. Each adapter extends `BaseAdapter`, registers itself with `RetrievalAgent`, and must be reachable from both the CLI and the NL parser (if applicable).

---

## Step 1: Create the Adapter Class

Create `src/zion_terminal/agents/retrieval/adapters/<source_name>.py`:

```python
from zion_terminal.agents.retrieval.base_adapter import BaseAdapter
from zion_terminal.agents.retrieval.retry import adapter_retry
from zion_terminal.models.responses import RetrievalResult


class MySourceAdapter(BaseAdapter):
    SOURCE_NAME = "my_source"
    SUPPORTED_CATEGORIES = ["equity", "macro"]  # or whatever categories apply

    def fetch(self, params: dict) -> RetrievalResult:
        cached = self._cache_get(params)
        if cached is not None:
            return RetrievalResult(
                data=cached,
                sources_used=[self.SOURCE_NAME],
                cached=True,
            )

        result = self._do_fetch(params)
        if result.success:
            self._cache_set(params, result.data)
        return result

    @adapter_retry
    def _do_fetch(self, params: dict) -> RetrievalResult:
        # Make your external API call here.
        # Return RetrievalResult(success=True, data={...}, sources_used=[self.SOURCE_NAME])
        # or RetrievalResult(success=False, errors=["..."])
        raise NotImplementedError
```

**Checklist:**
- [ ] `SOURCE_NAME` is a unique lowercase string with underscores.
- [ ] `SUPPORTED_CATEGORIES` lists all categories this adapter can serve.
- [ ] `fetch()` checks the cache before making a network call.
- [ ] `_do_fetch()` is decorated with `@adapter_retry`.
- [ ] The adapter handles its own exceptions and returns `success=False` with a descriptive `errors` list rather than raising.

---

## Step 2: Register in RetrievalAgent.__init__

In `src/zion_terminal/agents/retrieval/agent.py`, add registration inside `__init__`:

```python
from zion_terminal.agents.retrieval.adapters.my_source import MySourceAdapter

# Inside __init__:
my_source_key = os.environ.get("MY_SOURCE_API_KEY")
if my_source_key:
    self.register_adapter(MySourceAdapter(key=my_source_key, cache=self.cache))
else:
    logger.warning("MY_SOURCE_API_KEY not set — MySourceAdapter not registered")
```

If the adapter requires no API key (e.g. it reads from a local file or a public API), register unconditionally:

```python
self.register_adapter(MySourceAdapter(cache=self.cache))
```

---

## Step 3: Add Orchestrator Public Method

In `src/zion_terminal/orchestrator/orchestrator.py`, expose a typed method:

```python
def get_my_data(
    self,
    param: str,
    *,
    validate: bool = True,
) -> OrchestratorResponse:
    """Fetch data from MySource for the given param."""
    return self._fetch_and_validate(
        tasks=[{"source": "my_source", "action": "fetch", "param": param}],
        query=f"my_source {param}",
        intent="my_intent",
        validate=validate,
    )
```

**Do not** directly instantiate adapters in the orchestrator. Always go through `_fetch_and_validate` so the validation pipeline and caching remain consistent.

---

## Step 4: Add CLI Command

In `src/zion_terminal/cli.py`, add a new click command:

```python
@cli.command("my-data")
@click.argument("param")
@click.option("--no-validate", is_flag=True, default=False, help="Skip validation.")
@click.pass_obj
def cmd_my_data(orc: Orchestrator, param: str, no_validate: bool) -> None:
    """Fetch data for PARAM from MySource."""
    response = orc.get_my_data(param, validate=not no_validate)
    _print_response(response)
```

Verify that `_print_response` handles the new intent correctly if it has any special formatting needs.

---

## Step 5: Add Parser Support

If users should be able to reach the adapter via natural language (e.g. `"my_source data for AAPL"`), update the NL intent parser:

In `src/zion_terminal/agents/parsing/intent_parser.py`:

1. Add keywords to `_INTENT_KEYWORDS`:
   ```python
   "my_intent": ["my_source", "my data", "my keyword"],
   ```

2. Add a routing branch in `_build_task_from_intent()` (or equivalent routing method):
   ```python
   elif intent == "my_intent":
       return {"source": "my_source", "action": "fetch", "param": ticker_or_param}
   ```

3. Add test queries to `tests/fixtures/sample_queries.json`:
   ```json
   {"query": "my source data for AAPL", "expected_intent": "my_intent", "expected_source": "my_source"}
   ```

---

## Step 6: Write Tests

Create `tests/test_my_source_adapter.py`:

```python
from unittest.mock import MagicMock, patch
from zion_terminal.agents.retrieval.adapters.my_source import MySourceAdapter


def test_fetch_success():
    adapter = MySourceAdapter(cache=MagicMock())
    with patch.object(adapter, "_do_fetch", return_value=MagicMock(success=True, data={"k": "v"})):
        result = adapter.fetch({"param": "test"})
    assert result.success
    assert result.data == {"k": "v"}


def test_fetch_uses_cache():
    cache = MagicMock()
    cache.get.return_value = {"k": "cached"}
    adapter = MySourceAdapter(cache=cache)
    result = adapter.fetch({"param": "test"})
    assert result.cached is True


def test_fetch_handles_api_error():
    adapter = MySourceAdapter(cache=MagicMock())
    with patch.object(adapter, "_do_fetch", side_effect=Exception("API down")):
        result = adapter.fetch({"param": "test"})
    assert not result.success
    assert any("API down" in e for e in result.errors)
```

Also add:
- `tests/test_cli.py` — test the new CLI command with `CliRunner`.
- `tests/fixtures/sample_queries.json` — add NL parser test cases.
- Integration test (decorated `@pytest.mark.integration`) that calls the real API.

---

## Step 7: Bump Cache Version (if needed)

If the new adapter's output format changes the cache schema (e.g. new required fields, renamed keys), increment `CACHE_SCHEMA_VERSION` in `src/zion_terminal/cache/cache_manager.py`:

```python
CACHE_SCHEMA_VERSION = 5  # was 4 — bumped for MySourceAdapter output format change
```

This invalidates all cached entries from previous versions automatically.

**When to bump:** any time the shape of `data` in `RetrievalResult` changes for any registered adapter, or when cache keys change format.

---

## Reference

- Original guide: `docs/runbooks/adding-a-data-source.md`
- Base class: `src/zion_terminal/agents/retrieval/base_adapter.py`
- Retry decorator: `src/zion_terminal/agents/retrieval/retry.py`
- Cache manager: `src/zion_terminal/cache/cache_manager.py`
- Existing adapters: `src/zion_terminal/agents/retrieval/adapters/`
