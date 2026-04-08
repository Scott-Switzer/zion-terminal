# Runbook: Adding a New Data Source

## Steps

### 1. Create the Adapter

Create `src/zion_terminal/agents/retrieval/adapters/my_source.py`:

```python
from zion_terminal.agents.retrieval.base_adapter import BaseAdapter
from zion_terminal.agents.retrieval.retry import adapter_retry
from zion_terminal.models.responses import RetrievalResult

class MySourceAdapter(BaseAdapter):
    SOURCE_NAME = "my_source"
    SUPPORTED_CATEGORIES = ["category1", "category2"]

    def fetch(self, params: dict) -> RetrievalResult:
        # 1. Check cache
        cached = self._cache_get(params)
        if cached is not None:
            return RetrievalResult(data=cached, sources_used=[self.SOURCE_NAME], cached=True)

        # 2. Fetch data (use @adapter_retry on internal methods)
        result = self._do_fetch(params)
        if result.success:
            self._cache_set(params, result.data)
        return result

    @adapter_retry
    def _do_fetch(self, params: dict) -> RetrievalResult:
        # Your API call here
        ...
```

### 2. Register in RetrievalAgent

In `agents/retrieval/agent.py`, add registration in `__init__`:

```python
if my_source_key:
    self.register_adapter(MySourceAdapter(key=my_source_key, cache=cache))
```

### 3. Add Orchestrator Public Method

In `orchestrator/orchestrator.py`, add:

```python
def get_my_data(self, param: str, *, validate: bool = True) -> OrchestratorResponse:
    return self._fetch_and_validate(
        tasks=[{"source": "my_source", "param": param}],
        query=f"my_source {param}",
        intent="my_intent",
        validate=validate,
    )
```

### 4. Add CLI Command

In `cli.py`, add a new click command that calls `orc.get_my_data()`.

### 5. Add Parser Support (Optional)

If the source should be reachable via natural language, add intent keywords
to `_INTENT_KEYWORDS` and handle routing in `_build_equity_task`.

### 6. Write Tests

- Unit test for the adapter (mock the external API)
- Unit test for the CLI command
- Add queries to `tests/fixtures/sample_queries.json`
- Integration test (mark with `@pytest.mark.integration`)

### 7. Bump Cache Version

If the new adapter's output format differs from existing sources,
bump `CACHE_SCHEMA_VERSION` in `cache_manager.py`.
