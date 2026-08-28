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
        latency_ms=450.0,
    )
```

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
- Only counters, timings, and status are transmitted - never prompt or response text.
