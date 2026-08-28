"""Generate realistic LLM traffic against a running AIMONITOR backend.

    python scripts/simulate.py --batches 20 --spread-hours 6

`--spread-hours` backdates the generated events so the charts have history to
draw instead of one spike at the current minute.
"""
import argparse
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx

DEFAULT_API_URL = "http://localhost:8000/api/v1"

MODELS = [
    {"provider": "openai", "model": "gpt-4o", "prompt": 1200, "completion": 450, "latency": 420},
    {"provider": "openai", "model": "gpt-4o-mini", "prompt": 600, "completion": 250, "latency": 180},
    {"provider": "anthropic", "model": "claude-3-5-sonnet", "prompt": 1800, "completion": 800, "latency": 680},
    {"provider": "anthropic", "model": "claude-3-5-haiku", "prompt": 500, "completion": 200, "latency": 230},
    {"provider": "gemini", "model": "gemini-2.5-flash", "prompt": 3000, "completion": 900, "latency": 520},
    {"provider": "gemini", "model": "gemini-2.0-flash", "prompt": 800, "completion": 350, "latency": 150},
    {"provider": "ollama", "model": "llama3.1:8b", "prompt": 700, "completion": 300, "latency": 850},
    # Intentionally absent from the pricing table, so the dashboard has
    # something to render as "계산 불가".
    {"provider": "acme", "model": "mystery-model-v9", "prompt": 900, "completion": 400, "latency": 300},
]

REGIONS = ["us-east", "eu-west", "ap-northeast"]


def generate_batch(size: int, at: datetime, error_rate: float) -> Dict[str, List[Dict[str, Any]]]:
    traces = []
    for _ in range(size):
        m = random.choice(MODELS)
        is_error = random.random() < error_rate
        # Only Anthropic reports cache counters in this sample set.
        uses_cache = m["provider"] == "anthropic" and random.random() < 0.4
        traces.append({
            "timestamp": at.isoformat(),
            "provider": m["provider"],
            "model_name": m["model"],
            "prompt_tokens": m["prompt"] + random.randint(-150, 300),
            "completion_tokens": 0 if is_error else m["completion"] + random.randint(-50, 150),
            "cache_read_tokens": random.randint(3000, 9000) if uses_cache else 0,
            "cache_write_tokens": 1200 if uses_cache else 0,
            "latency_ms": round(m["latency"] + random.uniform(-40, 400), 2),
            "status": "ERROR" if is_error else "SUCCESS",
            "error_message": "Rate limit (429) or context length exceeded" if is_error else None,
            "tags": ["prod-traffic", f"env-{random.choice(REGIONS)}"],
            "metadata": {"user_id": f"usr_{random.randint(100, 999)}"},
        })
    return {"traces": traces}


def run_simulation(
    api_url: str,
    api_key: str,
    batches: int,
    delay_sec: float,
    spread_hours: float,
    error_rate: float,
) -> int:
    endpoint = f"{api_url.rstrip('/')}/traces/batch"
    headers = {"X-API-Key": api_key} if api_key else {}
    now = datetime.now(timezone.utc)
    ingested = 0
    failures = 0

    print(f"Generating {batches} batches against {endpoint} ...")
    with httpx.Client(timeout=10.0, headers=headers) as client:
        for i in range(batches):
            # Oldest batch first, so the series reads left to right.
            offset = timedelta(hours=spread_hours * (batches - 1 - i) / max(1, batches - 1))
            payload = generate_batch(random.randint(3, 7), now - offset, error_rate)
            try:
                res = client.post(endpoint, json=payload)
                if res.status_code == 201:
                    ingested += len(payload["traces"])
                    print(f"[{i + 1}/{batches}] ingested {len(payload['traces'])} traces")
                elif res.status_code == 401:
                    print("Rejected: this server requires an API key (--api-key or AIMONITOR_API_KEY).")
                    return 1
                else:
                    failures += 1
                    print(f"[{i + 1}/{batches}] failed: HTTP {res.status_code}")
            except httpx.HTTPError as exc:
                failures += 1
                print(f"[{i + 1}/{batches}] connection error: {exc.__class__.__name__}")
            if delay_sec and i < batches - 1:
                time.sleep(delay_sec)

    print(f"Done. {ingested} traces ingested, {failures} batches failed.")
    return 1 if ingested == 0 else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=os.environ.get("AIMONITOR_API_URL", DEFAULT_API_URL))
    parser.add_argument("--api-key", default=os.environ.get("AIMONITOR_API_KEY", ""))
    parser.add_argument("--batches", type=int, default=10)
    parser.add_argument("--delay", type=float, default=0.3, help="Seconds between batches")
    parser.add_argument("--spread-hours", type=float, default=0.0,
                        help="Backdate events across this many hours")
    parser.add_argument("--error-rate", type=float, default=0.10)
    args = parser.parse_args()

    return run_simulation(
        api_url=args.api_url,
        api_key=args.api_key,
        batches=max(1, args.batches),
        delay_sec=max(0.0, args.delay),
        spread_hours=max(0.0, args.spread_hours),
        error_rate=min(1.0, max(0.0, args.error_rate)),
    )


if __name__ == "__main__":
    sys.exit(main())
