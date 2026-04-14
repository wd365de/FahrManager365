import re
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Appointment
from app.planner_settings import get_planner_setting_value
from app.routes.utils import get_authenticated_user
from app.settings import SCHOOL_LOGO_URL, SCHOOL_NAME, SCHOOL_PRIMARY_COLOR

router = APIRouter()

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$")


def _safe_color(value: str, fallback: str = "#e11d48") -> str:
    value = (value or "").strip()
    return value if _HEX_RE.match(value) else fallback


@router.get("/theme.css")
def theme_css(db: Session = Depends(get_db)):
    color = _safe_color(get_planner_setting_value(db, SCHOOL_PRIMARY_COLOR), "#e11d48")
    school_name = (get_planner_setting_value(db, SCHOOL_NAME) or "Fahrschule").strip()
    # Derive a darker shade for hover/active states (~20% darker)
    css = f""":root {{
  --school-color: {color};
  --school-color-dark: color-mix(in srgb, {color} 80%, black);
  --school-name: "{school_name}";
}}
.app-sidebar-rail {{
  background: var(--school-color) !important;
}}
.rail-link.active,
.rail-link:hover {{
  color: var(--school-color) !important;
}}
.rail-logo {{
  background: #ffffff !important;
  color: var(--school-color) !important;
}}
"""
    return Response(content=css, media_type="text/css", headers={"Cache-Control": "no-store"})


@router.get("/api/school-settings")
def school_settings_api(db: Session = Depends(get_db)):
    logo_url = (get_planner_setting_value(db, SCHOOL_LOGO_URL) or "").strip()
    return {
        "name": (get_planner_setting_value(db, SCHOOL_NAME) or "Fahrschule").strip(),
        "color": _safe_color(get_planner_setting_value(db, SCHOOL_PRIMARY_COLOR), "#e11d48"),
        "logo_url": logo_url if logo_url else None,
    }


@router.get("/api/rail-badges")
def rail_badges_api(request: Request, db: Session = Depends(get_db)):
    user = get_authenticated_user(request, db)
    if not user:
        return {"pending": 0, "today": 0}

    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = today_start + timedelta(days=1)

    if user.role == "teacher" and user.teacher:
        pending = (
            db.query(Appointment)
            .filter(
                Appointment.teacher_id == user.teacher.id,
                Appointment.status == "booked",
                Appointment.requires_teacher_confirmation == True,
                Appointment.is_closed == False,
            )
            .count()
        )
        today = (
            db.query(Appointment)
            .filter(
                Appointment.teacher_id == user.teacher.id,
                Appointment.status == "booked",
                Appointment.start_at >= today_start,
                Appointment.start_at < today_end,
            )
            .count()
        )
    elif user.role == "student" and user.student:
        pending = (
            db.query(Appointment)
            .filter(
                Appointment.student_id == user.student.id,
                Appointment.status == "booked",
                Appointment.requires_teacher_confirmation == True,
                Appointment.is_closed == False,
            )
            .count()
        )
        today = (
            db.query(Appointment)
            .filter(
                Appointment.student_id == user.student.id,
                Appointment.status == "booked",
                Appointment.start_at >= today_start,
                Appointment.start_at < today_end,
            )
            .count()
        )
    elif user.role == "admin":
        pending = (
            db.query(Appointment)
            .filter(
                Appointment.status == "booked",
                Appointment.requires_teacher_confirmation == True,
                Appointment.is_closed == False,
            )
            .count()
        )
        today = (
            db.query(Appointment)
            .filter(
                Appointment.status == "booked",
                Appointment.start_at >= today_start,
                Appointment.start_at < today_end,
            )
            .count()
        )
    else:
        pending = 0
        today = 0

    return {"pending": pending, "today": today}
