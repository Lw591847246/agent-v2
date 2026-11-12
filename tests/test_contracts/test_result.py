# tests/test_contracts/test_result.py
from app.contracts.result import (
    Result, build_output, error_part, image_part, text_part,
)


def test_text_part():
    p = text_part("hello")
    assert p["type"] == "text"
    assert p["content"] == "hello"


def test_result_text():
    r = Result.text("hi")
    assert r.as_text() == "hi"


def test_result_error():
    r = Result.error("oops")
    assert r.as_text().startswith("❌")


def test_build_output():
    o = build_output(
        "image", "a.png", "/tmp/a.png",
        "http://x/a.png", thumb="http://x/t.jpg",
    )
    assert o["kind"] == "image"
    assert o["thumb"] == "http://x/t.jpg"


def test_image_part_no_thumb():
    p = image_part("http://x/a.png")
    assert "thumb_url" not in p