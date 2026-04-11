from datetime import datetime, timedelta

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import get_session_user
from app.models import Appointment, AvailabilityWindow, User


def parse_datetime_local(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M")


def parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def redirect_to_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)


def get_authenticated_user(request: Request, db: Session) -> User | None:
    return get_session_user(request, db)


def require_role(user: User, role: str) -> bool:
    return user.role == role


def has_appointment_overlap(
    db: Session,
    teacher_id: int,
    start_at: datetime,
    end_at: datetime,
    buffer_minutes: int,
) -> bool:
    appointments = (
        db.query(Appointment)
        .filter(Appointment.teacher_id == teacher_id, Appointment.status == "booked")
        .all()
    )
    for appointment in appointments:
        blocked_start = appointment.start_at - timedelta(minutes=buffer_minutes)
        blocked_end = appointment.end_at + timedelta(minutes=buffer_minutes)
        if start_at < blocked_end and end_at > blocked_start:
            return True
    return False


def build_booking_options(
    db: Session,
    windows: list[AvailabilityWindow],
    duration_options: list[int],
    step_minutes: int,
    buffer_minutes: int,
    include_locked_slots: bool = False,
    direct_booking_start_lead_hours: int = 48,
    direct_booking_window_hours: int = 48,
) -> list[dict]:
    now = datetime.now()
    direct_booking_until = now + timedelta(hours=direct_booking_start_lead_hours)
    request_booking_until = direct_booking_until + timedelta(hours=direct_booking_window_hours)
    options: list[dict] = []

    for window in windows:
        if window.start_at.date() != window.end_at.date():
            continue

        for duration in duration_options:
            cursor = window.start_at
            while cursor + timedelta(minutes=duration) <= window.end_at:
                end_at = cursor + timedelta(minutes=duration)
                has_overlap = has_appointment_overlap(
                    db,
                    teacher_id=window.teacher_id,
                    start_at=cursor,
                    end_at=end_at,
                    buffer_minutes=buffer_minutes,
                )
                can_book_now = now >= window.bookable_from
                if (
                    cursor >= now
                    and not has_overlap
                    and cursor <= request_booking_until
                    and (can_book_now or include_locked_slots)
                ):
                    booking_mode = "book" if cursor <= direct_booking_until else "request"
                    requires_teacher_confirmation = booking_mode == "request"
                    can_submit_now = booking_mode in {"book", "request"}
                    options.append(
                        {
                            "window_id": window.id,
                            "teacher_name": window.teacher.user.name,
                            "start_at": cursor,
                            "end_at": end_at,
                            "duration_min": duration,
                            "start_iso": cursor.isoformat(),
                            "bookable_from": window.bookable_from,
                            "can_book_now": can_book_now,
                            "can_submit_now": can_submit_now,
                            "booking_mode": booking_mode,
                            "requires_teacher_confirmation": requires_teacher_confirmation,
                        }
                    )
                cursor += timedelta(minutes=step_minutes)

    options.sort(key=lambda item: (item["start_at"], item["teacher_name"], item["duration_min"]))
    return options
