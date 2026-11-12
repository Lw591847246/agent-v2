from unittest.mock import patch

from app.context.attachment_resolver import AttachmentResolver


def test_resolve_base64():
    r = AttachmentResolver()
    result = r.resolve_many([
        {"base64": "aGVsbG8=", "name": "a.png", "type": "image"},
    ])
    assert len(result) == 1
    assert result[0].type == "image"
    assert result[0].name == "a.png"


def test_resolve_file_id_not_found():
    r = AttachmentResolver()
    with patch("app.utils.file_registry.file_registry.get", return_value=None):
        result = r.resolve_many([{"file_id": "missing", "type": "image"}])
        assert result == []


def test_guess_type():
    assert AttachmentResolver._guess_type("a.mp4") == "video"
    assert AttachmentResolver._guess_type("a.pdf") == "doc"
    assert AttachmentResolver._guess_type("a.wav") == "audio"