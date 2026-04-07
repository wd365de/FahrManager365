from datetime import datetime, timedelta
import os
from pathlib import Path
import smtplib
from email.message import EmailMessage

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Appointment, AvailabilityWindow, User
from app.settings import ALLOWED_APPOINTMENT_DURATIONS, BOOKING_BUFFER_MINUTES
from app.settings import STUDENT_DIRECT_BOOKING_START_LEAD_HOURS, STUDENT_DIRECT_BOOKING_WINDOW_HOURS
from app.routes.utils import (
    get_authenticated_user,
    has_appointment_overlap,
    parse_iso_datetime,
    redirect_to_login,
)

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def send_request_notification_email(db: Session, appointment: Appointment) -> None:
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
    request_text = (appointment.request_message or "").strip() or "(kein Text)"

    message = EmailMessage()
    message["Subject"] = "Neue Fahrstunden-Anfrage"
    message["From"] = smtp_from
    message["To"] = ", ".join(recipients)
    message.set_content(
        "\n".join(
            [
                "Es wurde eine neue Fahrstunden-Anfrage gestellt.",
                "",
                f"Schüler: {student_name}",
                f"Fahrlehrer: {teacher_name}",
                f"Start: {appointment.start_at.strftime('%d.%m.%Y %H:%M')}",
                f"Ende: {appointment.end_at.strftime('%d.%m.%Y %H:%M')}",
                f"Dauer: {appointment.duration_min} Minuten",
                "",
                f"Anfrage: {request_text}",
            ]
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
        {"request": request, "user": user, "appointments": appointments},
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

    direct_booking_until = datetime.now() + timedelta(hours=STUDENT_DIRECT_BOOKING_START_LEAD_HOURS)
    request_booking_until = direct_booking_until + timedelta(hours=STUDENT_DIRECT_BOOKING_WINDOW_HOURS)
    if start_dt > request_booking_until:
        return RedirectResponse(url="/portal", status_code=302)

    requires_teacher_confirmation = start_dt > direct_booking_until

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
    db.commit()

    if requires_teacher_confirmation:
        db.refresh(appointment)
        send_request_notification_email(db, appointment)

    return RedirectResponse(url="/portal", status_code=302)


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
    appointment.is_read_by_student = True
    appointment.is_closed = True

    db.commit()
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
    return RedirectResponse(url="/appointments", status_code=302)
