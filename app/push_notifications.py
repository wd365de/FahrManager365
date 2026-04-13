import json
import os
from typing import Any

from app.models import PushSubscription

try:
    from pywebpush import WebPushException, webpush
except Exception:  # pragma: no cover
    WebPushException = Exception
    webpush = None


VAPID_PUBLIC_KEY_ENV = "VAPID_PUBLIC_KEY"
VAPID_PRIVATE_KEY_ENV = "VAPID_PRIVATE_KEY"
VAPID_CLAIMS_SUB_ENV = "VAPID_CLAIMS_SUB"


def get_vapid_public_key() -> str:
    return os.getenv(VAPID_PUBLIC_KEY_ENV, "").strip()


def has_push_config() -> bool:
    return bool(get_vapid_public_key() and os.getenv(VAPID_PRIVATE_KEY_ENV, "").strip())


def notify_admins(db: Any, title: str, body: str) -> None:
    """Send a push notification to all admin users who have push subscriptions."""
    from app.models import User
    admins = db.query(User).filter(User.role == "admin").all()
    for admin in admins:
        subs = db.query(PushSubscription).filter(PushSubscription.user_id == admin.id).all()
        for sub in subs:
            send_push_payload(sub, {"title": title, "body": body})


def notify_user(db: Any, user_id: int, title: str, body: str) -> None:
    """Send a push notification to a specific user."""
    subs = db.query(PushSubscription).filter(PushSubscription.user_id == user_id).all()
    for sub in subs:
        send_push_payload(sub, {"title": title, "body": body})


def send_push_payload(subscription: PushSubscription, payload: dict[str, Any]) -> bool:
    if webpush is None:
        return False

    vapid_private_key = os.getenv(VAPID_PRIVATE_KEY_ENV, "").strip()
    if not get_vapid_public_key() or not vapid_private_key:
        return False

    subscription_info = {
        "endpoint": subscription.endpoint,
        "keys": {
            "p256dh": subscription.p256dh,
            "auth": subscription.auth,
        },
    }

    vapid_claims_sub = os.getenv(VAPID_CLAIMS_SUB_ENV, "mailto:admin@fahrmanager360.local").strip()

    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=vapid_private_key,
            vapid_claims={"sub": vapid_claims_sub},
        )
        return True
    except WebPushException:
        return False
