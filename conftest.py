"""Root test setup, loaded before any test module imports the app."""
import os

# Starlette's TestClient sends `Host: testserver`; without API_KEY the server answers only local names.
os.environ.setdefault("ALLOWED_HOSTS", "testserver")
