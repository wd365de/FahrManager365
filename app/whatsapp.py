import os
import requests


def _get_config() -> tuple[str, str, str] | None:
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_number = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()
    if not account_sid or not auth_token or not from_number:
        return None
    return account_sid, auth_token, from_number


def has_whatsapp_config() -> bool:
    return _get_config() is not None


def send_whatsapp(to_number: str, message: str) -> bool:
    """Send a WhatsApp message via Twilio. to_number must be in E.164 format, e.g. +4915112345678."""
    config = _get_config()
    if not config:
        return False

    to_number = to_number.strip()
    if not to_number.startswith("+"):
        to_number = "+" + to_number

    account_sid, auth_token, from_number = config
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

    try:
        resp = requests.post(
            url,
            data={
                "To": f"whatsapp:{to_number}",
                "From": from_number,
                "Body": message,
            },
            auth=(account_sid, auth_token),
            timeout=10,
        )
        return resp.status_code in (200, 201)
    except Exception:
        return False


def notify_appointment_confirmed(student_name: str, to_number: str, start_at: str) -> bool:
    message = (
        f"Hallo {student_name},\n\n"
        f"dein Fahrstunden-Termin am {start_at} wurde bestätigt ✓\n\n"
        f"Bis dann!\nDeine Fahrschule"
    )
    return send_whatsapp(to_number, message)


def notify_appointment_cancelled(student_name: str, to_number: str, start_at: str) -> bool:
    message = (
        f"Hallo {student_name},\n\n"
        f"dein Fahrstunden-Termin am {start_at} wurde leider storniert.\n"
        f"Bitte buche einen neuen Termin im Portal.\n\n"
        f"Deine Fahrschule"
    )
    return send_whatsapp(to_number, message)


def notify_appointment_booked(student_name: str, to_number: str, start_at: str) -> bool:
    message = (
        f"Hallo {student_name},\n\n"
        f"deine Buchung für den {start_at} ist eingegangen ✓\n"
        f"Du erhältst eine Bestätigung sobald dein Fahrlehrer zugesagt hat.\n\n"
        f"Deine Fahrschule"
    )
    return send_whatsapp(to_number, message)
