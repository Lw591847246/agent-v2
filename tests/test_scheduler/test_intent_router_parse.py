from app.scheduler.intent_router import IntentRouter


def test_parse_json_plain():
    r = IntentRouter()
    data = r._parse_json('{"intent": "chat", "confidence": 0.9}')
    assert data["intent"] == "chat"


def test_parse_json_markdown():
    r = IntentRouter()
    data = r._parse_json('```json\n{"intent": "chat"}\n```')
    assert data["intent"] == "chat"


def test_parse_json_invalid():
    r = IntentRouter()
    data = r._parse_json("not json")
    assert data == {}


def test_validate_l2_out_of_domain():
    r = IntentRouter()
    result = r._validate_l2({"intent": "OUT_OF_DOMAIN"}, "general")
    assert result is None


def test_validate_l2_wrong_domain():
    r = IntentRouter()
    result = r._validate_l2({"intent": "ppt_generation"}, "general")
    assert result is None


def test_validate_l2_ok():
    r = IntentRouter()
    result = r._validate_l2(
        {"intent": "chat", "confidence": 0.9, "params": {"text": "hi"}},
        "general",
    )
    assert result is not None
    intent, conf, needs_agent, reason, params = result
    assert intent.value == "chat"
    assert params["text"] == "hi"