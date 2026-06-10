"""Root pytest configuration for brainengine."""
import sys
from pathlib import Path

# Ensure brainengine root is in sys.path for all tests
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
