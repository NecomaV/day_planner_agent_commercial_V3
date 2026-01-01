from app.bot.parsing.ru_reply import parse_reply


def test_yes_variants():
    samples = [
        "Да",
        "да!!!",
        "ага",
        "угу",
        "ок",
        "ок 😊",
        "ОК!",
        "окей",
        "конечно",
        "разумеется",
        "подтверждаю",
        "согласен",
        "согласна",
        "да, конечно",
        "да-да",
        "ага да",
        "можно",
        "делай",
        "ok",
        "yes",
    ]
    for text in samples:
        flags = parse_reply(text)
        assert flags.is_yes, text


def test_no_variants():
    samples = [
        "нет",
        "неа",
        "не нужно",
        "не надо",
        "пока нет",
        "скорее нет",
        "не согласен",
        "не согласна",
        "не хочу",
        "не сейчас",
        "нет, спасибо",
        "no",
        "nope",
    ]
    for text in samples:
        flags = parse_reply(text)
        assert flags.is_no, text


def test_cancel_variants():
    samples = [
        "отмена",
        "отмени",
        "стоп",
        "стоп, отмена",
        "прекрати",
        "перестань",
        "передумал",
        "назад",
        "cancel",
        "abort",
    ]
    for text in samples:
        flags = parse_reply(text)
        assert flags.is_cancel, text


def test_help_variants():
    samples = [
        "помощь",
        "справка",
        "что умеешь",
        "что ты умеешь",
        "как пользоваться",
        "покажи команды",
        "help",
        "help me",
    ]
    for text in samples:
        flags = parse_reply(text)
        assert flags.is_help, text
