from app.api.chats import _effective_web_tool_enabled


def test_effective_web_tool_enabled_honors_request_disable():
    assert _effective_web_tool_enabled(True, False) is False


def test_effective_web_tool_enabled_keeps_org_disable():
    assert _effective_web_tool_enabled(False, True) is False
    assert _effective_web_tool_enabled(False, None) is False


def test_effective_web_tool_enabled_defaults_to_org_setting():
    assert _effective_web_tool_enabled(True, None) is True
    assert _effective_web_tool_enabled(True, True) is True
