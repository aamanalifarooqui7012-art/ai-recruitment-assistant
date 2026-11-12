"""Run the whole Microsoft Agent Framework workflow (Phase 1 -> Phase 2 -> Phase 2.5 -> HR) from the command line.
Examples are in the docstring of src/maf/run.py;  python scripts/run_workflow.py --help"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # repo/src on the import path

from maf.run import main  # noqa: E402

if __name__ == "__main__":
    main()
