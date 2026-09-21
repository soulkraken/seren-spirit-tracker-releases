import sys
from pathlib import Path


python_root = Path(sys.base_prefix)
datas = [
    (str(python_root / "tcl" / "tcl8.6"), "_tcl_data"),
    (str(python_root / "tcl" / "tk8.6"), "_tk_data"),
]
