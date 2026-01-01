from app.i18n.core import t


def me_message(user, settings, locale: str = "ru") -> str:
    api_key_hint = t("cabinet.api_key_header_suffix", locale=locale) if settings.API_KEY else ""
    tz = user.timezone or settings.TZ
    cabinet_url = f"http://{settings.APP_HOST}:{settings.APP_PORT}/web"
    api_prefix = f"{user.api_key_prefix}..." if user.api_key_prefix else t("common.not_set", locale=locale)
    return t(
        "cabinet.me",
        locale=locale,
        telegram_chat_id=user.telegram_chat_id,
        timezone=tz,
        api_prefix=api_prefix,
        cabinet_url=cabinet_url,
        api_key_hint=api_key_hint,
    )


def token_message(token: str, locale: str = "ru") -> str:
    return t("cabinet.token", locale=locale, token=token)


def cabinet_message(user, routine, steps, pantry, workouts, settings, locale: str = "ru") -> str:
    api_key_hint = t("cabinet.api_key_header_suffix", locale=locale) if settings.API_KEY else ""
    tz = user.timezone or settings.TZ
    cabinet_url = f"http://{settings.APP_HOST}:{settings.APP_PORT}/web"
    api_prefix = f"{user.api_key_prefix}..." if user.api_key_prefix else t("common.not_set", locale=locale)
    status = t("common.active", locale=locale) if user.is_active else t("common.inactive", locale=locale)
    onboarded = t("common.yes", locale=locale) if user.onboarded else t("common.no", locale=locale)
    full_name = user.full_name or t("common.not_set", locale=locale)
    focus = user.primary_focus or t("common.not_set", locale=locale)
    latest_task_end = routine.latest_task_end or t("common.not_set", locale=locale)
    return t(
        "cabinet.full",
        locale=locale,
        cabinet_url=cabinet_url,
        api_key_hint=api_key_hint,
        api_prefix=api_prefix,
        user_id=user.id,
        full_name=full_name,
        primary_focus=focus,
        timezone=tz,
        workday_start=routine.workday_start,
        workday_end=routine.workday_end,
        latest_task_end=latest_task_end,
        task_buffer_after_min=routine.task_buffer_after_min,
        status=status,
        onboarded=onboarded,
        routine_steps=len(steps),
        pantry_items=len(pantry),
        workout_plans=len(workouts),
    )
