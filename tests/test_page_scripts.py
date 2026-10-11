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


def test_scripts_do_not_redeclare_each_others_names():
    """The page loads every script into one global scope: a second `const x` in another file stops that
    file from loading at all. Parse them together, in the order index.html loads them."""
    import re
    html = (STATIC.parent / "index.html").read_text()
    names = re.findall(r'<script src="/static/([\w.-]+)"', html)
    assert names, "index.html loads no scripts?"
    src = "\n".join((STATIC / n).read_text() for n in names)
    mini_racer.MiniRacer().eval("(function(){\n" + src + "\n})")
