"""The UI's page lets a person pinch to zoom: its viewport (ui/index.html, the page the phone app and the desktop app
bundle and the workspace serves) names no maximum scale and does not turn zooming off. A maximum scale of 1 would also
stop iOS zooming a page in when a small field takes focus, but in the phone app's WebView it stops a person's pinch as
well, so that a person who needs larger text could not get it; the fields are at 16 pixels or more on a touch screen
instead (tests/test_phone_fit_layout.py; PHONE-TEST)."""

import os
import re

INDEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui", "index.html")


def test_the_pages_viewport_lets_a_person_pinch_to_zoom():
    html = open(INDEX, encoding="utf-8").read()
    tags = re.findall(r'<meta\s+name="viewport"\s+content="([^"]*)"', html)
    assert len(tags) == 1, tags                         # the one viewport the page has, read
    parts = dict(p.split("=", 1) for p in (q.replace(" ", "") for q in tags[0].split(",")) if "=" in p)
    assert parts.get("width") == "device-width", parts
    assert "maximum-scale" not in parts, parts
    assert parts.get("user-scalable", "yes") not in ("no", "0"), parts
