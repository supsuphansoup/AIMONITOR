# aimonitor-sdk

Python client for [AIMONITOR](https://github.com/supsuphansoup/AIMONITOR).

```bash
pip install -e sdk/python
```

## Explicit tracking

```python
from aimonitor import AIMonitor

with AIMonitor(api_url="http://localhost:8000/api/v1", api_key="...") as mon:
    mon.track(
        provider="anthropic",
        model_name="claude-3-5-sonnet",
        prompt_tokens=1200,
        completion_tokens=600,
        cache_read_tokens=8000,
        cache_write_tokens=1000,
        cache_write_1h_tokens=400,  # subset of cache_write_tokens, never added twice
        latency_ms=450.0,
    )
```

Omitting `latency_ms` (or passing `None`) sends null for an unmeasured value.
Passing `0.0` keeps a measured zero. Run the install command from the repository root.

## Decorator

`observe` measures latency, records failures, and reads token counters straight
off the provider response, so cost is real rather than zero.

```python
monitor = AIMonitor()

@monitor.observe(provider="openai", model_name="gpt-4o", tags=["production"])
def call_gpt(prompt: str):
    return openai_client.chat.completions.create(...)
```

Supported response shapes: OpenAI (`usage.prompt_tokens`), Anthropic
(`usage.input_tokens`, plus cache counters), Google
(`usage_metadata.prompt_token_count`), and plain dicts. Pass
`usage_extractor=...` for anything else, or `capture_usage=False` to skip it.
Anthropic's `usage.cache_creation.ephemeral_1h_input_tokens` is carried as
`cache_write_1h_tokens`, a subset of the total cache writes
([official response fields](https://platform.claude.com/docs/en/build-with-claude/prompt-caching#mixing-different-ttls)).
Custom extractors must return a dict of integer token counters within the server
limits. Invalid results are omitted; instrumentation failures preserve the
observed function's return value or original exception.

## Background mode

`background=True` moves delivery off the request path: `enqueue()` appends to a
queue and a worker thread batches and flushes it.

```python
monitor = AIMonitor(background=True, batch_size=50, flush_interval=2.0)
monitor.enqueue(provider="openai", model_name="gpt-4o", prompt_tokens=100)
monitor.flush()   # drain before shutdown; close() also drains
```

## Guarantees

- Transport failures never propagate to the caller.
- Automatic usage extraction reads counters, never prompt or response text.
- Automatic errors contain only the exception class name; the original exception is re-raised.
- Explicit calls also send provider/model, optional trace/session/account IDs, tags,
  metadata, and any `error_message` you pass. These fields can be stored by the server.
- Known content metadata keys are recursively redacted before queuing or sending,
  through dicts and lists (50 items per container, depth 6 from root depth 0).
  Key-based filtering cannot recognize every secret: keep content and credentials
  out of arbitrary metadata keys, tags, IDs, and explicit error messages.
