from app.context.reference_resolver import (
    ReferenceResolver, pick_outputs_strict, resolve_referent,
)


def _history_with_outputs():
    return [
        {"id": 1, "role": "user", "content": "画一张图"},
        {"id": 2, "role": "assistant", "content": "", "outputs": [
            {"kind": "image", "filename": "a.png", "url": "/img/a"},
        ]},
        {"id": 3, "role": "user", "content": "再画一张"},
        {"id": 4, "role": "assistant", "content": "", "outputs": [
            {"kind": "image", "filename": "b.png", "url": "/img/b"},
        ]},
    ]


def test_resolve_referent_last_turn():
    r = resolve_referent("把上一轮的图改暗")
    assert r is not None
    assert r.kind == "image"
    assert r.turn_offset == -1


def test_resolve_referent_index():
    r = resolve_referent("用第一张图")
    assert r is not None
    assert r.index == 1


def test_pick_outputs_strict_ok():
    referent = resolve_referent("上一轮的图")
    result = pick_outputs_strict(_history_with_outputs(), referent)
    assert result["reason"] == "ok"
    assert result["outputs"][0]["filename"] == "b.png"


def test_reference_resolver_returns_attachments():
    r = ReferenceResolver()
    atts = r.resolve("上一轮的图改暗", _history_with_outputs())
    assert len(atts) == 1
    assert atts[0].name == "b.png"


def test_no_reference_returns_empty():
    r = ReferenceResolver()
    atts = r.resolve("画一只猫", _history_with_outputs())
    assert atts == []