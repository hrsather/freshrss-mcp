from freshrss_mcp.text import clean, strip_html, truncate

RLO = chr(0x202E)
LRI = chr(0x2066)
ZWSP = chr(0x200B)
BOM = chr(0xFEFF)


def test_strip_html_drops_tags_scripts_and_decodes_entities() -> None:
    html = "<p>Tom &amp; <b>Jerry</b></p><script>alert(1)</script><style>p{}</style><div>Next</div>"
    assert strip_html(html) == "Tom & Jerry\nNext"


def test_strip_html_collapses_whitespace() -> None:
    assert strip_html("<p>a   b\n\n c</p><br><br><p> d </p>") == "a b c\nd"


def test_truncate() -> None:
    assert truncate("short", 10) == ("short", False)
    assert truncate("abcdefghij", 4) == ("abcd…", True)
    assert truncate("abc", 0) == ("…", True)


def test_strip_html_removes_invisible_tag_and_mark_characters() -> None:
    hidden = chr(0xE0069) + chr(0xE0067) + chr(0x200E) + chr(0x200F) + chr(0x061C)
    assert strip_html(f"<p>visible{hidden}</p>") == "visible"


def test_strip_html_keeps_joiners_that_emoji_and_persian_need() -> None:
    family = "\U0001f468" + chr(0x200D) + "\U0001f469"
    assert strip_html(f"<p>{family}</p>") == family
    word = "ab" + chr(0x200C) + "cd"
    assert strip_html(f"<p>{word}</p>") == word


def test_strip_html_removes_control_and_bidi_characters() -> None:
    assert strip_html(f"<p>\x1b[31mred\x07 {RLO}evil{ZWSP}{BOM}</p>") == "[31mred evil"


def test_clean_makes_single_line_text() -> None:
    assert clean(f"a\x1b\nb{LRI} c") == "a b c"
