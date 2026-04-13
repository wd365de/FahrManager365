from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import login_user, logout_user, verify_password
from app.database import get_db
from app.models import User
from app.routes.utils import get_authenticated_user

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_authenticated_user(request, db)
    if current_user:
        if current_user.role == "student":
            return RedirectResponse(url="/portal", status_code=302)
        if current_user.role == "teacher":
            return RedirectResponse(url="/appointments", status_code=302)
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Ungültige E-Mail oder Passwort."},
            status_code=400,
        )

    login_user(request, user)
    if user.role == "student":
        return RedirectResponse(url="/portal", status_code=302)
    if user.role == "teacher":
        return RedirectResponse(url="/appointments", status_code=302)
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse(url="/login", status_code=302)
