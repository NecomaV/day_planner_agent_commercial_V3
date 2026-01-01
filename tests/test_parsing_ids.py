from app.bot.parsing.text import extract_task_ids


def test_extract_task_ids_plain_numbers():
    assert extract_task_ids("27") == [27]
    assert extract_task_ids("27 28") == [27, 28]


def test_extract_task_ids_with_prefixes():
    assert extract_task_ids("ID27") == [27]
    assert extract_task_ids("id=27") == [27]
    assert extract_task_ids("ид 27") == [27]
    assert extract_task_ids("#27") == [27]
    assert extract_task_ids("№27") == [27]
