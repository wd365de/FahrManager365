from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Appointment, AvailabilityWindow
from app.readiness import calculate_student_readiness
from app.planner_settings import get_planner_setting_bool, get_planner_setting_value
from app.push_notifications import has_push_config
from app.settings import ALLOWED_APPOINTMENT_DURATIONS, BOOKING_BUFFER_MINUTES, BOOKING_STEP_MINUTES
from app.settings import PLANNER_SETTING_AUTO_REMINDERS
from app.settings import PLANNER_SETTING_SHOW_LOCKED_SLOTS
from app.settings import SCHOOL_WHATSAPP_NUMBER
from app.settings import STUDENT_DIRECT_BOOKING_START_LEAD_HOURS, STUDENT_DIRECT_BOOKING_WINDOW_HOURS
from app.routes.utils import build_booking_options, get_authenticated_user, redirect_to_login

DAYPART_LABELS = {
    "morning": "Vormittag",
    "afternoon": "Nachmittag",
    "evening": "Abend",
}

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/portal")
def portal(request: Request, db: Session = Depends(get_db)):
    user = get_authenticated_user(request, db)
    if not user:
        return redirect_to_login()

    if user.role != "student" or not user.student:
        return RedirectResponse(url="/dashboard", status_code=302)

    appointments = (
        db.query(Appointment)
        .filter(Appointment.student_id == user.student.id, Appointment.is_closed == False)
        .order_by(Appointment.start_at.asc())
        .all()
    )
    readiness_appointments = (
        db.query(Appointment)
        .filter(Appointment.student_id == user.student.id)
        .order_by(Appointment.start_at.asc())
        .all()
    )
    readiness = calculate_student_readiness(user.student, readiness_appointments)

    assigned_teacher_id = user.student.teacher_id
    windows_query = (
        db.query(AvailabilityWindow)
        .join(AvailabilityWindow.teacher)
        .filter(AvailabilityWindow.end_at >= datetime.now())
    )
    if assigned_teacher_id:
        windows_query = windows_query.filter(AvailabilityWindow.teacher_id == assigned_teacher_id)
    else:
        windows_query = windows_query.filter(AvailabilityWindow.id == -1)

    windows = windows_query.order_by(AvailabilityWindow.start_at.asc()).all()
    show_locked_slots = get_planner_setting_bool(db, PLANNER_SETTING_SHOW_LOCKED_SLOTS)
    booking_options = build_booking_options(
        db,
        windows=windows,
        duration_options=ALLOWED_APPOINTMENT_DURATIONS,
        step_minutes=BOOKING_STEP_MINUTES,
        buffer_minutes=BOOKING_BUFFER_MINUTES,
        include_locked_slots=show_locked_slots,
        direct_booking_start_lead_hours=STUDENT_DIRECT_BOOKING_START_LEAD_HOURS,
        direct_booking_window_hours=STUDENT_DIRECT_BOOKING_WINDOW_HOURS,
    )

    today = date.today()
    default_week_start = today - timedelta(days=today.weekday())
    week_start_param = request.query_params.get("week_start")
    try:
        week_start = date.fromisoformat(week_start_param) if week_start_param else default_week_start
    except ValueError:
        week_start = default_week_start

    duration_param = request.query_params.get("duration_min")
    try:
        selected_duration = int(duration_param) if duration_param else 0
    except ValueError:
        selected_duration = 0

    selected_daypart = request.query_params.get("daypart", "")
    if selected_daypart not in DAYPART_LABELS:
        selected_daypart = ""

    available_durations = sorted({option["duration_min"] for option in booking_options})
    if selected_duration and selected_duration not in available_durations:
        selected_duration = 0

    filtered_options = booking_options
    if selected_duration:
        filtered_options = [
            option for option in filtered_options if option["duration_min"] == selected_duration
        ]

    if selected_daypart == "morning":
        filtered_options = [option for option in filtered_options if option["start_at"].hour < 12]
    elif selected_daypart == "afternoon":
        filtered_options = [
            option for option in filtered_options if 12 <= option["start_at"].hour < 17
        ]
    elif selected_daypart == "evening":
        filtered_options = [option for option in filtered_options if option["start_at"].hour >= 17]

    week_dates = [week_start + timedelta(days=offset) for offset in range(7)]
    week_end = week_start + timedelta(days=7)
    week_options = [
        option
        for option in filtered_options
        if week_start <= option["start_at"].date() < week_end
    ]

    options_by_day = {day: [] for day in week_dates}
    for option in week_options:
        day_key = option["start_at"].date()
        if day_key in options_by_day:
            options_by_day[day_key].append(option)

    for day in week_dates:
        options_by_day[day].sort(key=lambda item: (item["start_at"], item["duration_min"]))

    weekday_labels = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    week_days = [
        {
            "date": day,
            "label": f"{weekday_labels[offset]} {day.strftime('%d.%m')}",
        }
        for offset, day in enumerate(week_dates)
    ]

    prev_week_start = (week_start - timedelta(days=7)).isoformat()
    next_week_start = (week_start + timedelta(days=7)).isoformat()
    current_week_start = default_week_start.isoformat()

    assigned_teacher_name = user.student.teacher.user.name if user.student.teacher else None
    auto_reminders_enabled = get_planner_setting_bool(db, PLANNER_SETTING_AUTO_REMINDERS)
    push_mvp_available = auto_reminders_enabled and has_push_config()
    whatsapp_number = get_planner_setting_value(db, SCHOOL_WHATSAPP_NUMBER)
    student_whatsapp_phone = user.student.whatsapp_phone or ""
    student_whatsapp_opted_in = user.student.whatsapp_opted_in

    return templates.TemplateResponse(
        "portal.html",
        {
            "request": request,
            "user": user,
            "appointments": appointments,
            "readiness": readiness,
            "booking_options": filtered_options,
            "week_options": week_options,
            "week_days": week_days,
            "options_by_day": options_by_day,
            "week_start": week_start.isoformat(),
            "prev_week_start": prev_week_start,
            "next_week_start": next_week_start,
            "current_week_start": current_week_start,
            "available_durations": available_durations,
            "selected_duration": selected_duration,
            "selected_daypart": selected_daypart,
            "daypart_labels": DAYPART_LABELS,
            "assigned_teacher_name": assigned_teacher_name,
            "push_mvp_available": push_mvp_available,
            "auto_reminders_enabled": auto_reminders_enabled,
            "whatsapp_number": whatsapp_number,
            "student_whatsapp_phone": student_whatsapp_phone,
            "student_whatsapp_opted_in": student_whatsapp_opted_in,
        },
    )


@router.post("/portal/whatsapp")
def portal_whatsapp_update(
    request: Request,
    whatsapp_phone: str = Form(""),
    whatsapp_opted_in: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_authenticated_user(request, db)
    if not user or user.role != "student" or not user.student:
        return redirect_to_login()

    cleaned = "".join(c for c in whatsapp_phone if c.isdigit())
    user.student.whatsapp_phone = cleaned or None
    user.student.whatsapp_opted_in = whatsapp_opted_in == "on"
    db.commit()
    return RedirectResponse(url="/portal", status_code=302)
