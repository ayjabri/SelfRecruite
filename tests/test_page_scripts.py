"""Every page script must parse in a real JavaScript engine (a syntax error in one file kills the
whole page, which is how the filters once stopped working). Skipped when the engine is missing."""
import glob
from pathlib import Path

import pytest

mini_racer = pytest.importorskip("py_mini_racer")
STATIC = Path(__file__).resolve().parents[1] / "jobagent" / "web" / "static"


@pytest.mark.parametrize("path", sorted(glob.glob(str(STATIC / "*.js"))), ids=lambda p: Path(p).name)
def test_script_parses(path):
    ctx = mini_racer.MiniRacer()
    # compile without running: wrap in a never-called function so DOM access is not attempted
    src = Path(path).read_text()
    ctx.eval("(function(){\n" + src + "\n})")
