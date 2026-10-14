"""AIMONITOR Python client.

Design constraints, in priority order:

1. **Never break the caller.** Every transport failure is swallowed and logged;
   an observability outage must not become an application outage.
2. **Minimize content.** Usage extractors read counters, never response text.
   Known content metadata keys are redacted; explicit diagnostics remain opt-in.
3. **Stay out of the latency path when asked.** `track()` posts inline and
   returns the stored record; `enqueue()` hands off to a background worker that
   batches and flushes, costing the caller a queue append.
"""
import asyncio
import atexit
import functools
import inspect
import logging
import queue
import threading
import time
from itertools import islice
from typing import Any, Callable, Dict, List, Optional

import httpx

logger = logging.getLogger("aimonitor")

DEFAULT_API_URL = "http://localhost:8000/api/v1"

# Kept in sync with the server by test_sdk_sensitive_key_list_matches_server.
_SENSITIVE_KEY_PARTS = (
    "prompt", "completion", "response", "message", "content", "answer", "question", "query",
    "input_text", "output_text", "system", "choices", "transcript", "document", "context_text",
)
_USAGE_FIELDS = (
    "prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_write_tokens", "cache_write_1h_tokens",
)


def _redact_metadata(value: Any, depth: int = 0) -> Any:
    if isinstance(value, (dict, list)) and depth > 6:
        return {"_truncated": "max depth exceeded"}
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS)
            else _redact_metadata(item, depth + 1)
            for key, item in islice(value.items(), 50)
        }
    if isinstance(value, list):
        return [_redact_metadata(item, depth + 1) for item in value[:50]]
    return value


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
        "cache_write_1h_tokens": 0,
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
    cache_creation = field(source, "cache_creation")
    one_hour = field(cache_creation, "ephemeral_1h_input_tokens") if cache_creation is not None else None
    if type(one_hour) is int:
        usage["cache_write_1h_tokens"] = min(max(0, one_hour), max(0, usage["cache_write_tokens"]))

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
        self._condition = threading.Condition()
        self._close_lock = threading.Lock()
        self._closing = False
        self._closed = False
        self._outstanding = 0
        self._active_sends = 0
        self._flush_requested = False
        self._atexit_registered = False
        self._background = background
        self._max_queue_size = max(1, max_queue_size)

        if background:
            self._start_worker()

    # --- internals ------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        return {"X-API-Key": self.api_key} if self.api_key else {}

    def _start_worker(self) -> bool:
        with self._condition:
            if self._closing:
                return False
            if self._queue is None:
                self._queue = queue.Queue(maxsize=self._max_queue_size)
                self._worker = threading.Thread(
                    target=self._drain_loop, name="aimonitor-flush", daemon=True,
                )
                atexit.register(self.close)
                self._atexit_registered = True
                self._worker.start()
            return True

    def _begin_send(self) -> bool:
        with self._condition:
            if self._closing:
                return False
            self._active_sends += 1
            return True

    def _finish_send(self, delivered: bool) -> None:
        with self._condition:
            self._active_sends -= 1
            if not delivered:
                self._dropped += 1
            self._condition.notify_all()

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
        latency_ms: Optional[float],
        status: str,
        error_message: Optional[str],
        session_id: Optional[str],
        trace_id: Optional[str],
        tags: Optional[List[str]],
        metadata: Optional[Dict[str, Any]],
        account_id: Optional[str] = None,
        cache_write_1h_tokens: int = 0,
    ) -> Dict[str, Any]:
        return {
            "trace_id": trace_id,
            "provider": provider,
            "model_name": model_name,
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "cache_read_tokens": max(0, cache_read_tokens),
            "cache_write_tokens": max(0, cache_write_tokens),
            "cache_write_1h_tokens": min(max(0, cache_write_1h_tokens), max(0, cache_write_tokens)),
            "latency_ms": max(0.0, latency_ms) if latency_ms is not None else None,
            "status": status,
            "error_message": error_message,
            "session_id": session_id,
            "account_id": account_id,
            "tags": tags,
            "metadata": _redact_metadata(metadata),
        }

    def _drain_loop(self) -> None:
        assert self._queue is not None
        pending: List[Dict[str, Any]] = []
        last_flush = time.monotonic()
        while True:
            with self._condition:
                while True:
                    while len(pending) < self.batch_size:
                        try:
                            pending.append(self._queue.get_nowait())
                        except queue.Empty:
                            break
                    remaining = self.flush_interval - (time.monotonic() - last_flush)
                    if pending and (
                        len(pending) >= self.batch_size or remaining <= 0
                        or self._flush_requested or self._stop.is_set()
                    ):
                        break
                    if self._stop.is_set() and not pending:
                        return
                    self._condition.wait(timeout=max(0.01, remaining) if pending else None)
            try:
                delivered = self._post_batch(pending)
            except Exception as exc:
                delivered = False
                logger.debug("aimonitor: worker send failed (%s)", type(exc).__name__)
            with self._condition:
                if not delivered:
                    self._dropped += len(pending)
                self._outstanding -= len(pending)
                for _ in pending:
                    self._queue.task_done()
                pending = []
                if self._outstanding == 0:
                    self._flush_requested = False
                self._condition.notify_all()
            last_flush = time.monotonic()

    def _post_batch(self, payloads: List[Dict[str, Any]]) -> bool:
        if not payloads:
            return True
        try:
            response = self._sync_client.post(f"{self.api_url}/traces/batch", json={"traces": payloads})
            return 200 <= response.status_code < 300
        except Exception as exc:
            logger.debug("aimonitor: batch flush failed (%s)", exc.__class__.__name__)
            return False

    # --- public API -----------------------------------------------------
    def track(
        self,
        provider: str,
        model_name: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        latency_ms: Optional[float] = None,
        status: str = "SUCCESS",
        error_message: Optional[str] = None,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        account_id: Optional[str] = None,
        *,
        cache_write_1h_tokens: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Send one trace inline and return the stored record, or None on failure."""
        if not self._begin_send():
            return None
        delivered = False
        try:
            payload = self._build_payload(
                provider, model_name, prompt_tokens, completion_tokens,
                cache_read_tokens, cache_write_tokens, latency_ms, status,
                error_message, session_id, trace_id, tags, metadata, account_id, cache_write_1h_tokens,
            )
            res = self._sync_client.post(f"{self.api_url}/traces", json=payload)
            if res.status_code == 201:
                result = res.json()
                delivered = True
                return result
            logger.debug("aimonitor: ingest rejected with HTTP %s", res.status_code)
        except Exception as exc:
            # Silent by design: a monitoring outage must not break the caller.
            logger.debug("aimonitor: ingest failed (%s)", exc.__class__.__name__)
        finally:
            self._finish_send(delivered)
        return None

    def enqueue(self, **kwargs: Any) -> bool:
        """Queue a trace for background delivery. Requires background=True.

        Returns False when the queue is full, in which case the trace is dropped
        rather than blocking the calling request.
        """
        with self._condition:
            if self._closing:
                return False
        if self._queue is None:
            return self.track(**kwargs) is not None
        try:
            payload = self._build_payload(
                kwargs.get("provider", "custom"),
                kwargs.get("model_name", "unknown"),
                kwargs.get("prompt_tokens", 0),
                kwargs.get("completion_tokens", 0),
                kwargs.get("cache_read_tokens", 0),
                kwargs.get("cache_write_tokens", 0),
                kwargs.get("latency_ms"),
                kwargs.get("status", "SUCCESS"),
                kwargs.get("error_message"),
                kwargs.get("session_id"),
                kwargs.get("trace_id"),
                kwargs.get("tags"),
                kwargs.get("metadata"),
                kwargs.get("account_id"),
                kwargs.get("cache_write_1h_tokens", 0),
            )
            with self._condition:
                if self._closing:
                    return False
                try:
                    self._queue.put_nowait(payload)
                except queue.Full:
                    self._dropped += 1
                    if self._dropped % 100 == 1:
                        logger.warning("aimonitor: queue full, dropped %d traces", self._dropped)
                    return False
                self._outstanding += 1
                self._condition.notify_all()
                return True
        except Exception as exc:
            with self._condition:
                self._dropped += 1
            logger.debug("aimonitor: enqueue failed (%s)", type(exc).__name__)
            return False

    @property
    def pending_count(self) -> int:
        """Accepted traces still queued, pending, or in flight."""
        with self._condition:
            return self._outstanding + self._active_sends

    @property
    def dropped_count(self) -> int:
        """Cumulative dropped or unconfirmed traces, including transport failures."""
        with self._condition:
            return self._dropped

    def flush(self, timeout: float = 5.0) -> bool:
        """Force delivery and wait; False means timeout or cumulative delivery loss."""
        deadline = time.monotonic() + timeout
        with self._condition:
            self._flush_requested = True
            self._condition.notify_all()
            while self._outstanding or self._active_sends:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
            self._flush_requested = False
            return self._dropped == 0

    async def a_track(
        self,
        provider: str,
        model_name: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        latency_ms: Optional[float] = None,
        status: str = "SUCCESS",
        error_message: Optional[str] = None,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        account_id: Optional[str] = None,
        *,
        cache_write_1h_tokens: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Asynchronously send a single trace event."""
        if not self._begin_send():
            return None
        delivered = False
        try:
            payload = self._build_payload(
                provider, model_name, prompt_tokens, completion_tokens,
                cache_read_tokens, cache_write_tokens, latency_ms, status,
                error_message, session_id, trace_id, tags, metadata, account_id, cache_write_1h_tokens,
            )
            res = await self._get_async_client().post(f"{self.api_url}/traces", json=payload)
            if res.status_code == 201:
                result = res.json()
                delivered = True
                return result
            logger.debug("aimonitor: async ingest rejected with HTTP %s", res.status_code)
        except Exception as exc:
            logger.debug("aimonitor: async ingest failed (%s)", exc.__class__.__name__)
        finally:
            self._finish_send(delivered)
        return None

    def observe(
        self,
        provider: str = "custom",
        model_name: str = "unknown",
        tags: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        capture_usage: bool = True,
        usage_extractor: Optional[Callable[[Any], Dict[str, int]]] = None,
        account_id: Optional[str] = None,
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
                    extracted = extractor(result)
                    if not isinstance(extracted, dict):
                        return {}
                    usage = {key: value for key, value in extracted.items() if key in _USAGE_FIELDS}
                    if any(type(value) is not int or value > 1_000_000_000 for value in usage.values()):
                        return {}
                    usage = {key: max(0, value) for key, value in usage.items()}
                    if sum(usage.get(key, 0) for key in _USAGE_FIELDS[:4]) > 1_000_000_000:
                        return {}
                    return usage
                except Exception:
                    return {}

            if inspect.iscoroutinefunction(func):
                @functools.wraps(func)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    start_time = time.perf_counter()
                    status_val, err_msg, usage = "SUCCESS", None, {}
                    cancelled = False
                    try:
                        result = await func(*args, **kwargs)
                        usage = usage_for(result)
                        return result
                    except asyncio.CancelledError as exc:
                        status_val, err_msg, cancelled = "ERROR", type(exc).__name__, True
                        raise
                    except Exception as e:
                        status_val = "ERROR"
                        err_msg = type(e).__name__
                        raise
                    finally:
                        try:
                            payload = dict(
                                provider=provider,
                                model_name=model_name,
                                latency_ms=round((time.perf_counter() - start_time) * 1000.0, 2),
                                status=status_val,
                                error_message=err_msg,
                                session_id=session_id,
                                account_id=account_id,
                                tags=tags,
                                **usage,
                            )
                            if self._background or cancelled:
                                if self._start_worker():
                                    self.enqueue(**payload)
                            else:
                                await self.a_track(**payload)
                        except Exception as exc:
                            logger.debug("aimonitor: observation failed (%s)", type(exc).__name__)
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
                    err_msg = type(e).__name__
                    raise
                finally:
                    send = self.enqueue if self._background else self.track
                    try:
                        send(
                            provider=provider,
                            model_name=model_name,
                            latency_ms=round((time.perf_counter() - start_time) * 1000.0, 2),
                            status=status_val,
                            error_message=err_msg,
                            session_id=session_id,
                            account_id=account_id,
                            tags=tags,
                            **usage,
                        )
                    except Exception as exc:
                        logger.debug("aimonitor: observation failed (%s)", type(exc).__name__)
            return sync_wrapper
        return decorator

    def close(self) -> bool:
        """Reject new work, drain all accepted sends, then close the sync transport."""
        with self._close_lock:
            with self._condition:
                if self._closed:
                    return self._dropped == 0
                self._closing = True
                self._stop.set()
                self._condition.notify_all()
            if self._worker is not None:
                self._worker.join()
            with self._condition:
                while self._active_sends:
                    self._condition.wait()
                # Defensive accounting if a caller injected a queue without a worker.
                if self._outstanding:
                    self._dropped += self._outstanding
                    self._outstanding = 0
            try:
                self._sync_client.close()
            except Exception as exc:
                logger.debug("aimonitor: transport close failed (%s)", type(exc).__name__)
            with self._condition:
                self._closed = True
            if self._atexit_registered:
                atexit.unregister(self.close)
                self._atexit_registered = False
            return self.dropped_count == 0

    async def a_close(self) -> bool:
        """Drain sync/background work off the event loop, then close async transport."""
        delivered = await asyncio.to_thread(self.close)
        with self._async_lock:
            client, self._async_client = self._async_client, None
        if client is not None:
            try:
                await client.aclose()
            except Exception as exc:
                logger.debug("aimonitor: async transport close failed (%s)", type(exc).__name__)
                return False
        return delivered

    def __enter__(self) -> "AIMonitor":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    async def __aenter__(self) -> "AIMonitor":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.a_close()


__all__ = ["AIMonitor", "extract_usage", "DEFAULT_API_URL"]
