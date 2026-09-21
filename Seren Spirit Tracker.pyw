from pathlib import Path
import runpy


SCRIPT_PATH = Path(__file__).resolve().with_name("seren_watcher_gui.py")
runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
