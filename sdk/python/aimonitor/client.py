"""AIMONITOR Python client.

Design constraints, in priority order:

1. **Never break the caller.** Every transport failure is swallowed and logged;
   an observability outage must not become an application outage.
2. **Never leak content.** Only token counts, timings, and status are sent. The
   usage extractors read counters off a provider response, never its text.
3. **Stay out of the latency path when asked.** `track()` posts inline and
   returns the stored record; `enqueue()` hands off to a background worker that
   batches and flushes, costing the caller a queue append.
"""
import atexit
import functools
import inspect
import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import httpx

logger = logging.getLogger("aimonitor")

DEFAULT_API_URL = "http://localhost:8000/api/v1"


def extract_usage(response: Any) -> Dict[str, int]:
    """Read token counters off a provider response object.

    Understands the OpenAI, Anthropic, and Google shapes plus plain dicts, and
    returns zeros for anything unrecognised. Only integer counters are touched -
    message content is never inspected.
    """
    usage: Dict[str, int] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    if response is None:
        return usage

    source: Any = None
    if isinstance(response, dict):
        source = response.get("usage") or response.get("usage_metadata")
    else:
        source = getattr(response, "usage", None) or getattr(response, "usage_metadata", None)
    if source is None:
        return usage

    def field(obj: Any, name: str) -> Any:
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

    def read(*names: str) -> int:
        for name in names:
            value = field(source, name)
            if isinstance(value, int):
                return value
        return 0

    # OpenAI: prompt_tokens/completion_tokens (Responses API: input_tokens/output_tokens)
    # Anthropic: input_tokens/output_tokens (+ cache counters)
    # Google: prompt_token_count/candidates_token_count
    usage["prompt_tokens"] = read("prompt_tokens", "input_tokens", "prompt_token_count")
    usage["completion_tokens"] = read(
        "completion_tokens", "output_tokens", "candidates_token_count"
    )
    usage["cache_read_tokens"] = read(
        "cache_read_input_tokens", "cached_content_token_count", "cached_tokens"
    )
    usage["cache_write_tokens"] = read("cache_creation_input_tokens")

    # The server prices prompt_tokens as *uncached* input. Anthropic already
    # reports it that way; OpenAI and Google include the cached part in the
    # prompt count, so it is split out here or it would be billed twice
    # (Google) or at the full rate (OpenAI).
    details = field(source, "prompt_tokens_details") or field(source, "input_tokens_details")
    openai_cached = field(details, "cached_tokens") if details is not None else None
    if isinstance(openai_cached, int):
        usage["cache_read_tokens"] = openai_cached
        usage["prompt_tokens"] = max(0, usage["prompt_tokens"] - openai_cached)
    elif isinstance(field(source, "cached_content_token_count"), int):
        usage["prompt_tokens"] = max(0, usage["prompt_tokens"] - usage["cache_read_tokens"])
    return usage


class AIMonitor:
    """Client SDK for ingesting LLM traces, with sync and async support."""

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        api_key: Optional[str] = None,
        timeout: float = 3.0,
        background: bool = False,
        batch_size: int = 25,
        flush_interval: float = 2.0,
        max_queue_size: int = 10_000,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.batch_size = max(1, batch_size)
        self.flush_interval = max(0.1, flush_interval)

        self._sync_client = httpx.Client(timeout=self.timeout, headers=self._headers())
        self._async_client: Optional[httpx.AsyncClient] = None
        self._async_lock = threading.Lock()

        self._queue: Optional["queue.Queue[Dict[str, Any]]"] = None
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._dropped = 0

        if background:
            self._queue = queue.Queue(maxsize=max_queue_size)
            self._worker = threading.Thread(
                target=self._drain_loop, name="aimonitor-flush", daemon=True
            )
            self._worker.start()
            atexit.register(self.close)

    # --- internals ------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        return {"X-API-Key": self.api_key} if self.api_key else {}

    def _get_async_client(self) -> httpx.AsyncClient:
        # Created lazily so constructing AIMonitor in a sync program does not
        # leave an unawaited client behind.
        with self._async_lock:
            if self._async_client is None:
                self._async_client = httpx.AsyncClient(
                    timeout=self.timeout, headers=self._headers()
                )
            return self._async_client

    @staticmethod
    def _build_payload(
        provider: str,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        latency_ms: float,
        status: str,
        error_message: Optional[str],
        session_id: Optional[str],
        trace_id: Optional[str],
        tags: Optional[List[str]],
        metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "trace_id": trace_id,
            "provider": provider,
            "model_name": model_name,
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "cache_read_tokens": max(0, cache_read_tokens),
            "cache_write_tokens": max(0, cache_write_tokens),
            "latency_ms": max(0.0, latency_ms),
            "status": status,
            "error_message": error_message,
            "session_id": session_id,
            "tags": tags,
            "metadata": metadata,
        }

    def _drain_loop(self) -> None:
        assert self._queue is not None
        pending: List[Dict[str, Any]] = []
        last_flush = time.monotonic()
        while not self._stop.is_set() or not self._queue.empty() or pending:
            timeout = max(0.05, self.flush_interval - (time.monotonic() - last_flush))
            try:
                pending.append(self._queue.get(timeout=timeout))
            except queue.Empty:
                pass

            due = (
                len(pending) >= self.batch_size
                or (pending and time.monotonic() - last_flush >= self.flush_interval)
                or (pending and self._stop.is_set())
            )
            if due:
                self._post_batch(pending)
                pending = []
                last_flush = time.monotonic()

    def _post_batch(self, payloads: List[Dict[str, Any]]) -> None:
        if not payloads:
            return
        try:
            self._sync_client.post(f"{self.api_url}/traces/batch", json={"traces": payloads})
        except Exception as exc:
            logger.debug("aimonitor: batch flush failed (%s)", exc.__class__.__name__)

    # --- public API -----------------------------------------------------
    def track(
        self,
        provider: str,
        model_name: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        latency_ms: float = 0.0,
        status: str = "SUCCESS",
        error_message: Optional[str] = None,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Send one trace inline and return the stored record, or None on failure."""
        payload = self._build_payload(
            provider, model_name, prompt_tokens, completion_tokens,
            cache_read_tokens, cache_write_tokens, latency_ms, status,
            error_message, session_id, trace_id, tags, metadata,
        )
        try:
            res = self._sync_client.post(f"{self.api_url}/traces", json=payload)
            if res.status_code == 201:
                return res.json()
            logger.debug("aimonitor: ingest rejected with HTTP %s", res.status_code)
        except Exception as exc:
            # Silent by design: a monitoring outage must not break the caller.
            logger.debug("aimonitor: ingest failed (%s)", exc.__class__.__name__)
        return None

    def enqueue(self, **kwargs: Any) -> bool:
        """Queue a trace for background delivery. Requires background=True.

        Returns False when the queue is full, in which case the trace is dropped
        rather than blocking the calling request.
        """
        if self._queue is None:
            self.track(**kwargs)
            return True
        payload = self._build_payload(
            kwargs.get("provider", "custom"),
            kwargs.get("model_name", "unknown"),
            kwargs.get("prompt_tokens", 0),
            kwargs.get("completion_tokens", 0),
            kwargs.get("cache_read_tokens", 0),
            kwargs.get("cache_write_tokens", 0),
            kwargs.get("latency_ms", 0.0),
            kwargs.get("status", "SUCCESS"),
            kwargs.get("error_message"),
            kwargs.get("session_id"),
            kwargs.get("trace_id"),
            kwargs.get("tags"),
            kwargs.get("metadata"),
        )
        try:
            self._queue.put_nowait(payload)
            return True
        except queue.Full:
            self._dropped += 1
            if self._dropped % 100 == 1:
                logger.warning("aimonitor: queue full, dropped %d traces", self._dropped)
            return False

    def flush(self, timeout: float = 5.0) -> None:
        """Block until the background queue has drained (no-op in inline mode)."""
        if self._queue is None:
            return
        deadline = time.monotonic() + timeout
        while not self._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.05)

    async def a_track(
        self,
        provider: str,
        model_name: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        latency_ms: float = 0.0,
        status: str = "SUCCESS",
        error_message: Optional[str] = None,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Asynchronously send a single trace event."""
        payload = self._build_payload(
            provider, model_name, prompt_tokens, completion_tokens,
            cache_read_tokens, cache_write_tokens, latency_ms, status,
            error_message, session_id, trace_id, tags, metadata,
        )
        try:
            res = await self._get_async_client().post(f"{self.api_url}/traces", json=payload)
            if res.status_code == 201:
                return res.json()
            logger.debug("aimonitor: async ingest rejected with HTTP %s", res.status_code)
        except Exception as exc:
            logger.debug("aimonitor: async ingest failed (%s)", exc.__class__.__name__)
        return None

    def observe(
        self,
        provider: str = "custom",
        model_name: str = "unknown",
        tags: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        capture_usage: bool = True,
        usage_extractor: Optional[Callable[[Any], Dict[str, int]]] = None,
    ) -> Callable:
        """Decorator for sync and async functions that call an LLM.

        Measures latency, records success or failure, and - unless
        `capture_usage=False` - reads token counters off the returned provider
        response so the trace carries a real cost instead of zero.
        """
        extractor = usage_extractor or extract_usage

        def decorator(func: Callable) -> Callable:
            def usage_for(result: Any) -> Dict[str, int]:
                if not capture_usage:
                    return {}
                try:
                    return extractor(result)
                except Exception:
                    return {}

            if inspect.iscoroutinefunction(func):
                @functools.wraps(func)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    start_time = time.perf_counter()
                    status_val, err_msg, usage = "SUCCESS", None, {}
                    try:
                        result = await func(*args, **kwargs)
                        usage = usage_for(result)
                        return result
                    except Exception as e:
                        status_val = "ERROR"
                        err_msg = str(e)
                        raise
                    finally:
                        await self.a_track(
                            provider=provider,
                            model_name=model_name,
                            latency_ms=round((time.perf_counter() - start_time) * 1000.0, 2),
                            status=status_val,
                            error_message=err_msg,
                            session_id=session_id,
                            tags=tags,
                            **usage,
                        )
                return async_wrapper

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                start_time = time.perf_counter()
                status_val, err_msg, usage = "SUCCESS", None, {}
                try:
                    result = func(*args, **kwargs)
                    usage = usage_for(result)
                    return result
                except Exception as e:
                    status_val = "ERROR"
                    err_msg = str(e)
                    raise
                finally:
                    send = self.enqueue if self._queue is not None else self.track
                    send(
                        provider=provider,
                        model_name=model_name,
                        latency_ms=round((time.perf_counter() - start_time) * 1000.0, 2),
                        status=status_val,
                        error_message=err_msg,
                        session_id=session_id,
                        tags=tags,
                        **usage,
                    )
            return sync_wrapper
        return decorator

    def close(self) -> None:
        """Drain the queue if there is one, then close the sync transport."""
        if self._worker is not None and self._worker.is_alive():
            self._stop.set()
            self._worker.join(timeout=self.timeout + self.flush_interval)
        try:
            self._sync_client.close()
        except Exception:
            pass

    async def a_close(self) -> None:
        """Close the async transport."""
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

    def __enter__(self) -> "AIMonitor":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    async def __aenter__(self) -> "AIMonitor":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.a_close()


__all__ = ["AIMonitor", "extract_usage", "DEFAULT_API_URL"]
