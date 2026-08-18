"""CLI script to generate realistic LLM traffic to AIMONITOR backend."""
import random
import time
import httpx

API_URL = "http://localhost:8000/api/v1/traces/batch"

MODELS = [
    {"provider": "openai", "model": "gpt-4o", "base_prompt": 1200, "base_comp": 450, "base_lat": 420},
    {"provider": "openai", "model": "gpt-4o-mini", "base_prompt": 600, "base_comp": 250, "base_lat": 180},
    {"provider": "anthropic", "model": "claude-3-5-sonnet", "base_prompt": 1800, "base_comp": 800, "base_lat": 680},
    {"provider": "anthropic", "model": "claude-3-5-haiku", "base_prompt": 500, "base_comp": 200, "base_lat": 230},
    {"provider": "gemini", "model": "gemini-2.5-flash", "base_prompt": 3000, "base_comp": 900, "base_lat": 520},
    {"provider": "gemini", "model": "gemini-2.0-flash", "base_prompt": 800, "base_comp": 350, "base_lat": 150},
    {"provider": "ollama", "model": "llama3.1:8b", "base_prompt": 700, "base_comp": 300, "base_lat": 850},
]


def generate_batch(size: int = 5):
    traces = []
    for _ in range(size):
        m = random.choice(MODELS)
        is_err = random.random() < 0.10  # 10% error
        traces.append({
            "provider": m["provider"],
            "model_name": m["model"],
            "prompt_tokens": m["base_prompt"] + random.randint(-150, 300),
            "completion_tokens": 0 if is_err else (m["base_comp"] + random.randint(-50, 150)),
            "latency_ms": round(m["base_lat"] + random.uniform(-40, 200), 2),
            "status": "ERROR" if is_err else "SUCCESS",
            "error_message": "Rate limit (429) or context exceeded" if is_err else None,
            "tags": ["prod-traffic", f"env-{random.choice(['us-east', 'eu-west', 'ap-northeast'])}"],
            "metadata": {"user_id": f"usr_{random.randint(100, 999)}"},
        })
    return {"traces": traces}


def run_simulation(batches: int = 10, delay_sec: float = 1.0):
    print(f"🚀 Generating {batches} batches of simulated LLM traffic to {API_URL}...")
    with httpx.Client(timeout=5.0) as client:
        for i in range(batches):
            payload = generate_batch(size=random.randint(3, 7))
            try:
                res = client.post(API_URL, json=payload)
                if res.status_code == 201:
                    print(f"[{i+1}/{batches}] Ingested {len(payload['traces'])} traces successfully.")
                else:
                    print(f"[{i+1}/{batches}] Failed: HTTP {res.status_code}")
            except Exception as e:
                print(f"[{i+1}/{batches}] Error connecting to backend: {e}")
            time.sleep(delay_sec)
    print("✨ Simulation finished!")


if __name__ == "__main__":
    run_simulation(batches=10, delay_sec=0.5)
