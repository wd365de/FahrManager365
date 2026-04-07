import bcrypt
from fastapi import Request
from sqlalchemy.orm import Session

from app.models import User


SESSION_USER_ID_KEY = "user_id"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def login_user(request: Request, user: User) -> None:
    request.session[SESSION_USER_ID_KEY] = user.id


def logout_user(request: Request) -> None:
    request.session.clear()


def get_session_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get(SESSION_USER_ID_KEY)
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()
