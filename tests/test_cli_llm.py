"""Tests for LLM CLI commands (call and chat)."""
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from skyward.cli import cli


def test_llm_call_command_exists():
    runner = CliRunner()
    result = runner.invoke(cli, ["llm", "call", "--help"])
    assert result.exit_code == 0
    assert "--provider" in result.output
    assert "--model" in result.output
    assert "--message" in result.output


def test_llm_call_has_all_options():
    runner = CliRunner()
    result = runner.invoke(cli, ["llm", "call", "--help"])
    assert result.exit_code == 0
    assert "--system" in result.output
    assert "--temperature" in result.output
    assert "--max-tokens" in result.output
    assert "--api-key" in result.output


def test_llm_chat_command_exists():
    runner = CliRunner()
    result = runner.invoke(cli, ["llm", "chat", "--help"])
    assert result.exit_code == 0
    assert "--provider" in result.output
    assert "--model" in result.output


def test_llm_chat_has_all_options():
    runner = CliRunner()
    result = runner.invoke(cli, ["llm", "chat", "--help"])
    assert result.exit_code == 0
    assert "--system" in result.output
    assert "--api-key" in result.output
    assert "--summarize-tokens" in result.output


@patch("skyward.llm.get_provider")
@patch("skyward.llm.calculate_cost", return_value=0.0042)
@patch("skyward.llm.format_cost", return_value="$0.0042")
def test_llm_call_invokes_provider(mock_format, mock_cost, mock_get_provider):
    fake_provider = MagicMock()
    fake_provider.call.return_value = ("Hello world!", 10, 20)
    mock_get_provider.return_value = fake_provider

    runner = CliRunner()
    result = runner.invoke(cli, [
        "llm", "call",
        "--provider", "openai",
        "--model", "gpt-4o",
        "--message", "Hi",
    ])
    assert result.exit_code == 0
    assert "Hello world!" in result.output
    assert "10 in / 20 out" in result.output
    assert "$0.0042" in result.output
    fake_provider.call.assert_called_once()


@patch("skyward.llm.get_provider")
@patch("skyward.llm.session.LLMSession")
def test_llm_chat_interactive_session(mock_session_cls, mock_get_provider):
    fake_provider = MagicMock()
    mock_get_provider.return_value = fake_provider

    fake_session = MagicMock()
    fake_session.send.return_value = "Bot reply"
    fake_session.total_input_tokens = 15
    fake_session.total_output_tokens = 25
    fake_session.messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "Bot reply"}]
    mock_session_cls.return_value = fake_session

    runner = CliRunner()
    result = runner.invoke(cli, [
        "llm", "chat",
        "--provider", "openai",
        "--model", "gpt-4o",
    ], input="hello\nquit\n")
    assert result.exit_code == 0
    assert "Bot reply" in result.output
    fake_session.send.assert_called_once()
