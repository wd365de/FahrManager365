from __future__ import annotations

from datetime import datetime, timedelta

from app.models import Appointment, Student

PRACTICAL_TARGET_LESSONS = 20
PROFILE_FIELDS = [
    "first_name",
    "last_name",
    "birth_date",
    "birth_place",
    "city",
    "street",
    "house_number",
    "mobile_phone",
    "training_class",
    "course_name",
    "price_list",
    "payment_method",
]


def _clamp(value: float, minimum: int = 0, maximum: int = 100) -> int:
    return max(minimum, min(maximum, int(round(value))))


def calculate_student_readiness(student: Student, appointments: list[Appointment]) -> dict[str, object]:
    now = datetime.now()

    past_appointments = [
        appointment
        for appointment in appointments
        if appointment.start_at <= now and appointment.status in {"done", "booked", "cancelled"}
    ]
    done_count = sum(1 for appointment in past_appointments if appointment.status == "done")
    cancelled_count = sum(1 for appointment in past_appointments if appointment.status == "cancelled")

    practical_score = _clamp((done_count / PRACTICAL_TARGET_LESSONS) * 100)
    commitment_score = _clamp(100 - ((cancelled_count / len(past_appointments)) * 100)) if past_appointments else 70

    theory_score = 100 if student.theory_status == "bestanden" else 40

    filled_profile_fields = sum(1 for field in PROFILE_FIELDS if str(getattr(student, field, "") or "").strip())
    profile_score = _clamp((filled_profile_fields / len(PROFILE_FIELDS)) * 100)

    score = _clamp(
        (practical_score * 0.45)
        + (commitment_score * 0.20)
        + (theory_score * 0.20)
        + (profile_score * 0.15)
    )

    if score >= 75:
        readiness_level = "green"
    elif score >= 50:
        readiness_level = "yellow"
    else:
        readiness_level = "red"

    if score < 50:
        next_action = "Mehr Fahrpraxis einplanen und regelmaessig Termine wahrnehmen."
    elif student.theory_status != "bestanden":
        next_action = "Theorievorbereitung priorisieren und Pruefungstermin planen."
    elif student.practical_status != "bestanden":
        next_action = "Praxisstunden fortsetzen, dann Pruefungsanmeldung vorbereiten."
    else:
        next_action = "Pruefungsreif: Anmeldung zur praktischen Pruefung sinnvoll."

    trend_labels: list[str] = []
    trend_values: list[int] = []
    for offset in range(5, -1, -1):
        week_start = (now - timedelta(days=now.weekday()) - timedelta(weeks=offset)).date()
        week_end = week_start + timedelta(days=7)
        lessons_in_week = sum(
            1
            for appointment in appointments
            if appointment.status == "done" and week_start <= appointment.start_at.date() < week_end
        )
        trend_labels.append(week_start.strftime("%d.%m"))
        trend_values.append(lessons_in_week)

    components = [
        {"label": "Praxisfortschritt", "value": practical_score},
        {"label": "Termintreue", "value": commitment_score},
        {"label": "Theorie", "value": theory_score},
        {"label": "Profil", "value": profile_score},
    ]

    return {
        "score": score,
        "level": readiness_level,
        "next_action": next_action,
        "components": components,
        "trend_labels": trend_labels,
        "trend_values": trend_values,
    }
