from app.services.tools.previews import tool_call_action_summary


def test_action_summary_web_search():
    assert (
        tool_call_action_summary("web_search", {"queries": ["cats near me"]})
        == "Looking up cats near me"
    )


def test_action_summary_web_scrape_uses_host():
    assert (
        tool_call_action_summary(
            "web_scrape", {"urls": ["https://example.com/docs/page"]}
        )
        == "Exploring example.com"
    )


def test_action_summary_web_scrape_includes_question():
    assert (
        tool_call_action_summary(
            "web_scrape",
            {
                "url": "https://www.rotorama.de/product/walksnail-avatar-hd-moonlight-kit",
                "question": "What is the current price and stock status?",
            },
        )
        == "Exploring www.rotorama.de: What is the current price and stock status?"
    )


def test_action_summary_code_execution_prefers_purpose():
    assert (
        tool_call_action_summary(
            "code_execution",
            {
                "purpose": "Summarize sales CSV by region",
                "code": "import pandas as pd\ndf = pd.read_csv('/inputs/sales.csv')\n",
            },
        )
        == "Running code (Summarize sales CSV by region)"
    )


def test_action_summary_code_execution_falls_back_to_first_code_line():
    code = "# setup\nimport pandas as pd\ndf = pd.DataFrame()\n"
    assert (
        tool_call_action_summary("code_execution", {"code": code})
        == "Running code (import pandas as pd)"
    )


def test_action_summary_unknown_tool():
    assert tool_call_action_summary("custom_tool", {}) == "Running custom tool"
