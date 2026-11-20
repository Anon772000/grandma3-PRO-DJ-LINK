"""Start the standalone audio-backed test player from a source checkout."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from simulator.app import main

if __name__ == '__main__':
    main()
