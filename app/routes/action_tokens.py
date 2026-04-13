import os
from itsdangerous import URLSafeTimedSerializer

_TOKEN_SALT = "wa-appointment-action"
_TOKEN_MAX_AGE = 86400 * 7  # 7 Tage


def make_action_token(appointment_id: int, action: str) -> str:
    secret = os.getenv("SESSION_SECRET", "dev-secret")
    s = URLSafeTimedSerializer(secret, salt=_TOKEN_SALT)
    return s.dumps({"id": appointment_id, "action": action})


def verify_action_token(token: str, appointment_id: int, action: str) -> bool:
    secret = os.getenv("SESSION_SECRET", "dev-secret")
    s = URLSafeTimedSerializer(secret, salt=_TOKEN_SALT)
    try:
        data = s.loads(token, max_age=_TOKEN_MAX_AGE)
        return data.get("id") == appointment_id and data.get("action") == action
    except Exception:
        return False
