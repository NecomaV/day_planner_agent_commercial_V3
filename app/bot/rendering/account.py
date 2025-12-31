def me_message(user, settings) -> str:
    api_key_hint = " + X-API-Key" if settings.API_KEY else ""
    tz = user.timezone or settings.TZ
    cabinet_url = f"http://{settings.APP_HOST}:{settings.APP_PORT}/web"
    api_prefix = f"{user.api_key_prefix}..." if user.api_key_prefix else "not set"
    return f"""Cabinet info:
- telegram_chat_id: {user.telegram_chat_id}
- timezone: {tz}
- api_token: {api_prefix}

Cabinet: {cabinet_url}
Auth: Authorization: Bearer <api_token>{api_key_hint}
Use /token to rotate and reveal a new token."""


def token_message(token: str) -> str:
    return f"""Your API token (save it now; it will not be shown again):
{token}"""


def cabinet_message(user, routine, steps, pantry, workouts, settings) -> str:
    api_key_hint = " + X-API-Key" if settings.API_KEY else ""
    tz = user.timezone or settings.TZ
    cabinet_url = f"http://{settings.APP_HOST}:{settings.APP_PORT}/web"
    api_prefix = f"{user.api_key_prefix}..." if user.api_key_prefix else "not set"
    status = "active" if user.is_active else "inactive"
    onboarded = "yes" if user.onboarded else "no"
    return f"""Cabinet: {cabinet_url}
Auth: Authorization: Bearer <api_token>{api_key_hint}
API token: {api_prefix} (use /token to rotate)

Profile:
- id: {user.id}
- name: {user.full_name or 'not set'}
- focus: {user.primary_focus or 'not set'}
- timezone: {tz}
- workday: {routine.workday_start}-{routine.workday_end}
- latest_task_end: {routine.latest_task_end or 'not set'}
- task_buffer_after: {routine.task_buffer_after_min} min
- status: {status}
- onboarded: {onboarded}

Routine steps: {len(steps)}
Pantry items: {len(pantry)}
Workout plans: {len(workouts)}"""
