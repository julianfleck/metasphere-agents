"""Make brain/ importable for tests without turning brain/ into a
package (brain.cli.py uses bare ``from explore import ...`` which
relies on script-mode sys.path[0]; an __init__.py would not break
that, but staying script-shaped matches the rest of brain/)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
