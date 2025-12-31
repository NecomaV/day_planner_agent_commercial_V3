from app.bot.parsing.commands import parse_command_text, parse_yes_no


def test_parse_command_text():
    assert parse_command_text("/todo 30 check mail") == ("todo", ["30", "check", "mail"])
    assert parse_command_text("hello") is None


def test_parse_yes_no():
    assert parse_yes_no("\u0434\u0430") is True
    assert parse_yes_no("\u043d\u0435\u0442") is False
    assert parse_yes_no("maybe") is None
