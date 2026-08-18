import inspect
import functools
import time
import asyncio
from typing import Optional, Dict, Any, List, Callable, Union
import httpx


class AIMonitor:
    """Client SDK for ingesting LLM traces into AIMONITOR backend with sync and async support."""

    def __init__(
        self,
        api_url: str = "http://localhost:8000/api/v1",
        timeout: float = 3.0,
    ):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._sync_client = httpx.Client(timeout=self.timeout)
        self._async_client = httpx.AsyncClient(timeout=self.timeout)

    def track(
        self,
        provider: str,
        model_name: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: float = 0.0,
        status: str = "SUCCESS",
        error_message: Optional[str] = None,
        session_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Synchronously send a single trace event to AIMONITOR backend."""
        payload = {
            "provider": provider,
            "model_name": model_name,
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "latency_ms": max(0.0, latency_ms),
            "status": status,
            "error_message": error_message,
            "session_id": session_id,
            "tags": tags,
            "metadata": metadata,
        }
        try:
            res = self._sync_client.post(f"{self.api_url}/traces", json=payload)
            if res.status_code == 201:
                return res.json()
        except Exception:
            # Silent fallback so monitoring network issues never interrupt user application
            pass
        return None

    async def a_track(
        self,
        provider: str,
        model_name: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: float = 0.0,
        status: str = "SUCCESS",
        error_message: Optional[str] = None,
        session_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Asynchronously send a single trace event to AIMONITOR backend."""
        payload = {
            "provider": provider,
            "model_name": model_name,
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "latency_ms": max(0.0, latency_ms),
            "status": status,
            "error_message": error_message,
            "session_id": session_id,
            "tags": tags,
            "metadata": metadata,
        }
        try:
            res = await self._async_client.post(f"{self.api_url}/traces", json=payload)
            if res.status_code == 201:
                return res.json()
        except Exception:
            pass
        return None

    def observe(
        self,
        provider: str = "custom",
        model_name: str = "unknown",
        tags: Optional[List[str]] = None,
    ) -> Callable:
        """
        Universal decorator supporting both synchronous and asynchronous (async def) functions.
        Measures execution latency, tracks success/failure status, and logs trace events.
        """
        def decorator(func: Callable) -> Callable:
            if inspect.iscoroutinefunction(func):
                @functools.wraps(func)
                async def async_wrapper(*args, **kwargs):
                    start_time = time.perf_counter()
                    status_val = "SUCCESS"
                    err_msg = None
                    try:
                        result = await func(*args, **kwargs)
                        return result
                    except Exception as e:
                        status_val = "ERROR"
                        err_msg = str(e)
                        raise e
                    finally:
                        duration_ms = (time.perf_counter() - start_time) * 1000.0
                        await self.a_track(
                            provider=provider,
                            model_name=model_name,
                            latency_ms=round(duration_ms, 2),
                            status=status_val,
                            error_message=err_msg,
                            tags=tags,
                        )
                return async_wrapper
            else:
                @functools.wraps(func)
                def sync_wrapper(*args, **kwargs):
                    start_time = time.perf_counter()
                    status_val = "SUCCESS"
                    err_msg = None
                    try:
                        result = func(*args, **kwargs)
                        return result
                    except Exception as e:
                        status_val = "ERROR"
                        err_msg = str(e)
                        raise e
                    finally:
                        duration_ms = (time.perf_counter() - start_time) * 1000.0
                        self.track(
                            provider=provider,
                            model_name=model_name,
                            latency_ms=round(duration_ms, 2),
                            status=status_val,
                            error_message=err_msg,
                            tags=tags,
                        )
                return sync_wrapper
        return decorator

    def close(self):
        """Close synchronous client."""
        self._sync_client.close()

    async def a_close(self):
        """Close asynchronous client."""
        await self._async_client.aclose()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.a_close()
