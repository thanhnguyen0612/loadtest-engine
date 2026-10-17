"""Make the standalone janitor script importable as a module in tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
