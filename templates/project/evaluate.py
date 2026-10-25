"""Example entry: uses the framework evaluate contract."""
from pathlib import Path
import sys
# Works in templates/project/ or after copying to workspace/.
root = next(p for p in Path(__file__).resolve().parents if (p / "loop_cli.py").is_file())
sys.path.insert(0, str(root))
from evaluate import main
if __name__ == "__main__":
    raise SystemExit(main())
