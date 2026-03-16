import os
from pathlib import Path


def pytest_configure(config):
    """Load .env file before tests run."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        # Resolve symlinks
        env_path = env_path.resolve()
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())
