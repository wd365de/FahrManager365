from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import PushSubscription
from app.push_notifications import get_vapid_public_key, has_push_config, send_push_payload
from app.routes.utils import get_authenticated_user

router = APIRouter()


class PushKeysIn(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionIn(BaseModel):
    endpoint: str
    keys: PushKeysIn


class PushUnsubscribeIn(BaseModel):
    endpoint: str


@router.get("/api/push/config")
def push_config():
    return {
        "supported": has_push_config(),
        "public_key": get_vapid_public_key(),
    }


@router.post("/api/push/subscribe")
def push_subscribe(
    payload: PushSubscriptionIn,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_authenticated_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")

    endpoint = payload.endpoint.strip()
    if not endpoint:
        raise HTTPException(status_code=400, detail="Endpoint fehlt")

    subscription = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).first()
    if not subscription:
        subscription = PushSubscription(
            user_id=user.id,
            endpoint=endpoint,
            p256dh=payload.keys.p256dh.strip(),
            auth=payload.keys.auth.strip(),
            user_agent=(request.headers.get("user-agent") or "")[:255],
        )
        db.add(subscription)
    else:
        subscription.user_id = user.id
        subscription.p256dh = payload.keys.p256dh.strip()
        subscription.auth = payload.keys.auth.strip()
        subscription.user_agent = (request.headers.get("user-agent") or "")[:255]
        subscription.updated_at = datetime.utcnow()

    db.commit()
    return {"ok": True}


@router.post("/api/push/unsubscribe")
def push_unsubscribe(
    payload: PushUnsubscribeIn,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_authenticated_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")

    endpoint = payload.endpoint.strip()
    if not endpoint:
        raise HTTPException(status_code=400, detail="Endpoint fehlt")

    subscription = (
        db.query(PushSubscription)
        .filter(PushSubscription.endpoint == endpoint, PushSubscription.user_id == user.id)
        .first()
    )
    if subscription:
        db.delete(subscription)
        db.commit()

    return {"ok": True}


@router.post("/api/push/test")
def push_test(request: Request, db: Session = Depends(get_db)):
    user = get_authenticated_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")

    if not has_push_config():
        raise HTTPException(status_code=503, detail="Push-Konfiguration fehlt")

    subscriptions = db.query(PushSubscription).filter(PushSubscription.user_id == user.id).all()
    if not subscriptions:
        raise HTTPException(status_code=400, detail="Keine aktive Push-Subscription gefunden")

    payload = {
        "title": "FahrManager Erinnerung",
        "body": "Dies ist eine Test-Benachrichtigung fuer dein Handy.",
        "url": "/portal",
    }

    success_count = 0
    for subscription in subscriptions:
        if send_push_payload(subscription, payload):
            success_count += 1

    return {
        "ok": True,
        "sent": success_count,
        "total": len(subscriptions),
    }
