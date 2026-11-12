from app.scheduler.fast_path import try_fast_path
from app.scheduler.intents import Intent


def test_empty():
    r = try_fast_path("", False, [])
    assert r["intent"] == Intent.CHAT


def test_short_no_att():
    r = try_fast_path("hi", False, [])
    assert r["intent"] == Intent.CHAT


def test_ppt_keyword():
    r = try_fast_path("帮我做ppt", False, [])
    assert r["intent"] == Intent.PPT_GENERATION


def test_translation_keyword():
    r = try_fast_path("翻译成英文", False, [])
    assert r["intent"] == Intent.TRANSLATION


def test_no_hit():
    r = try_fast_path("这是一个比较长的句子用来测试是否不会被快速路径命中", False, [])
    assert r is None