"""Sanitise a posting's own HTML for display: keep structure (headings, paragraphs, lists,
emphasis, links, tables), drop everything else (scripts, styles, attributes, iframes...)."""
import re

from bs4 import BeautifulSoup, Comment, NavigableString

KEEP = {"p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "u", "h1", "h2", "h3", "h4", "h5", "h6",
        "a", "table", "thead", "tbody", "tr", "th", "td", "blockquote", "hr", "code", "pre"}
DROP = {"script", "style", "iframe", "object", "embed", "form", "input", "button", "svg", "noscript", "head", "title", "meta", "link"}
_BLANK = re.compile(r"^[\s ]*$")


def sanitize_html(html: str | None) -> str:
    if not html or not html.strip():
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all(string=lambda s: isinstance(s, Comment)):
        node.extract()
    for tag in list(soup.find_all(True)):
        if tag.name in DROP:
            tag.decompose()
    for tag in list(soup.find_all(True)):
        name = tag.name.lower()
        if name in ("div", "section", "article"):
            if tag.find(["p", "div", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"]):
                tag.unwrap()  # a wrapper around blocks: its children carry the structure
                continue
            tag.name = "p"  # a block of text becomes a paragraph, keeping its line break
            name = "p"
        if name not in KEEP:
            tag.unwrap()
            continue
        href = tag.get("href") if name == "a" else None
        tag.attrs = {}
        if name == "a":
            if href and re.match(r"^https?://", href.strip(), re.I):
                tag.attrs = {"href": href.strip(), "target": "_blank", "rel": "noopener"}
            else:
                tag.unwrap()
    for tag in list(soup.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6"])):
        if not tag.find(["img", "br", "table"]) and _BLANK.match(tag.get_text()):
            tag.decompose()  # empty <p>&nbsp;</p> spacers
    # a heading that is only bold text, or a bold-only paragraph, reads as a heading
    for tag in list(soup.find_all("p")):
        kids = [k for k in tag.contents if not (isinstance(k, NavigableString) and _BLANK.match(str(k)))]
        if len(kids) == 1 and getattr(kids[0], "name", None) in ("strong", "b") and len(kids[0].get_text()) <= 80:
            tag.name = "h3"
            kids[0].unwrap()
    return str(soup).strip()
