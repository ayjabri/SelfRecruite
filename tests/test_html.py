from jobagent.parse.html import sanitize_html


def test_sanitize_keeps_structure_and_drops_the_rest():
    html = ('<div class="x" onclick="evil()">Intro<script>bad()</script><p style="a">&nbsp;</p>'
            '<p><strong>Job responsibilities</strong></p><ul><li>Lead <em>teams</em></li></ul>'
            '<h2><strong>About</strong></h2><a href="javascript:x">no</a> <a href="https://ok.example">yes</a></div>')
    out = sanitize_html(html)
    assert "<script" not in out and "onclick" not in out and "style=" not in out and "javascript" not in out
    assert "<h3>Job responsibilities</h3>" in out          # bold-only paragraph reads as a heading
    assert "<ul><li>Lead <em>teams</em></li></ul>" in out
    assert '<a href="https://ok.example" rel="noopener" target="_blank">yes</a>' in out
    assert "&nbsp;" not in out                              # empty spacer paragraph dropped
    assert sanitize_html(None) == "" and sanitize_html("  ") == ""
