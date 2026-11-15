from freshrss_mcp.text import strip_html, truncate


def test_strip_html_drops_tags_scripts_and_decodes_entities() -> None:
    html = "<p>Tom &amp; <b>Jerry</b></p><script>alert(1)</script><style>p{}</style><div>Next</div>"
    assert strip_html(html) == "Tom & Jerry\nNext"


def test_strip_html_collapses_whitespace() -> None:
    assert strip_html("<p>a   b\n\n c</p><br><br><p> d </p>") == "a b c\nd"


def test_truncate() -> None:
    assert truncate("short", 10) == ("short", False)
    assert truncate("abcdefghij", 4) == ("abcd…", True)
    assert truncate("abc", 0) == ("…", True)
