"""
Background scheduler for appointment reminders.

Runs every 5 minutes, finds upcoming appointments where a reminder is due
(within the user's configured reminder_minutes window), sends push and/or
WhatsApp notifications, and marks reminder_sent = True to avoid duplicates.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)


def _send_reminders() -> None:
    """Core reminder logic – runs inside the scheduler thread."""
    try:
        from app.database import SessionLocal
        from app.models import Appointment, Student, Teacher
        from app.push_notifications import notify_user
        from app.whatsapp import has_whatsapp_config, send_whatsapp
    except Exception as exc:
        logger.error("reminder: import error: %s", exc)
        return

    db = SessionLocal()
    try:
        now = datetime.now()
        # Look ahead up to 120 min to catch all possible reminder windows
        look_ahead = now + timedelta(minutes=120)

        candidates = (
            db.query(Appointment)
            .filter(
                Appointment.status == "booked",
                Appointment.reminder_sent == False,
                Appointment.start_at >= now,
                Appointment.start_at <= look_ahead,
            )
            .all()
        )

        for appt in candidates:
            student: Student | None = appt.student
            teacher: Teacher | None = appt.teacher

            student_reminder = (student.reminder_minutes if student else 30) or 30
            teacher_reminder = (teacher.reminder_minutes if teacher else 30) or 30
            combined_minutes = max(student_reminder, teacher_reminder)

            # Reminder window: [start_at - reminder_minutes, start_at - reminder_minutes + 5min)
            # Use the larger window so we don't miss anybody; each user uses their own value
            due_at_student = appt.start_at - timedelta(minutes=student_reminder)
            due_at_teacher = appt.start_at - timedelta(minutes=teacher_reminder)

            student_due = due_at_student <= now
            teacher_due = due_at_teacher <= now

            if not (student_due or teacher_due):
                continue

            start_fmt = appt.start_at.strftime("%d.%m.%Y %H:%M")
            reminder_sent = False

            # --- Notify student ---
            if student_due and student:
                student_user_id = student.user_id
                # Push
                try:
                    notify_user(db, student_user_id, "Termin-Erinnerung", f"Dein Termin beginnt in {student_reminder} Min. ({start_fmt})")
                    reminder_sent = True
                except Exception as exc:
                    logger.warning("reminder: push to student %s failed: %s", student_user_id, exc)

                # WhatsApp
                if student.whatsapp_opted_in and student.whatsapp_phone and has_whatsapp_config():
                    try:
                        msg = f"Erinnerung: Dein Fahrstunden-Termin beginnt in {student_reminder} Min. ({start_fmt}). Viel Erfolg!"
                        send_whatsapp(student.whatsapp_phone, msg)
                        reminder_sent = True
                    except Exception as exc:
                        logger.warning("reminder: WA to student failed: %s", exc)

            # --- Notify teacher ---
            if teacher_due and teacher:
                teacher_user_id = teacher.user_id
                student_name = student.user.name if student and student.user else "Schüler"
                # Push
                try:
                    notify_user(db, teacher_user_id, "Termin-Erinnerung", f"Termin mit {student_name} beginnt in {teacher_reminder} Min. ({start_fmt})")
                    reminder_sent = True
                except Exception as exc:
                    logger.warning("reminder: push to teacher %s failed: %s", teacher_user_id, exc)

                # WhatsApp
                if teacher.whatsapp_phone and has_whatsapp_config():
                    try:
                        msg = f"Erinnerung: Termin mit {student_name} beginnt in {teacher_reminder} Min. ({start_fmt})."
                        send_whatsapp(teacher.whatsapp_phone, msg)
                        reminder_sent = True
                    except Exception as exc:
                        logger.warning("reminder: WA to teacher failed: %s", exc)

            if reminder_sent:
                appt.reminder_sent = True
                db.commit()
                logger.info("reminder: sent for appointment %s at %s", appt.id, start_fmt)

    except Exception as exc:
        logger.error("reminder: unexpected error: %s", exc)
    finally:
        db.close()


_scheduler: BackgroundScheduler | None = None


def start_reminder_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(_send_reminders, "interval", minutes=5, id="appointment_reminders")
    _scheduler.start()
    logger.info("reminder: scheduler started (every 5 min)")


def stop_reminder_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
