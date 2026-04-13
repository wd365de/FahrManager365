from datetime import datetime, timedelta
import os
from pathlib import Path
import smtplib
from email.message import EmailMessage

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.exc import IntegrityError
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Appointment, AvailabilityWindow, User
from app.push_notifications import has_push_config, notify_admins, notify_user
from app.whatsapp import (
    has_whatsapp_config,
    notify_appointment_booked,
    notify_appointment_confirmed,
    notify_appointment_cancelled,
    notify_teacher_new_booking,
)
from app.settings import ALLOWED_APPOINTMENT_DURATIONS, BOOKING_BUFFER_MINUTES
from app.settings import STUDENT_DIRECT_BOOKING_START_LEAD_HOURS
from app.routes.utils import (
    get_authenticated_user,
    has_appointment_overlap,
    parse_iso_datetime,
    redirect_to_login,
)

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_TOKEN_SALT = "wa-appointment-action"
_TOKEN_MAX_AGE = 86400 * 7  # 7 Tage


def _make_action_token(appointment_id: int, action: str) -> str:
    secret = os.getenv("SESSION_SECRET", "dev-secret")
    s = URLSafeTimedSerializer(secret, salt=_TOKEN_SALT)
    return s.dumps({"id": appointment_id, "action": action})


def _verify_action_token(token: str, appointment_id: int, action: str) -> bool:
    secret = os.getenv("SESSION_SECRET", "dev-secret")
    s = URLSafeTimedSerializer(secret, salt=_TOKEN_SALT)
    try:
        data = s.loads(token, max_age=_TOKEN_MAX_AGE)
        return data.get("id") == appointment_id and data.get("action") == action
    except Exception:
        return False


def send_booking_notification_email(db: Session, appointment: Appointment, is_request: bool) -> None:
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    if not smtp_host:
        return

    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    smtp_from = os.getenv("SMTP_FROM", smtp_user or "no-reply@fahrmanager360.local").strip()

    smtp_to = os.getenv("SMTP_TO", "").strip()
    if smtp_to:
        recipients = [item.strip() for item in smtp_to.split(",") if item.strip()]
    else:
        recipients = [
            user.email
            for user in db.query(User).filter(User.role == "admin").all()
            if user.email
        ]

    if not recipients:
        return

    student_name = appointment.student.user.name if appointment.student and appointment.student.user else "Unbekannt"
    teacher_name = appointment.teacher.user.name if appointment.teacher and appointment.teacher.user else "Unbekannt"

    if is_request:
        subject = "Neue Fahrstunden-Anfrage"
        intro = "Es wurde eine neue Fahrstunden-Anfrage gestellt."
        request_text = (appointment.request_message or "").strip() or "(kein Text)"
        extra_lines = ["", f"Anfrage: {request_text}"]
    else:
        subject = "Neuer Termin gebucht"
        intro = "Ein Schüler hat einen Termin direkt gebucht."
        extra_lines = []

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_from
    message["To"] = ", ".join(recipients)
    message.set_content(
        "\n".join(
            [
                intro,
                "",
                f"Schüler: {student_name}",
                f"Fahrlehrer: {teacher_name}",
                f"Start: {appointment.start_at.strftime('%d.%m.%Y %H:%M')}",
                f"Ende: {appointment.end_at.strftime('%d.%m.%Y %H:%M')}",
                f"Dauer: {appointment.duration_min} Minuten",
            ]
            + extra_lines
        )
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            if smtp_user and smtp_password:
                smtp.login(smtp_user, smtp_password)
            smtp.send_message(message)
    except Exception:
        return


@router.get("/appointments")
def appointments_list(request: Request, db: Session = Depends(get_db)):
    user = get_authenticated_user(request, db)
    if not user:
        return redirect_to_login()

    query = db.query(Appointment).order_by(Appointment.start_at.asc())
    if user.role == "teacher" and user.teacher:
        query = query.filter(Appointment.teacher_id == user.teacher.id)
    elif user.role == "student" and user.student:
        query = query.filter(Appointment.student_id == user.student.id, Appointment.is_closed == False)

    appointments = query.all()
    return templates.TemplateResponse(
        "appointments_list.html",
        {"request": request, "user": user, "appointments": appointments, "push_mvp_available": has_push_config()},
    )


@router.post("/appointments/book")
def book_appointment(
    request: Request,
    window_id: int = Form(...),
    start_at: str = Form(...),
    duration_min: int = Form(...),
    request_message: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_authenticated_user(request, db)
    if not user:
        return redirect_to_login()

    if user.role != "student" or not user.student:
        return RedirectResponse(url="/appointments", status_code=302)

    if duration_min not in ALLOWED_APPOINTMENT_DURATIONS:
        return RedirectResponse(url="/portal", status_code=302)

    window = db.query(AvailabilityWindow).filter(AvailabilityWindow.id == window_id).first()
    if not window:
        return RedirectResponse(url="/portal", status_code=302)

    if not user.student.teacher_id or window.teacher_id != user.student.teacher_id:
        return RedirectResponse(url="/portal", status_code=302)

    start_dt = parse_iso_datetime(start_at)
    end_dt = start_dt + timedelta(minutes=duration_min)

    if start_dt < datetime.now():
        return RedirectResponse(url="/portal", status_code=302)

    booking_until = datetime.now() + timedelta(hours=STUDENT_DIRECT_BOOKING_START_LEAD_HOURS)
    if start_dt > booking_until:
        return RedirectResponse(url="/portal", status_code=302)

    requires_teacher_confirmation = True  # Fahrlehrer muss jeden Termin bestätigen

    if datetime.now() < window.bookable_from:
        return RedirectResponse(url="/portal", status_code=302)

    if start_dt < window.start_at or end_dt > window.end_at:
        return RedirectResponse(url="/portal", status_code=302)

    has_overlap = has_appointment_overlap(
        db,
        teacher_id=window.teacher_id,
        start_at=start_dt,
        end_at=end_dt,
        buffer_minutes=BOOKING_BUFFER_MINUTES,
    )
    if has_overlap:
        return RedirectResponse(url="/portal", status_code=302)

    appointment = Appointment(
        student_id=user.student.id,
        teacher_id=window.teacher_id,
        start_at=start_dt,
        end_at=end_dt,
        duration_min=duration_min,
        status="booked",
        requires_teacher_confirmation=requires_teacher_confirmation,
        request_message=request_message.strip() or None,
        is_request_seen_by_admin=not requires_teacher_confirmation,
    )
    db.add(appointment)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(url="/portal", status_code=302)

    db.refresh(appointment)
    student_name = appointment.student.user.name if appointment.student and appointment.student.user else "Schüler"
    start_fmt = appointment.start_at.strftime("%d.%m.%Y %H:%M")
    base_url = str(request.base_url).rstrip("/")

    # WhatsApp an Fahrlehrer mit Bestätigungs-/Ablehnungslink
    teacher = appointment.teacher
    if teacher and teacher.whatsapp_phone and has_whatsapp_config():
        confirm_url = f"{base_url}/appointments/{appointment.id}/wa-confirm/{_make_action_token(appointment.id, 'confirm')}"
        reject_url = f"{base_url}/appointments/{appointment.id}/wa-reject/{_make_action_token(appointment.id, 'reject')}"
        notify_teacher_new_booking(teacher.whatsapp_phone, student_name, start_fmt, confirm_url, reject_url)

    # WhatsApp an Schüler: Eingangsbestätigung
    student = appointment.student
    if student and student.whatsapp_opted_in and student.whatsapp_phone and has_whatsapp_config():
        notify_appointment_booked(student_name, student.whatsapp_phone, start_fmt)

    # Push an Admins + Fahrlehrer
    notify_admins(db, "Neue Terminanfrage", f"{student_name} – {start_fmt}")
    if teacher:
        notify_user(db, teacher.user_id, "Neue Terminanfrage", f"{student_name} – {start_fmt}")
    send_booking_notification_email(db, appointment, is_request=True)

    return RedirectResponse(url="/portal?booked=1", status_code=302)


@router.post("/appointments/{appointment_id}/cancel")
def cancel_appointment(appointment_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_authenticated_user(request, db)
    if not user:
        return redirect_to_login()

    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment or appointment.status != "booked":
        return RedirectResponse(url="/appointments", status_code=302)

    is_owner_student = user.role == "student" and user.student and appointment.student_id == user.student.id
    is_admin = user.role == "admin"
    is_owner_teacher = user.role == "teacher" and user.teacher and appointment.teacher_id == user.teacher.id

    if not (is_owner_student or is_admin or is_owner_teacher):
        return RedirectResponse(url="/appointments", status_code=302)

    appointment.status = "cancelled"
    appointment.is_read_by_student = is_owner_student
    appointment.is_closed = True

    db.commit()
    db.refresh(appointment)

    start_fmt = appointment.start_at.strftime("%d.%m.%Y um %H:%M Uhr")
    student = appointment.student
    teacher = appointment.teacher

    if is_owner_student:
        # Schüler hat storniert → Push an Admin + Fahrlehrer
        notify_admins(db, "Termin storniert", f"{student.user.name if student else 'Schüler'} – {start_fmt}")
        if teacher:
            notify_user(db, teacher.user_id, "Termin storniert", f"{student.user.name if student else 'Schüler'} – {start_fmt}")
    else:
        # Admin oder Fahrlehrer hat storniert → Push + WhatsApp an Schüler
        if student:
            notify_user(db, student.user_id, "Termin storniert", f"Dein Termin am {start_fmt} wurde storniert.")
            if student.whatsapp_opted_in and student.whatsapp_phone and has_whatsapp_config():
                notify_appointment_cancelled(student.user.name, student.whatsapp_phone, start_fmt)

    if user.role == "student":
        return RedirectResponse(url="/portal", status_code=302)
    return RedirectResponse(url="/appointments", status_code=302)


@router.post("/appointments/{appointment_id}/confirm")
def confirm_appointment(appointment_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_authenticated_user(request, db)
    if not user:
        return redirect_to_login()

    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment or appointment.status != "booked" or not appointment.requires_teacher_confirmation:
        return RedirectResponse(url="/appointments", status_code=302)

    is_admin = user.role == "admin"
    is_owner_teacher = user.role == "teacher" and user.teacher and appointment.teacher_id == user.teacher.id
    if not (is_admin or is_owner_teacher):
        return RedirectResponse(url="/appointments", status_code=302)

    appointment.requires_teacher_confirmation = False
    appointment.is_read_by_student = False
    db.commit()
    db.refresh(appointment)

    student = appointment.student
    start_fmt = appointment.start_at.strftime("%d.%m.%Y um %H:%M Uhr")
    if student:
        notify_user(db, student.user_id, "Termin bestätigt", f"Dein Termin am {start_fmt} wurde bestätigt ✓")
        if student.whatsapp_opted_in and student.whatsapp_phone and has_whatsapp_config():
            notify_appointment_confirmed(student.user.name, student.whatsapp_phone, start_fmt)

    return RedirectResponse(url="/appointments", status_code=302)


@router.post("/appointments/{appointment_id}/reject")
def reject_appointment(appointment_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_authenticated_user(request, db)
    if not user:
        return redirect_to_login()

    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment or appointment.status != "booked" or not appointment.requires_teacher_confirmation:
        return RedirectResponse(url="/appointments", status_code=302)

    is_admin = user.role == "admin"
    is_owner_teacher = user.role == "teacher" and user.teacher and appointment.teacher_id == user.teacher.id
    if not (is_admin or is_owner_teacher):
        return RedirectResponse(url="/appointments", status_code=302)

    appointment.status = "cancelled"
    appointment.requires_teacher_confirmation = False
    appointment.is_read_by_student = True
    appointment.is_closed = True
    db.commit()
    db.refresh(appointment)
    student = appointment.student
    start_fmt = appointment.start_at.strftime("%d.%m.%Y um %H:%M Uhr")
    if student:
        notify_user(db, student.user_id, "Termin abgelehnt", f"Dein Termin am {start_fmt} wurde leider abgelehnt.")
        if student.whatsapp_opted_in and student.whatsapp_phone and has_whatsapp_config():
            notify_appointment_cancelled(student.user.name, student.whatsapp_phone, start_fmt)
    return RedirectResponse(url="/appointments", status_code=302)


_WA_PAGE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FahrManager</title>
<style>body{{font-family:sans-serif;text-align:center;padding:3rem 1rem;color:#0f172a}}
h2{{font-size:1.4rem}}p{{color:#475569}}</style></head>
<body><h2>{icon} {title}</h2><p>{body}</p></body></html>"""


@router.get("/appointments/{appointment_id}/wa-confirm/{token}")
def wa_confirm_appointment(appointment_id: int, token: str, db: Session = Depends(get_db)):
    if not _verify_action_token(token, appointment_id, "confirm"):
        return HTMLResponse(_WA_PAGE.format(icon="⚠️", title="Ungültiger Link", body="Dieser Link ist abgelaufen oder ungültig."), status_code=400)

    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        return HTMLResponse(_WA_PAGE.format(icon="⚠️", title="Nicht gefunden", body="Termin nicht gefunden."), status_code=404)
    if appointment.status == "cancelled":
        return HTMLResponse(_WA_PAGE.format(icon="ℹ️", title="Termin storniert", body="Dieser Termin wurde bereits storniert."))
    if not appointment.requires_teacher_confirmation:
        start_fmt = appointment.start_at.strftime("%d.%m.%Y um %H:%M Uhr")
        return HTMLResponse(_WA_PAGE.format(icon="✅", title="Bereits bestätigt", body=f"Der Termin am {start_fmt} wurde bereits bestätigt."))
    if appointment.status != "booked":
        return HTMLResponse(_WA_PAGE.format(icon="ℹ️", title="Bereits bearbeitet", body="Dieser Termin wurde bereits abgesagt."))

    appointment.requires_teacher_confirmation = False
    appointment.is_request_seen_by_admin = True
    appointment.is_read_by_student = False
    db.commit()
    db.refresh(appointment)

    student = appointment.student
    student_name = student.user.name if student and student.user else "Schüler"
    if student and student.whatsapp_opted_in and student.whatsapp_phone and has_whatsapp_config():
        start_fmt = appointment.start_at.strftime("%d.%m.%Y um %H:%M Uhr")
        notify_appointment_confirmed(student_name, student.whatsapp_phone, start_fmt)

    return HTMLResponse(_WA_PAGE.format(icon="✅", title="Termin bestätigt", body=f"{student_name} wurde per WhatsApp benachrichtigt."))


@router.get("/appointments/{appointment_id}/wa-reject/{token}")
def wa_reject_appointment(appointment_id: int, token: str, db: Session = Depends(get_db)):
    if not _verify_action_token(token, appointment_id, "reject"):
        return HTMLResponse(_WA_PAGE.format(icon="⚠️", title="Ungültiger Link", body="Dieser Link ist abgelaufen oder ungültig."), status_code=400)

    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        return HTMLResponse(_WA_PAGE.format(icon="⚠️", title="Nicht gefunden", body="Termin nicht gefunden."), status_code=404)
    if appointment.status == "cancelled":
        return HTMLResponse(_WA_PAGE.format(icon="ℹ️", title="Bereits storniert", body="Dieser Termin wurde bereits storniert."))
    if appointment.status != "booked":
        return HTMLResponse(_WA_PAGE.format(icon="ℹ️", title="Bereits bearbeitet", body="Dieser Termin wurde bereits abgeschlossen."))

    appointment.status = "cancelled"
    appointment.requires_teacher_confirmation = False
    appointment.is_request_seen_by_admin = True
    appointment.is_read_by_student = True
    appointment.is_closed = True
    db.commit()
    db.refresh(appointment)

    student = appointment.student
    student_name = student.user.name if student and student.user else "Schüler"
    if student and student.whatsapp_opted_in and student.whatsapp_phone and has_whatsapp_config():
        start_fmt = appointment.start_at.strftime("%d.%m.%Y um %H:%M Uhr")
        notify_appointment_cancelled(student_name, student.whatsapp_phone, start_fmt)

    return HTMLResponse(_WA_PAGE.format(icon="❌", title="Termin abgelehnt", body=f"{student_name} wurde per WhatsApp benachrichtigt."))
