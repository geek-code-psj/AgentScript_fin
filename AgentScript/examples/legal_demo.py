"""Convenience entry-point for the Week 6 legal demo."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentscript.demo.legal_demo import main


if __name__ == "__main__":
    raise SystemExit(main())
