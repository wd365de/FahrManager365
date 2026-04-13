import logging
import os
import requests

logger = logging.getLogger(__name__)


def _get_config() -> tuple[str, str, str] | None:
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_number = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()
    if not account_sid or not auth_token or not from_number:
        return None
    return account_sid, auth_token, from_number


def has_whatsapp_config() -> bool:
    return _get_config() is not None


def _normalize_phone(number: str) -> str:
    """Normalize a phone number to E.164 format for Germany (+49...)."""
    number = number.strip()
    # Remove all non-digit characters except leading +
    digits = "".join(c for c in number if c.isdigit())
    if number.startswith("+"):
        return "+" + digits
    # German leading 0: 0151... → +4951...
    if digits.startswith("0"):
        return "+49" + digits[1:]
    # Already has country code 49
    if digits.startswith("49"):
        return "+" + digits
    # Fallback: assume Germany
    return "+49" + digits


def send_whatsapp(to_number: str, message: str) -> bool:
    """Send a WhatsApp message via Twilio. to_number must be in E.164 format, e.g. +4915112345678."""
    config = _get_config()
    if not config:
        return False

    to_number = _normalize_phone(to_number)

    account_sid, auth_token, from_number = config
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

    print(f"WA send: to={to_number} from={from_number}", flush=True)
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
        print(f"WA response: status={resp.status_code} body={resp.text[:200]}", flush=True)
        return resp.status_code in (200, 201)
    except Exception as e:
        print(f"WA exception: {e}", flush=True)
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


def notify_teacher_new_booking(
    to_number: str,
    student_name: str,
    start_at: str,
    confirm_url: str,
    reject_url: str,
) -> bool:
    message = (
        f"Neue Terminanfrage\n\n"
        f"Schüler: {student_name}\n"
        f"Termin: {start_at}\n\n"
        f"Bestätigen:\n{confirm_url}\n\n"
        f"Ablehnen:\n{reject_url}"
    )
    return send_whatsapp(to_number, message)
