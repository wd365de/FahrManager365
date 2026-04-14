from datetime import date, datetime, timedelta
import os
from pathlib import Path
import smtplib
from email.message import EmailMessage

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.exc import IntegrityError
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.routes.action_tokens import make_action_token, verify_action_token
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Appointment, AvailabilityWindow, Student, Teacher, User
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


@router.get("/teacher/settings")
def teacher_settings_form(request: Request, db: Session = Depends(get_db)):
    user = get_authenticated_user(request, db)
    if not user or user.role != "teacher" or not user.teacher:
        return redirect_to_login()
    saved = request.query_params.get("saved") == "1"
    return templates.TemplateResponse(
        "teacher_settings.html",
        {
            "request": request,
            "user": user,
            "teacher": user.teacher,
            "push_available": has_push_config(),
            "saved": saved,
        },
    )


@router.post("/teacher/settings")
def teacher_settings_save(
    request: Request,
    reminder_minutes: int = Form(30),
    whatsapp_phone: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_authenticated_user(request, db)
    if not user or user.role != "teacher" or not user.teacher:
        return redirect_to_login()
    user.teacher.reminder_minutes = max(5, min(240, reminder_minutes))
    cleaned = "".join(c for c in whatsapp_phone if c.isdigit())
    user.teacher.whatsapp_phone = cleaned or None
    db.commit()
    return RedirectResponse(url="/teacher/settings?saved=1", status_code=302)


@router.get("/teacher/planner")
def teacher_planner(request: Request, db: Session = Depends(get_db)):
    user = get_authenticated_user(request, db)
    if not user or user.role != "teacher" or not user.teacher:
        return redirect_to_login()

    teacher = user.teacher
    today = date.today()

    week_start_param = request.query_params.get("week_start")
    try:
        if week_start_param:
            week_start = date.fromisoformat(week_start_param)
        else:
            # Default to the week of the next upcoming slot, fallback to current week
            next_window = (
                db.query(AvailabilityWindow)
                .filter(
                    AvailabilityWindow.teacher_id == teacher.id,
                    AvailabilityWindow.start_at >= datetime.combine(today, datetime.min.time()),
                )
                .order_by(AvailabilityWindow.start_at.asc())
                .first()
            )
            if next_window:
                anchor = next_window.start_at.date()
                week_start = anchor - timedelta(days=anchor.weekday())
            else:
                week_start = today - timedelta(days=today.weekday())
    except ValueError:
        week_start = today - timedelta(days=today.weekday())

    week_end = week_start + timedelta(days=7)
    current_week_start = (today - timedelta(days=today.weekday())).isoformat()

    weekday_labels = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    week_dates = [week_start + timedelta(days=i) for i in range(7)]
    week_days = [
        {"date": d, "label": f"{weekday_labels[i]} {d.strftime('%d.%m')}"}
        for i, d in enumerate(week_dates)
    ]

    windows = (
        db.query(AvailabilityWindow)
        .filter(
            AvailabilityWindow.teacher_id == teacher.id,
            AvailabilityWindow.start_at >= datetime.combine(week_start, datetime.min.time()),
            AvailabilityWindow.start_at < datetime.combine(week_end, datetime.min.time()),
        )
        .order_by(AvailabilityWindow.start_at.asc())
        .all()
    )

    appointments = (
        db.query(Appointment)
        .options(joinedload(Appointment.student).joinedload(Student.user))
        .filter(
            Appointment.teacher_id == teacher.id,
            Appointment.status == "booked",
            Appointment.start_at >= datetime.combine(week_start, datetime.min.time()),
            Appointment.start_at < datetime.combine(week_end, datetime.min.time()),
        )
        .order_by(Appointment.start_at.asc())
        .all()
    )

    windows_by_day = {d: [] for d in week_dates}
    for w in windows:
        day_key = w.start_at.date()
        if day_key in windows_by_day:
            windows_by_day[day_key].append(w)

    appointments_by_window = {}
    for appt in appointments:
        for w in windows:
            if appt.start_at < w.end_at and appt.end_at > w.start_at:
                appointments_by_window[w.id] = appt
                break

    pending_confirmations = (
        db.query(Appointment)
        .options(joinedload(Appointment.student).joinedload(Student.user))
        .filter(
            Appointment.teacher_id == teacher.id,
            Appointment.status == "booked",
            Appointment.requires_teacher_confirmation == True,
            Appointment.is_closed == False,
        )
        .order_by(Appointment.start_at.asc())
        .all()
    )

    # All upcoming slots for the list view
    all_upcoming_windows = (
        db.query(AvailabilityWindow)
        .filter(
            AvailabilityWindow.teacher_id == teacher.id,
            AvailabilityWindow.end_at >= datetime.now(),
        )
        .order_by(AvailabilityWindow.start_at.asc())
        .all()
    )
    all_upcoming_appointments = (
        db.query(Appointment)
        .options(joinedload(Appointment.student).joinedload(Student.user))
        .filter(
            Appointment.teacher_id == teacher.id,
            Appointment.status == "booked",
            Appointment.end_at >= datetime.now(),
        )
        .all()
    )
    # Map window.id → booking via time overlap
    upcoming_booking_map = {}
    for w in all_upcoming_windows:
        for appt in all_upcoming_appointments:
            if appt.start_at < w.end_at and appt.end_at > w.start_at:
                upcoming_booking_map[w.id] = appt
                break

    feedback = request.query_params.get("feedback", "")
    feedback_messages = {
        "created": ("success", "Slot wurde angelegt."),
        "deleted": ("success", "Slot wurde gelöscht."),
        "overlap": ("warning", "Slot überschneidet sich mit einem vorhandenen Slot."),
        "invalid": ("danger", "Ungültige Zeiten. Start muss vor Ende liegen, gleicher Tag."),
        "past": ("warning", "Slot kann nicht in der Vergangenheit angelegt werden."),
        "has_booking": ("warning", "Slot hat eine Buchung und kann nicht gelöscht werden."),
    }
    feedback_level, feedback_message = feedback_messages.get(feedback, ("", ""))

    return templates.TemplateResponse(
        "teacher_planner.html",
        {
            "request": request,
            "user": user,
            "teacher": teacher,
            "week_days": week_days,
            "week_start": week_start.isoformat(),
            "prev_week_start": (week_start - timedelta(days=7)).isoformat(),
            "next_week_start": (week_start + timedelta(days=7)).isoformat(),
            "current_week_start": current_week_start,
            "today_iso": today.isoformat(),
            "windows_by_day": windows_by_day,
            "appointments_by_window": appointments_by_window,
            "pending_confirmations": pending_confirmations,
            "all_upcoming_windows": all_upcoming_windows,
            "upcoming_booking_map": upcoming_booking_map,
            "feedback_level": feedback_level,
            "feedback_message": feedback_message,
            "now": datetime.now(),
        },
    )


@router.post("/teacher/planner/slots/new")
def teacher_planner_slot_create(
    request: Request,
    start_at: str = Form(...),
    end_at: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_authenticated_user(request, db)
    if not user or user.role != "teacher" or not user.teacher:
        return redirect_to_login()

    teacher = user.teacher
    try:
        start_dt = datetime.fromisoformat(start_at)
        end_dt = datetime.fromisoformat(end_at)
    except ValueError:
        return RedirectResponse(url="/teacher/planner?feedback=invalid", status_code=302)

    if start_dt < datetime.now():
        return RedirectResponse(url="/teacher/planner?feedback=past", status_code=302)

    if start_dt >= end_dt or start_dt.date() != end_dt.date():
        return RedirectResponse(url="/teacher/planner?feedback=invalid", status_code=302)

    existing = (
        db.query(AvailabilityWindow)
        .filter(
            AvailabilityWindow.teacher_id == teacher.id,
            AvailabilityWindow.start_at < end_dt,
            AvailabilityWindow.end_at > start_dt,
        )
        .first()
    )
    if existing:
        return RedirectResponse(url="/teacher/planner?feedback=overlap", status_code=302)

    window = AvailabilityWindow(
        teacher_id=teacher.id,
        start_at=start_dt,
        end_at=end_dt,
        bookable_from=start_dt - timedelta(hours=999),
        source="manual",
    )
    db.add(window)
    db.commit()
    return RedirectResponse(
        url=f"/teacher/planner?week_start={start_dt.date() - timedelta(days=start_dt.date().weekday())}&feedback=created",
        status_code=302,
    )


@router.post("/teacher/planner/slots/{slot_id}/delete")
def teacher_planner_slot_delete(
    slot_id: int,
    request: Request,
    week_start: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_authenticated_user(request, db)
    if not user or user.role != "teacher" or not user.teacher:
        return redirect_to_login()

    window = (
        db.query(AvailabilityWindow)
        .filter(
            AvailabilityWindow.id == slot_id,
            AvailabilityWindow.teacher_id == user.teacher.id,
        )
        .first()
    )
    redirect_week = f"&week_start={week_start}" if week_start else ""
    if not window:
        return RedirectResponse(url=f"/teacher/planner?feedback=invalid{redirect_week}", status_code=302)

    has_booking = (
        db.query(Appointment)
        .filter(
            Appointment.teacher_id == user.teacher.id,
            Appointment.status == "booked",
            Appointment.start_at < window.end_at,
            Appointment.end_at > window.start_at,
        )
        .first()
    )
    if has_booking:
        return RedirectResponse(url=f"/teacher/planner?feedback=has_booking{redirect_week}", status_code=302)

    db.delete(window)
    db.commit()
    return RedirectResponse(url=f"/teacher/planner?feedback=deleted{redirect_week}", status_code=302)


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

    direct_until = datetime.now() + timedelta(hours=STUDENT_DIRECT_BOOKING_START_LEAD_HOURS)
    requires_teacher_confirmation = start_dt > direct_until

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
        confirm_url = f"{base_url}/appointments/{appointment.id}/wa-confirm/{make_action_token(appointment.id, 'confirm')}"
        reject_url = f"{base_url}/appointments/{appointment.id}/wa-reject/{make_action_token(appointment.id, 'reject')}"
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
    if not verify_action_token(token, appointment_id, "confirm"):
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
    if not verify_action_token(token, appointment_id, "reject"):
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
