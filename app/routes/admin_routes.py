import calendar
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.auth import hash_password
from app.database import get_db
from app.models import Appointment, AvailabilityWindow, ExamInspector, ExamRegistration, Student, Teacher, User
from app.readiness import calculate_student_readiness
from app.planner_settings import (
    get_planner_setting_bool,
    get_planner_setting_value,
    set_planner_setting_value,
)
from app.push_notifications import has_push_config
from app.settings import DEFAULT_BOOKABLE_HOURS_BEFORE, WEEK_SLOT_DURATION_MINUTES
from app.settings import (
    MASTER_DATA_APPOINTMENT_TYPES,
    MASTER_DATA_CLASSES,
    MASTER_DATA_DEFAULT_APPOINTMENT_TYPE,
    MASTER_DATA_DEFAULT_CLASS,
    MASTER_DATA_DEFAULT_PRODUCT,
    MASTER_DATA_DEFAULT_VEHICLE,
    MASTER_DATA_COURSES,
    MASTER_DATA_ISSUE_TYPES,
    MASTER_DATA_PAYMENT_METHODS,
    MASTER_DATA_PRICE_LISTS,
    MASTER_DATA_PRODUCT_ASSIGNMENTS,
    MASTER_DATA_PRODUCTS,
    MASTER_DATA_TRAINING_CATEGORIES,
    MASTER_DATA_VEHICLES,
    PLANNER_SETTING_AUTO_REMINDERS,
    PLANNER_SETTING_DEFINITIONS,
    PLANNER_SETTING_SHOW_LOCKED_SLOTS,
    SCHOOL_WHATSAPP_NUMBER,
)
from app.routes.utils import (
    get_authenticated_user,
    parse_datetime_local,
    redirect_to_login,
    require_role,
)

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

def require_admin(request: Request, db: Session):
    user = get_authenticated_user(request, db)
    if not user:
        return None, redirect_to_login()
    if not require_role(user, "admin"):
        target = "/appointments" if user.role == "teacher" else "/portal"
        return None, RedirectResponse(url=target, status_code=302)
    return user, None


def has_booked_appointments_in_window(db: Session, window: AvailabilityWindow) -> bool:
    return (
        db.query(Appointment)
        .filter(
            Appointment.teacher_id == window.teacher_id,
            Appointment.status == "booked",
            Appointment.start_at < window.end_at,
            Appointment.end_at > window.start_at,
        )
        .first()
        is not None
    )


def has_booked_overlap_for_teacher(
    db: Session,
    teacher_id: int,
    start_at: datetime,
    end_at: datetime,
    exclude_appointment_id: int | None = None,
) -> bool:
    query = db.query(Appointment).filter(
        Appointment.teacher_id == teacher_id,
        Appointment.status == "booked",
        Appointment.start_at < end_at,
        Appointment.end_at > start_at,
    )
    if exclude_appointment_id is not None:
        query = query.filter(Appointment.id != exclude_appointment_id)
    return query.first() is not None


def normalize_duration_minutes(raw_duration: object, fallback_minutes: int) -> int:
    try:
        parsed = int(raw_duration) if raw_duration is not None else int(fallback_minutes)
    except (TypeError, ValueError):
        parsed = int(fallback_minutes)
    return max(1, parsed)


def build_slots_redirect_url(
    week_start: str,
    view: str,
    day: str,
    selected_window_id: str,
    tab: str = "bearbeiten",
) -> str:
    if view in {"day", "week", "month"}:
        redirect_url = f"/slots?view={view}"
        if day:
            redirect_url += f"&day={day}"
        if selected_window_id:
            redirect_url += f"&selected_window_id={selected_window_id}"
        if tab:
            redirect_url += f"&tab={tab}"
        return redirect_url
    if week_start:
        return f"/slots?week_start={week_start}"
    return "/slots"


def parse_master_data_entries(raw_value: str) -> list[str]:
    return [line.strip() for line in raw_value.splitlines() if line.strip()]


def parse_product_names(raw_value: str) -> list[str]:
    product_names: list[str] = []
    for line in raw_value.splitlines():
        entry = line.strip()
        if not entry:
            continue
        product_name = entry.split("|", 1)[0].strip()
        if product_name:
            product_names.append(product_name)
    return product_names


def parse_form_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

EXAM_REGISTRATION_STATUSES = {
    "vorgeschlagen": "Vorgeschlagen",
    "angemeldet": "Angemeldet",
    "terminiert": "Terminiert",
    "bestanden": "Bestanden",
    "nicht_bestanden": "Nicht bestanden",
}

SLOTS_FEEDBACK_MESSAGES: dict[str, tuple[str, str]] = {
    "appointment_created": ("success", "Termin erfolgreich im Planer eingefügt."),
    "appointment_create_invalid": ("danger", "Termin konnte nicht eingefügt werden. Bitte Angaben prüfen."),
    "appointment_create_student_mismatch": ("danger", "Der gewählte Fahrschüler passt nicht zum Fahrlehrer des Slots."),
    "appointment_create_past": ("danger", "Vergangene Slots können nicht gebucht werden."),
    "appointment_create_overlap": ("danger", "Der Slot ist bereits belegt oder überschneidet sich mit einem Termin."),
    "appointment_create_duration": ("danger", "Ungültige Slot-Dauer. Bitte Slot prüfen."),
}


def parse_exam_type_tokens(raw_exam_types: str) -> set[str]:
    return {token.strip().lower() for token in str(raw_exam_types or "").split(",") if token.strip()}


def format_exam_type_tokens(exam_type_tokens: set[str]) -> str:
    if "theory" in exam_type_tokens and "practical" in exam_type_tokens:
        return "theory,practical"
    if "theory" in exam_type_tokens:
        return "theory"
    return "practical"


def get_exam_view_context(db: Session, exam_type: str) -> dict[str, object]:
    students = db.query(Student).join(User).order_by(User.name.asc()).all()
    registrations = (
        db.query(ExamRegistration)
        .options(joinedload(ExamRegistration.inspector))
        .filter(ExamRegistration.exam_type == exam_type)
        .order_by(ExamRegistration.created_at.desc(), ExamRegistration.id.desc())
        .all()
    )

    latest_registration_by_student: dict[int, ExamRegistration] = {}
    for registration in registrations:
        if registration.student_id not in latest_registration_by_student:
            latest_registration_by_student[registration.student_id] = registration

    rows = []
    for student in students:
        registration = latest_registration_by_student.get(student.id)
        if exam_type == "theory":
            status_key = student.theory_status or "offen"
            is_suggested = status_key != "bestanden"
        else:
            status_key = student.practical_status or "offen"
            is_suggested = (student.theory_status == "bestanden") and status_key != "bestanden"
        rows.append(
            {
                "student_id": student.id,
                "student_name": student.user.name if student.user else "-",
                "email": student.user.email if student.user else "-",
                "training_class": student.training_class or "-",
                "status": status_key,
                "exam_organization": student.exam_organization or "-",
                "exam_location": student.branch_exam_location or "-",
                "is_suggested": is_suggested,
                "registration_status": registration.status if registration else "",
                "registration_status_label": EXAM_REGISTRATION_STATUSES.get(registration.status, registration.status) if registration else "",
                "registration_planned_date": registration.planned_date if registration else "",
                "registration_inspector": registration.inspector.name if registration and registration.inspector else "",
            }
        )

    inspectors_raw = db.query(ExamInspector).filter(ExamInspector.is_active == True).order_by(ExamInspector.name.asc()).all()
    inspector_options = []
    for inspector in inspectors_raw:
        tokens = parse_exam_type_tokens(inspector.exam_types)
        if exam_type not in tokens:
            continue
        inspector_options.append(
            {
                "id": inspector.id,
                "name": inspector.name,
                "organization": inspector.organization,
            }
        )

    return {
        "rows": rows,
        "inspector_options": inspector_options,
        "registration_status_labels": EXAM_REGISTRATION_STATUSES,
    }


REQUIRED_STUDENT_FIELDS = {
    "salutation": "Anrede",
    "first_name": "Vorname",
    "last_name": "Name",
    "email": "E-Mail",
    "birth_date": "Geburtsdatum",
    "birth_place": "Geburtsort",
    "citizenship_country": "Staatsangehörigkeit",
    "postal_code": "PLZ",
    "city": "Ort",
    "street": "Straße",
    "house_number": "Nr.",
    "mobile_phone": "Mobil",
    "branch_exam_location": "Filiale Prüfort",
    "exam_organization": "Prüforganisation",
    "training_class": "Klasse",
    "issue_type": "Erteilungsart",
    "enrollment_date": "Anmeldedatum",
    "course_name": "Kurs",
    "price_list": "Preisliste",
    "payment_method": "Zahlungsart",
    "cost_bearer": "Kostenträger",
}

ONBOARDING_REQUIRED_FIELDS = {
    **REQUIRED_STUDENT_FIELDS,
    "teacher_id": "Fester Fahrlehrer",
    "password": "Passwort",
}


def get_missing_student_required_fields(form_values: dict[str, str]) -> list[str]:
    missing_fields: list[str] = []
    for key in REQUIRED_STUDENT_FIELDS:
        if not str(form_values.get(key, "")).strip():
            missing_fields.append(key)
    return missing_fields


def get_missing_student_required_labels(missing_fields: list[str]) -> list[str]:
    return [REQUIRED_STUDENT_FIELDS[key] for key in missing_fields if key in REQUIRED_STUDENT_FIELDS]


def get_missing_required_labels(
    missing_fields: list[str],
    required_fields: dict[str, str],
) -> list[str]:
    return [required_fields[key] for key in missing_fields if key in required_fields]


def get_address_validation_errors(form_values: dict[str, str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    postal_code = str(form_values.get("postal_code", "")).strip()
    city = str(form_values.get("city", "")).strip()
    street = str(form_values.get("street", "")).strip()

    if postal_code and not re.fullmatch(r"\d{5}", postal_code):
        errors["postal_code"] = "PLZ muss aus genau 5 Ziffern bestehen."
    if city and not re.fullmatch(r"[A-Za-zÄÖÜäöüß\-\s]{2,}", city):
        errors["city"] = "Ort darf nur Buchstaben enthalten und muss mindestens 2 Zeichen haben."
    if street and (len(street) < 3 or not re.search(r"[A-Za-zÄÖÜäöüß]", street)):
        errors["street"] = "Straße muss mindestens 3 Zeichen lang sein und Buchstaben enthalten."

    return errors


def get_onboarding_template_context(db: Session) -> dict[str, object]:
    teachers = db.query(Teacher).join(User).order_by(User.name.asc()).all()
    master_data_context = get_master_data_context(db)
    return {
        "teachers": teachers,
        "master_data_options": master_data_context["master_data_options"],
        "master_data_defaults": master_data_context["master_data_defaults"],
    }


def parse_product_assignments(raw_value: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for product_name in parse_product_names(raw_value):
        rows.append({"product_name": product_name, "assignment": product_name})
    return rows


def parse_training_categories(
    raw_value: str,
) -> tuple[list[dict[str, int]], list[dict[str, str]]]:
    valid_rows: list[dict[str, int]] = []
    invalid_rows: list[dict[str, str]] = []
    for line_number, line in enumerate(raw_value.splitlines(), start=1):
        entry = line.strip()
        if not entry:
            continue
        if "|" not in entry:
            invalid_rows.append(
                {
                    "line": str(line_number),
                    "raw": entry,
                    "reason": "Trennzeichen '|' fehlt",
                }
            )
            continue
        category_name, target_value = entry.split("|", 1)
        category_name = category_name.strip()
        target_value = target_value.strip()

        if not category_name:
            invalid_rows.append(
                {
                    "line": str(line_number),
                    "raw": entry,
                    "reason": "Kategorie-Name fehlt",
                }
            )
            continue
        if not target_value.isdigit():
            invalid_rows.append(
                {
                    "line": str(line_number),
                    "raw": entry,
                    "reason": "Zielwert muss numerisch sein",
                }
            )
            continue

        valid_rows.append({"name": category_name, "target": int(target_value)})

    return valid_rows, invalid_rows


PRODUCT_COLOR_OVERRIDES = {
    "Überlandfahrt": "#f97316",
    "Autobahnfahrt": "#fb923c",
    "Nachtfahrt": "#b45309",
    "Testfahrt B197": "#f87171",
    "Simulator Stunde": "#8b5cf6",
    "Fehlstunde": "#9ca3af",
    "Übungsfahrt": "#0ea5e9",
    "Beleuchtungsfahrt": "#c2410c",
}
PRODUCT_COLOR_PALETTE = [
    "#0ea5e9",
    "#f97316",
    "#8b5cf6",
    "#22c55e",
    "#f59e0b",
    "#ef4444",
    "#14b8a6",
    "#6366f1",
]


def get_product_color_map(products: list[str]) -> dict[str, str]:
    color_map: dict[str, str] = {}
    fallback_index = 0
    for product in products:
        if product in PRODUCT_COLOR_OVERRIDES:
            color_map[product] = PRODUCT_COLOR_OVERRIDES[product]
            continue
        color_map[product] = PRODUCT_COLOR_PALETTE[fallback_index % len(PRODUCT_COLOR_PALETTE)]
        fallback_index += 1
    return color_map


def get_master_data_context(db: Session) -> dict[str, object]:
    appointment_types_raw = get_planner_setting_value(db, MASTER_DATA_APPOINTMENT_TYPES)
    classes_raw = get_planner_setting_value(db, MASTER_DATA_CLASSES)
    products_raw = get_planner_setting_value(db, MASTER_DATA_PRODUCTS)
    product_assignments_raw_db = get_planner_setting_value(db, MASTER_DATA_PRODUCT_ASSIGNMENTS)
    product_assignments_raw = "\n".join(parse_product_names(product_assignments_raw_db))
    vehicles_raw = get_planner_setting_value(db, MASTER_DATA_VEHICLES)
    payment_methods_raw = get_planner_setting_value(db, MASTER_DATA_PAYMENT_METHODS)
    courses_raw = get_planner_setting_value(db, MASTER_DATA_COURSES)
    issue_types_raw = get_planner_setting_value(db, MASTER_DATA_ISSUE_TYPES)
    price_lists_raw = get_planner_setting_value(db, MASTER_DATA_PRICE_LISTS)
    training_categories_raw = get_planner_setting_value(db, MASTER_DATA_TRAINING_CATEGORIES)

    appointment_types = parse_master_data_entries(appointment_types_raw)
    classes = parse_master_data_entries(classes_raw)
    products = parse_master_data_entries(products_raw)
    product_assignments = parse_product_assignments(product_assignments_raw)
    vehicles = parse_master_data_entries(vehicles_raw)
    payment_methods = parse_master_data_entries(payment_methods_raw)
    courses = parse_master_data_entries(courses_raw)
    issue_types = parse_master_data_entries(issue_types_raw)
    price_lists = parse_master_data_entries(price_lists_raw)
    training_categories, training_categories_invalid = parse_training_categories(training_categories_raw)

    default_appointment_type = get_planner_setting_value(db, MASTER_DATA_DEFAULT_APPOINTMENT_TYPE)
    default_class = get_planner_setting_value(db, MASTER_DATA_DEFAULT_CLASS)
    default_product = get_planner_setting_value(db, MASTER_DATA_DEFAULT_PRODUCT)
    default_vehicle = get_planner_setting_value(db, MASTER_DATA_DEFAULT_VEHICLE)

    if default_appointment_type not in appointment_types and appointment_types:
        default_appointment_type = appointment_types[0]
    if default_class not in classes and classes:
        default_class = classes[0]
    if default_product not in products and products:
        default_product = products[0]
    if default_vehicle not in vehicles and vehicles:
        default_vehicle = vehicles[0]

    return {
        "master_data_raw": {
            "appointment_types": appointment_types_raw,
            "classes": classes_raw,
            "products": products_raw,
            "product_assignments": product_assignments_raw,
            "vehicles": vehicles_raw,
            "payment_methods": payment_methods_raw,
            "courses": courses_raw,
            "issue_types": issue_types_raw,
            "price_lists": price_lists_raw,
            "training_categories": training_categories_raw,
        },
        "master_data_validation": {
            "training_categories_invalid": training_categories_invalid,
        },
        "master_data_options": {
            "appointment_types": appointment_types,
            "classes": classes,
            "products": products,
            "product_assignments": product_assignments,
            "vehicles": vehicles,
            "payment_methods": payment_methods,
            "courses": courses,
            "issue_types": issue_types,
            "price_lists": price_lists,
            "training_categories": training_categories,
        },
        "master_data_defaults": {
            "appointment_type": default_appointment_type,
            "class": default_class,
            "product": default_product,
            "vehicle": default_vehicle,
        },
    }


@router.get("/master-data")
def master_data_page(request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    context = get_master_data_context(db)
    return templates.TemplateResponse(
        "master_data.html",
        {
            "request": request,
            "user": user,
            **context,
        },
    )


@router.post("/master-data")
def master_data_update(
    request: Request,
    appointment_types: str = Form(""),
    classes: str = Form(""),
    products: str = Form(""),
    product_assignments: str = Form(""),
    vehicles: str = Form(""),
    payment_methods: str = Form(""),
    courses: str = Form(""),
    issue_types: str = Form(""),
    price_lists: str = Form(""),
    training_categories: str = Form(""),
    default_appointment_type: str = Form(""),
    default_class: str = Form(""),
    default_product: str = Form(""),
    default_vehicle: str = Form(""),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    set_planner_setting_value(db, MASTER_DATA_APPOINTMENT_TYPES, appointment_types.strip())
    set_planner_setting_value(db, MASTER_DATA_CLASSES, classes.strip())
    cleaned_products = "\n".join(parse_product_names(products))
    cleaned_product_assignments = "\n".join(parse_product_names(product_assignments))
    set_planner_setting_value(db, MASTER_DATA_PRODUCTS, cleaned_products)
    set_planner_setting_value(db, MASTER_DATA_PRODUCT_ASSIGNMENTS, cleaned_product_assignments)
    set_planner_setting_value(db, MASTER_DATA_VEHICLES, vehicles.strip())
    set_planner_setting_value(db, MASTER_DATA_PAYMENT_METHODS, payment_methods.strip())
    set_planner_setting_value(db, MASTER_DATA_COURSES, courses.strip())
    set_planner_setting_value(db, MASTER_DATA_ISSUE_TYPES, issue_types.strip())
    set_planner_setting_value(db, MASTER_DATA_PRICE_LISTS, price_lists.strip())
    set_planner_setting_value(db, MASTER_DATA_TRAINING_CATEGORIES, training_categories.strip())
    set_planner_setting_value(db, MASTER_DATA_DEFAULT_APPOINTMENT_TYPE, default_appointment_type.strip())
    set_planner_setting_value(db, MASTER_DATA_DEFAULT_CLASS, default_class.strip())
    set_planner_setting_value(db, MASTER_DATA_DEFAULT_PRODUCT, default_product.strip())
    set_planner_setting_value(db, MASTER_DATA_DEFAULT_VEHICLE, default_vehicle.strip())
    return RedirectResponse(url="/master-data", status_code=302)


@router.post("/master-data/import-default-products")
def master_data_import_default_products(request: Request, db: Session = Depends(get_db)):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    default_products_raw = PLANNER_SETTING_DEFINITIONS.get(MASTER_DATA_PRODUCTS, {}).get("default", "")
    imported_product_names = parse_master_data_entries(default_products_raw)
    set_planner_setting_value(db, MASTER_DATA_PRODUCTS, "\n".join(imported_product_names))
    set_planner_setting_value(db, MASTER_DATA_PRODUCT_ASSIGNMENTS, "")
    if imported_product_names:
        set_planner_setting_value(db, MASTER_DATA_DEFAULT_PRODUCT, imported_product_names[0])
    return RedirectResponse(url="/master-data", status_code=302)


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    planner_option = PLANNER_SETTING_DEFINITIONS.get(PLANNER_SETTING_SHOW_LOCKED_SLOTS, {})
    reminders_option = PLANNER_SETTING_DEFINITIONS.get(PLANNER_SETTING_AUTO_REMINDERS, {})
    show_locked_slots = get_planner_setting_bool(db, PLANNER_SETTING_SHOW_LOCKED_SLOTS)
    auto_reminders = get_planner_setting_bool(db, PLANNER_SETTING_AUTO_REMINDERS)
    whatsapp_number = get_planner_setting_value(db, SCHOOL_WHATSAPP_NUMBER)
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "planner_option_label": planner_option.get("label", "Slots vor Freigabe im Schülerportal anzeigen"),
            "planner_option_description": planner_option.get(
                "description",
                "Wenn aktiv, sehen Fahrschüler zukünftige Slots bereits vorher und erhalten den Hinweis 'Buchbar ab ...'.",
            ),
            "reminders_option_label": reminders_option.get("label", "Automatische Terminerinnerungen aktivieren"),
            "reminders_option_description": reminders_option.get(
                "description",
                "Wenn aktiv, wird der Versand von automatischen Erinnerungshinweisen für bevorstehende Termine vorbereitet.",
            ),
            "show_locked_slots": show_locked_slots,
            "auto_reminders": auto_reminders,
            "whatsapp_number": whatsapp_number,
        },
    )


@router.post("/settings")
def settings_update(
    request: Request,
    show_locked_slots: str | None = Form(None),
    auto_reminders: str | None = Form(None),
    whatsapp_number: str = Form(""),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    set_planner_setting_value(
        db,
        PLANNER_SETTING_SHOW_LOCKED_SLOTS,
        "1" if show_locked_slots == "on" else "0",
    )
    set_planner_setting_value(
        db,
        PLANNER_SETTING_AUTO_REMINDERS,
        "1" if auto_reminders == "on" else "0",
    )
    cleaned_number = "".join(c for c in whatsapp_number if c.isdigit())
    set_planner_setting_value(db, SCHOOL_WHATSAPP_NUMBER, cleaned_number)
    return RedirectResponse(url="/settings", status_code=302)


@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    now = datetime.now()
    today = now.date()
    tomorrow = today + timedelta(days=1)
    start_30_days = today - timedelta(days=29)
    start_7_days = today - timedelta(days=6)

    students_count = db.query(Student).count()
    teachers_count = db.query(Teacher).count()
    open_slots = (
        db.query(AvailabilityWindow)
        .filter(AvailabilityWindow.end_at >= datetime.now())
        .count()
    )

    appointments_30_days = (
        db.query(Appointment)
        .filter(
            Appointment.start_at >= datetime.combine(start_30_days, datetime.min.time()),
            Appointment.start_at < datetime.combine(tomorrow, datetime.min.time()),
        )
        .all()
    )

    trend_labels = [(start_30_days + timedelta(days=idx)).strftime("%d.%m") for idx in range(30)]
    appointments_trend_values = [0] * 30
    cancel_trend_values = [0] * 30
    today_appointments = 0
    today_cancellations = 0

    for appointment in appointments_30_days:
        day_key = appointment.start_at.date()
        idx = (day_key - start_30_days).days
        if 0 <= idx < 30:
            if appointment.status in {"booked", "done"}:
                appointments_trend_values[idx] += 1
            if appointment.status == "cancelled":
                cancel_trend_values[idx] += 1
        if day_key == today:
            if appointment.status in {"booked", "done"}:
                today_appointments += 1
            if appointment.status == "cancelled":
                today_cancellations += 1

    students = db.query(Student).options(joinedload(Student.user), joinedload(Student.teacher).joinedload(Teacher.user)).all()
    student_ids = [student.id for student in students]
    appointments_by_student: dict[int, list[Appointment]] = {student_id: [] for student_id in student_ids}
    if student_ids:
        student_appointments = (
            db.query(Appointment)
            .filter(Appointment.student_id.in_(student_ids))
            .order_by(Appointment.start_at.asc())
            .all()
        )
        for appointment in student_appointments:
            appointments_by_student.setdefault(appointment.student_id, []).append(appointment)

    readiness_by_student = {
        student.id: calculate_student_readiness(student, appointments_by_student.get(student.id, []))
        for student in students
    }
    readiness_green = sum(1 for readiness in readiness_by_student.values() if readiness.get("level") == "green")
    readiness_yellow = sum(1 for readiness in readiness_by_student.values() if readiness.get("level") == "yellow")
    readiness_red = sum(1 for readiness in readiness_by_student.values() if readiness.get("level") == "red")

    urgent_students = []
    for student in students:
        readiness = readiness_by_student.get(student.id)
        if not readiness:
            continue
        urgent_students.append(
            {
                "student_id": student.id,
                "name": student.user.name if student.user else "-",
                "score": readiness.get("score", 0),
                "next_action": readiness.get("next_action", ""),
            }
        )
    urgent_students.sort(key=lambda item: item["score"])
    urgent_students = urgent_students[:5]

    teachers = db.query(Teacher).options(joinedload(Teacher.user)).order_by(Teacher.id.asc()).all()
    teacher_labels = [teacher.user.name if teacher.user else f"Fahrlehrer {teacher.id}" for teacher in teachers]
    teacher_load_values: list[int] = []
    for teacher in teachers:
        teacher_load = (
            db.query(Appointment)
            .filter(
                Appointment.teacher_id == teacher.id,
                Appointment.start_at >= datetime.combine(start_7_days, datetime.min.time()),
                Appointment.start_at < datetime.combine(tomorrow, datetime.min.time()),
                Appointment.status.in_(["booked", "done"]),
            )
            .count()
        )
        teacher_load_values.append(teacher_load)

    open_exam_registrations = (
        db.query(ExamRegistration)
        .filter(ExamRegistration.status.in_(["vorgeschlagen", "angemeldet", "terminiert"]))
        .count()
    )

    auto_reminders_enabled = get_planner_setting_bool(db, PLANNER_SETTING_AUTO_REMINDERS)
    push_mvp_available = auto_reminders_enabled and has_push_config()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "students_count": students_count,
            "teachers_count": teachers_count,
            "open_slots": open_slots,
            "today_appointments": today_appointments,
            "today_cancellations": today_cancellations,
            "open_exam_registrations": open_exam_registrations,
            "trend_labels": trend_labels,
            "appointments_trend_values": appointments_trend_values,
            "cancel_trend_values": cancel_trend_values,
            "readiness_labels": ["Grün", "Gelb", "Rot"],
            "readiness_values": [readiness_green, readiness_yellow, readiness_red],
            "teacher_labels": teacher_labels,
            "teacher_load_values": teacher_load_values,
            "urgent_students": urgent_students,
            "push_mvp_available": push_mvp_available,
        },
    )


@router.get("/students")
def students_list(request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    students = db.query(Student).join(User).order_by(User.name.asc()).all()
    student_ids = [student.id for student in students]
    appointments_by_student: dict[int, list[Appointment]] = {student_id: [] for student_id in student_ids}
    if student_ids:
        student_appointments = (
            db.query(Appointment)
            .filter(Appointment.student_id.in_(student_ids))
            .order_by(Appointment.start_at.asc())
            .all()
        )
        for appointment in student_appointments:
            appointments_by_student.setdefault(appointment.student_id, []).append(appointment)

    readiness_by_student = {
        student.id: calculate_student_readiness(student, appointments_by_student.get(student.id, []))
        for student in students
    }
    return templates.TemplateResponse(
        "students_list.html",
        {
            "request": request,
            "user": user,
            "students": students,
            "readiness_by_student": readiness_by_student,
        },
    )


@router.get("/exams/theory")
def exams_theory(request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    exam_context = get_exam_view_context(db, "theory")

    return templates.TemplateResponse(
        "exams_page.html",
        {
            "request": request,
            "user": user,
            "page_title": "Theorieprüfungen",
            "page_subtitle": "Übersicht des aktuellen Theorie-Prüfungsstatus aller Fahrschüler.",
            "status_labels": {"offen": "Offen", "bestanden": "Bestanden"},
            "rows": exam_context["rows"],
            "inspector_options": exam_context["inspector_options"],
            "registration_status_labels": exam_context["registration_status_labels"],
            "exam_type": "theory",
            "active_exam_nav": "theory",
        },
    )


@router.get("/exams/practical")
def exams_practical(request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    exam_context = get_exam_view_context(db, "practical")

    return templates.TemplateResponse(
        "exams_page.html",
        {
            "request": request,
            "user": user,
            "page_title": "Praxisprüfungen",
            "page_subtitle": "Übersicht des aktuellen Praxis-Prüfungsstatus aller Fahrschüler.",
            "status_labels": {"offen": "Offen", "laeuft": "Läuft", "bestanden": "Bestanden"},
            "rows": exam_context["rows"],
            "inspector_options": exam_context["inspector_options"],
            "registration_status_labels": exam_context["registration_status_labels"],
            "exam_type": "practical",
            "active_exam_nav": "practical",
        },
    )


@router.get("/exams/organizations")
def exams_organizations(request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    students = db.query(Student).join(User).order_by(User.name.asc()).all()
    rows = [
        {
            "student_id": student.id,
            "student_name": student.user.name if student.user else "-",
            "email": student.user.email if student.user else "-",
            "training_class": student.training_class or "-",
            "status": student.practical_status or "offen",
            "exam_organization": student.exam_organization or "-",
            "exam_location": student.branch_exam_location or "-",
        }
        for student in students
    ]

    organizations_summary: dict[str, int] = {}
    for row in rows:
        organization_name = row["exam_organization"]
        organizations_summary[organization_name] = organizations_summary.get(organization_name, 0) + 1

    organizations = [
        {"name": name, "students_count": count}
        for name, count in sorted(organizations_summary.items(), key=lambda item: item[0].lower())
    ]

    inspectors = db.query(ExamInspector).order_by(ExamInspector.organization.asc(), ExamInspector.name.asc()).all()

    inspector_rows = []
    for inspector in inspectors:
        exam_types = parse_exam_type_tokens(inspector.exam_types)
        labels = []
        if "theory" in exam_types:
            labels.append("Theorie")
        if "practical" in exam_types:
            labels.append("Praxis")
        inspector_rows.append(
            {
                "name": inspector.name,
                "organization": inspector.organization,
                "exam_types_label": ", ".join(labels) if labels else "-",
                "is_active": inspector.is_active,
            }
        )

    return templates.TemplateResponse(
        "exam_organizations_page.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "organizations": organizations,
            "inspectors": inspector_rows,
            "active_exam_nav": "organizations",
        },
    )


@router.post("/exams/inspectors/new")
def exams_inspector_create(
    request: Request,
    name: str = Form(""),
    organization: str = Form(""),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    name_clean = name.strip()
    organization_clean = organization.strip()
    if not name_clean or not organization_clean:
        return RedirectResponse(url="/exams/organizations", status_code=302)

    db.add(
        ExamInspector(
            name=name_clean,
            organization=organization_clean,
            exam_types="practical",
            is_active=True,
        )
    )
    db.commit()
    return RedirectResponse(url="/exams/organizations", status_code=302)


@router.post("/exams/register")
def exams_register_student(
    request: Request,
    student_id: int = Form(0),
    exam_type: str = Form("theory"),
    organization: str = Form(""),
    inspector_id: int = Form(0),
    planned_date: str = Form(""),
    status: str = Form("angemeldet"),
    notes: str = Form(""),
    return_to: str = Form(""),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    target_exam_type = "theory" if exam_type == "theory" else "practical"
    redirect_target = return_to.strip() or f"/exams/{target_exam_type}"
    if not redirect_target.startswith("/exams"):
        redirect_target = f"/exams/{target_exam_type}"

    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        return RedirectResponse(url=redirect_target, status_code=302)

    status_key = status if status in EXAM_REGISTRATION_STATUSES else "angemeldet"
    inspector = None
    if inspector_id > 0:
        inspector = db.query(ExamInspector).filter(ExamInspector.id == inspector_id, ExamInspector.is_active == True).first()

    organization_value = organization.strip() or student.exam_organization or "-"
    registration = ExamRegistration(
        student_id=student.id,
        exam_type=target_exam_type,
        organization=organization_value,
        inspector_id=inspector.id if inspector else None,
        planned_date=planned_date.strip() or None,
        status=status_key,
        notes=notes.strip() or None,
    )
    db.add(registration)

    if organization.strip():
        student.exam_organization = organization.strip()

    db.commit()
    return RedirectResponse(url=redirect_target, status_code=302)


@router.get("/students/new")
def students_new_form(request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect
    teachers = db.query(Teacher).join(User).order_by(User.name.asc()).all()
    master_data_context = get_master_data_context(db)
    return templates.TemplateResponse(
        "student_form.html",
        {
            "request": request,
            "user": user,
            "student": None,
            "teachers": teachers,
            "master_data_options": master_data_context["master_data_options"],
            "master_data_defaults": master_data_context["master_data_defaults"],
            "field_errors": [],
            "field_error_keys": [],
            "required_field_labels": REQUIRED_STUDENT_FIELDS,
            "action": "/students/new",
        },
    )


@router.get("/onboarding")
def onboarding_form(request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    context = get_onboarding_template_context(db)
    return templates.TemplateResponse(
        "onboarding_wizard.html",
        {
            "request": request,
            "user": user,
            **context,
            "form_values": {},
            "field_error_keys": [],
            "field_errors": [],
            "required_field_labels": ONBOARDING_REQUIRED_FIELDS,
            "selected_teacher_id": context["teachers"][0].id if context["teachers"] else None,
        },
    )


@router.post("/onboarding")
def onboarding_create(
    request: Request,
    salutation: str = Form(""),
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    teacher_id: int = Form(0),
    appointment_type: str = Form(""),
    training_class: str = Form(""),
    license_key_number: str = Form(""),
    issue_type: str = Form(""),
    enrollment_date: str = Form(""),
    course_name: str = Form(""),
    bf17_enabled: str = Form("0"),
    birth_date: str = Form(""),
    birth_place: str = Form(""),
    citizenship_country: str = Form(""),
    postal_code: str = Form(""),
    city: str = Form(""),
    street: str = Form(""),
    house_number: str = Form(""),
    mobile_phone: str = Form(""),
    phone_private: str = Form(""),
    phone_work: str = Form(""),
    branch_exam_location: str = Form(""),
    exam_organization: str = Form(""),
    has_visual_aid: str = Form("0"),
    info_text: str = Form(""),
    product_name: str = Form(""),
    vehicle_name: str = Form(""),
    price_list: str = Form(""),
    payment_method: str = Form(""),
    cost_bearer: str = Form(""),
    theory_status: str = Form("offen"),
    practical_status: str = Form("offen"),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    form_values = {
        "salutation": salutation,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "teacher_id": str(teacher_id),
        "appointment_type": appointment_type,
        "training_class": training_class,
        "license_key_number": license_key_number,
        "issue_type": issue_type,
        "enrollment_date": enrollment_date,
        "course_name": course_name,
        "bf17_enabled": bf17_enabled,
        "birth_date": birth_date,
        "birth_place": birth_place,
        "citizenship_country": citizenship_country,
        "postal_code": postal_code,
        "city": city,
        "street": street,
        "house_number": house_number,
        "mobile_phone": mobile_phone,
        "phone_private": phone_private,
        "phone_work": phone_work,
        "branch_exam_location": branch_exam_location,
        "exam_organization": exam_organization,
        "has_visual_aid": has_visual_aid,
        "info_text": info_text,
        "product_name": product_name,
        "vehicle_name": vehicle_name,
        "price_list": price_list,
        "payment_method": payment_method,
        "cost_bearer": cost_bearer,
        "theory_status": theory_status,
        "practical_status": practical_status,
        "notes": notes,
    }

    field_error_keys = get_missing_student_required_fields(form_values)
    if teacher_id <= 0:
        field_error_keys.append("teacher_id")
    if not password.strip():
        field_error_keys.append("password")
    address_errors = get_address_validation_errors(form_values)
    for key in address_errors:
        if key not in field_error_keys:
            field_error_keys.append(key)

    if field_error_keys:
        context = get_onboarding_template_context(db)
        return templates.TemplateResponse(
            "onboarding_wizard.html",
            {
                "request": request,
                "user": user,
                **context,
                "form_values": form_values,
                "selected_teacher_id": teacher_id if teacher_id > 0 else None,
                "field_error_keys": field_error_keys,
                "field_errors": (
                    get_missing_required_labels(field_error_keys, ONBOARDING_REQUIRED_FIELDS)
                    + list(address_errors.values())
                ),
                "required_field_labels": ONBOARDING_REQUIRED_FIELDS,
                "error": "Bitte Pflichtfelder ausfüllen.",
            },
            status_code=400,
        )

    selected_teacher = db.query(Teacher).filter(Teacher.id == teacher_id).first()
    if not selected_teacher:
        context = get_onboarding_template_context(db)
        return templates.TemplateResponse(
            "onboarding_wizard.html",
            {
                "request": request,
                "user": user,
                **context,
                "form_values": form_values,
                "selected_teacher_id": None,
                "field_error_keys": ["teacher_id"],
                "field_errors": [ONBOARDING_REQUIRED_FIELDS["teacher_id"]],
                "required_field_labels": ONBOARDING_REQUIRED_FIELDS,
                "error": "Der ausgewählte Fahrlehrer ist nicht gültig.",
            },
            status_code=400,
        )

    if db.query(User).filter(User.email == email).first():
        context = get_onboarding_template_context(db)
        return templates.TemplateResponse(
            "onboarding_wizard.html",
            {
                "request": request,
                "user": user,
                **context,
                "form_values": form_values,
                "selected_teacher_id": teacher_id,
                "field_error_keys": ["email"],
                "field_errors": [ONBOARDING_REQUIRED_FIELDS["email"]],
                "required_field_labels": ONBOARDING_REQUIRED_FIELDS,
                "error": "E-Mail existiert bereits.",
            },
            status_code=400,
        )

    new_user = User(
        name=f"{first_name.strip()} {last_name.strip()}".strip(),
        email=email,
        password_hash=hash_password(password),
        role="student",
    )
    db.add(new_user)
    db.flush()

    student = Student(
        user_id=new_user.id,
        teacher_id=teacher_id,
        salutation=salutation.strip() or None,
        first_name=first_name.strip() or None,
        last_name=last_name.strip() or None,
        birth_date=birth_date.strip() or None,
        birth_place=birth_place.strip() or None,
        citizenship_country=citizenship_country.strip() or None,
        postal_code=postal_code.strip() or None,
        city=city.strip() or None,
        street=street.strip() or None,
        house_number=house_number.strip() or None,
        mobile_phone=mobile_phone.strip() or None,
        phone_private=phone_private.strip() or None,
        phone_work=phone_work.strip() or None,
        appointment_type=appointment_type.strip() or None,
        training_class=training_class.strip() or None,
        license_key_number=license_key_number.strip() or None,
        issue_type=issue_type.strip() or None,
        enrollment_date=enrollment_date.strip() or None,
        course_name=course_name.strip() or None,
        bf17_enabled=parse_form_bool(bf17_enabled),
        branch_exam_location=branch_exam_location.strip() or None,
        exam_organization=exam_organization.strip() or None,
        has_visual_aid=parse_form_bool(has_visual_aid),
        info_text=info_text.strip() or None,
        product_name=product_name.strip() or None,
        vehicle_name=vehicle_name.strip() or None,
        price_list=price_list.strip() or None,
        payment_method=payment_method.strip() or None,
        cost_bearer=cost_bearer.strip() or None,
        theory_status=theory_status,
        practical_status=practical_status,
        notes=notes,
    )
    db.add(student)
    db.commit()

    return RedirectResponse(url="/students", status_code=302)


@router.post("/students/new")
def students_create(
    request: Request,
    salutation: str = Form(""),
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(...),
    teacher_id: int = Form(...),
    appointment_type: str = Form(""),
    training_class: str = Form(""),
    license_key_number: str = Form(""),
    issue_type: str = Form(""),
    enrollment_date: str = Form(""),
    course_name: str = Form(""),
    bf17_enabled: str = Form("0"),
    birth_date: str = Form(""),
    birth_place: str = Form(""),
    citizenship_country: str = Form(""),
    postal_code: str = Form(""),
    city: str = Form(""),
    street: str = Form(""),
    house_number: str = Form(""),
    mobile_phone: str = Form(""),
    phone_private: str = Form(""),
    phone_work: str = Form(""),
    branch_exam_location: str = Form(""),
    exam_organization: str = Form(""),
    has_visual_aid: str = Form("0"),
    info_text: str = Form(""),
    product_name: str = Form(""),
    vehicle_name: str = Form(""),
    price_list: str = Form(""),
    payment_method: str = Form(""),
    cost_bearer: str = Form(""),
    whatsapp_phone: str = Form(""),
    whatsapp_opted_in: str = Form(""),
    theory_status: str = Form("offen"),
    practical_status: str = Form("offen"),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    form_values = {
        "salutation": salutation,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "appointment_type": appointment_type,
        "training_class": training_class,
        "license_key_number": license_key_number,
        "issue_type": issue_type,
        "enrollment_date": enrollment_date,
        "course_name": course_name,
        "bf17_enabled": bf17_enabled,
        "birth_date": birth_date,
        "birth_place": birth_place,
        "citizenship_country": citizenship_country,
        "postal_code": postal_code,
        "city": city,
        "street": street,
        "house_number": house_number,
        "mobile_phone": mobile_phone,
        "phone_private": phone_private,
        "phone_work": phone_work,
        "branch_exam_location": branch_exam_location,
        "exam_organization": exam_organization,
        "has_visual_aid": has_visual_aid,
        "info_text": info_text,
        "product_name": product_name,
        "vehicle_name": vehicle_name,
        "price_list": price_list,
        "payment_method": payment_method,
        "cost_bearer": cost_bearer,
        "notes": notes,
    }
    field_error_keys = get_missing_student_required_fields(form_values)
    address_errors = get_address_validation_errors(form_values)
    for key in address_errors:
        if key not in field_error_keys:
            field_error_keys.append(key)
    field_errors = get_missing_student_required_labels(field_error_keys)
    field_errors.extend(address_errors.values())
    if field_errors:
        teachers = db.query(Teacher).join(User).order_by(User.name.asc()).all()
        master_data_context = get_master_data_context(db)
        return templates.TemplateResponse(
            "student_form.html",
            {
                "request": request,
                "user": get_authenticated_user(request, db),
                "student": None,
                "teachers": teachers,
                "selected_teacher_id": teacher_id,
                "master_data_options": master_data_context["master_data_options"],
                "master_data_defaults": master_data_context["master_data_defaults"],
                "form_values": form_values,
                "field_errors": field_errors,
                "field_error_keys": field_error_keys,
                "required_field_labels": REQUIRED_STUDENT_FIELDS,
                "action": "/students/new",
                "error": "Bitte Pflichtfelder ausfüllen.",
            },
            status_code=400,
        )

    selected_teacher = db.query(Teacher).filter(Teacher.id == teacher_id).first()
    if not selected_teacher:
        return RedirectResponse(url="/students/new", status_code=302)

    if db.query(User).filter(User.email == email).first():
        teachers = db.query(Teacher).join(User).order_by(User.name.asc()).all()
        master_data_context = get_master_data_context(db)
        return templates.TemplateResponse(
            "student_form.html",
            {
                "request": request,
                "user": get_authenticated_user(request, db),
                "student": None,
                "teachers": teachers,
                "selected_teacher_id": teacher_id,
                "master_data_options": master_data_context["master_data_options"],
                "master_data_defaults": master_data_context["master_data_defaults"],
                "form_values": form_values,
                "field_errors": [],
                "field_error_keys": [],
                "required_field_labels": REQUIRED_STUDENT_FIELDS,
                "action": "/students/new",
                "error": "E-Mail existiert bereits.",
            },
            status_code=400,
        )

    user = User(
        name=f"{first_name.strip()} {last_name.strip()}".strip(),
        email=email,
        password_hash=hash_password(password),
        role="student",
    )
    db.add(user)
    db.flush()

    student = Student(
        user_id=user.id,
        teacher_id=teacher_id,
        salutation=salutation.strip() or None,
        first_name=first_name.strip() or None,
        last_name=last_name.strip() or None,
        birth_date=birth_date.strip() or None,
        birth_place=birth_place.strip() or None,
        citizenship_country=citizenship_country.strip() or None,
        postal_code=postal_code.strip() or None,
        city=city.strip() or None,
        street=street.strip() or None,
        house_number=house_number.strip() or None,
        mobile_phone=mobile_phone.strip() or None,
        phone_private=phone_private.strip() or None,
        phone_work=phone_work.strip() or None,
        appointment_type=appointment_type.strip() or None,
        training_class=training_class.strip() or None,
        license_key_number=license_key_number.strip() or None,
        issue_type=issue_type.strip() or None,
        enrollment_date=enrollment_date.strip() or None,
        course_name=course_name.strip() or None,
        bf17_enabled=parse_form_bool(bf17_enabled),
        branch_exam_location=branch_exam_location.strip() or None,
        exam_organization=exam_organization.strip() or None,
        has_visual_aid=parse_form_bool(has_visual_aid),
        info_text=info_text.strip() or None,
        product_name=product_name.strip() or None,
        vehicle_name=vehicle_name.strip() or None,
        price_list=price_list.strip() or None,
        payment_method=payment_method.strip() or None,
        cost_bearer=cost_bearer.strip() or None,
        theory_status=theory_status,
        practical_status=practical_status,
        notes=notes,
    )
    db.add(student)
    db.commit()
    return RedirectResponse(url="/students", status_code=302)


@router.get("/students/{student_id}/edit")
def students_edit_form(student_id: int, request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        return RedirectResponse(url="/students", status_code=302)

    teachers = db.query(Teacher).join(User).order_by(User.name.asc()).all()
    master_data_context = get_master_data_context(db)

    return templates.TemplateResponse(
        "student_form.html",
        {
            "request": request,
            "user": user,
            "student": student,
            "teachers": teachers,
            "master_data_options": master_data_context["master_data_options"],
            "master_data_defaults": master_data_context["master_data_defaults"],
            "field_errors": [],
            "field_error_keys": [],
            "required_field_labels": REQUIRED_STUDENT_FIELDS,
            "action": f"/students/{student_id}/edit",
        },
    )


@router.post("/students/{student_id}/edit")
def students_update(
    student_id: int,
    request: Request,
    salutation: str = Form(""),
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    teacher_id: int = Form(...),
    appointment_type: str = Form(""),
    training_class: str = Form(""),
    license_key_number: str = Form(""),
    issue_type: str = Form(""),
    enrollment_date: str = Form(""),
    course_name: str = Form(""),
    bf17_enabled: str = Form("0"),
    birth_date: str = Form(""),
    birth_place: str = Form(""),
    citizenship_country: str = Form(""),
    postal_code: str = Form(""),
    city: str = Form(""),
    street: str = Form(""),
    house_number: str = Form(""),
    mobile_phone: str = Form(""),
    phone_private: str = Form(""),
    phone_work: str = Form(""),
    branch_exam_location: str = Form(""),
    exam_organization: str = Form(""),
    has_visual_aid: str = Form("0"),
    info_text: str = Form(""),
    product_name: str = Form(""),
    vehicle_name: str = Form(""),
    price_list: str = Form(""),
    payment_method: str = Form(""),
    cost_bearer: str = Form(""),
    theory_status: str = Form(...),
    practical_status: str = Form(...),
    notes: str = Form(""),
    whatsapp_phone: str = Form(""),
    whatsapp_opted_in: str = Form(""),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        return RedirectResponse(url="/students", status_code=302)

    selected_teacher = db.query(Teacher).filter(Teacher.id == teacher_id).first()
    if not selected_teacher:
        return RedirectResponse(url=f"/students/{student_id}/edit", status_code=302)

    form_values = {
        "salutation": salutation,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "appointment_type": appointment_type,
        "training_class": training_class,
        "license_key_number": license_key_number,
        "issue_type": issue_type,
        "enrollment_date": enrollment_date,
        "course_name": course_name,
        "bf17_enabled": bf17_enabled,
        "birth_date": birth_date,
        "birth_place": birth_place,
        "citizenship_country": citizenship_country,
        "postal_code": postal_code,
        "city": city,
        "street": street,
        "house_number": house_number,
        "mobile_phone": mobile_phone,
        "phone_private": phone_private,
        "phone_work": phone_work,
        "branch_exam_location": branch_exam_location,
        "exam_organization": exam_organization,
        "has_visual_aid": has_visual_aid,
        "info_text": info_text,
        "product_name": product_name,
        "vehicle_name": vehicle_name,
        "price_list": price_list,
        "payment_method": payment_method,
        "cost_bearer": cost_bearer,
        "notes": notes,
    }
    field_error_keys = get_missing_student_required_fields(form_values)
    address_errors = get_address_validation_errors(form_values)
    for key in address_errors:
        if key not in field_error_keys:
            field_error_keys.append(key)
    field_errors = get_missing_student_required_labels(field_error_keys)
    field_errors.extend(address_errors.values())
    if field_errors:
        teachers = db.query(Teacher).join(User).order_by(User.name.asc()).all()
        master_data_context = get_master_data_context(db)
        return templates.TemplateResponse(
            "student_form.html",
            {
                "request": request,
                "user": get_authenticated_user(request, db),
                "student": student,
                "teachers": teachers,
                "selected_teacher_id": teacher_id,
                "master_data_options": master_data_context["master_data_options"],
                "master_data_defaults": master_data_context["master_data_defaults"],
                "form_values": form_values,
                "field_errors": field_errors,
                "field_error_keys": field_error_keys,
                "required_field_labels": REQUIRED_STUDENT_FIELDS,
                "action": f"/students/{student_id}/edit",
                "error": "Bitte Pflichtfelder ausfüllen.",
            },
            status_code=400,
        )

    existing_user = db.query(User).filter(User.email == email, User.id != student.user_id).first()
    if existing_user:
        teachers = db.query(Teacher).join(User).order_by(User.name.asc()).all()
        master_data_context = get_master_data_context(db)
        return templates.TemplateResponse(
            "student_form.html",
            {
                "request": request,
                "user": get_authenticated_user(request, db),
                "student": student,
                "teachers": teachers,
                "selected_teacher_id": teacher_id,
                "master_data_options": master_data_context["master_data_options"],
                "master_data_defaults": master_data_context["master_data_defaults"],
                "form_values": form_values,
                "field_errors": [],
                "field_error_keys": [],
                "required_field_labels": REQUIRED_STUDENT_FIELDS,
                "action": f"/students/{student_id}/edit",
                "error": "E-Mail existiert bereits.",
            },
            status_code=400,
        )

    student.user.name = f"{first_name.strip()} {last_name.strip()}".strip()
    student.user.email = email
    student.teacher_id = teacher_id
    student.salutation = salutation.strip() or None
    student.first_name = first_name.strip() or None
    student.last_name = last_name.strip() or None
    student.birth_date = birth_date.strip() or None
    student.birth_place = birth_place.strip() or None
    student.citizenship_country = citizenship_country.strip() or None
    student.postal_code = postal_code.strip() or None
    student.city = city.strip() or None
    student.street = street.strip() or None
    student.house_number = house_number.strip() or None
    student.mobile_phone = mobile_phone.strip() or None
    student.phone_private = phone_private.strip() or None
    student.phone_work = phone_work.strip() or None
    student.appointment_type = appointment_type.strip() or None
    student.training_class = training_class.strip() or None
    student.license_key_number = license_key_number.strip() or None
    student.issue_type = issue_type.strip() or None
    student.enrollment_date = enrollment_date.strip() or None
    student.course_name = course_name.strip() or None
    student.bf17_enabled = parse_form_bool(bf17_enabled)
    student.branch_exam_location = branch_exam_location.strip() or None
    student.exam_organization = exam_organization.strip() or None
    student.has_visual_aid = parse_form_bool(has_visual_aid)
    student.info_text = info_text.strip() or None
    student.product_name = product_name.strip() or None
    student.vehicle_name = vehicle_name.strip() or None
    student.price_list = price_list.strip() or None
    student.payment_method = payment_method.strip() or None
    student.cost_bearer = cost_bearer.strip() or None
    student.whatsapp_phone = "".join(c for c in whatsapp_phone if c.isdigit()) or None
    student.whatsapp_opted_in = whatsapp_opted_in == "on"
    student.theory_status = theory_status
    student.practical_status = practical_status
    student.notes = notes

    db.commit()
    return RedirectResponse(url="/students", status_code=302)


@router.post("/students/{student_id}/delete")
def students_delete(student_id: int, request: Request, db: Session = Depends(get_db)):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    student = db.query(Student).filter(Student.id == student_id).first()
    if student:
        user = student.user
        db.delete(student)
        if user:
            db.delete(user)
        db.commit()
    return RedirectResponse(url="/students", status_code=302)


@router.get("/teachers")
def teachers_list(request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    teachers = db.query(Teacher).join(User).order_by(User.name.asc()).all()
    return templates.TemplateResponse(
        "teachers_list.html",
        {"request": request, "user": user, "teachers": teachers},
    )


@router.post("/teachers/new")
def teachers_create(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    whatsapp_phone: str = Form(""),
    db: Session = Depends(get_db),
):
    admin_user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    if db.query(User).filter(User.email == email).first():
        teachers = db.query(Teacher).join(User).order_by(User.name.asc()).all()
        return templates.TemplateResponse(
            "teachers_list.html",
            {"request": request, "user": admin_user, "teachers": teachers,
             "error": f"E-Mail '{email}' wird bereits verwendet."},
            status_code=400,
        )

    new_user = User(
        name=name.strip(),
        email=email.strip(),
        password_hash=hash_password(password),
        role="teacher",
    )
    db.add(new_user)
    db.flush()

    cleaned = "".join(c for c in whatsapp_phone if c.isdigit())
    teacher = Teacher(user_id=new_user.id, whatsapp_phone=cleaned or None)
    db.add(teacher)
    db.commit()
    return RedirectResponse(url="/teachers", status_code=302)


@router.post("/teachers/{teacher_id}/edit")
def teacher_edit(
    teacher_id: int,
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    admin_user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    teacher = db.query(Teacher).filter(Teacher.id == teacher_id).first()
    if not teacher:
        return RedirectResponse(url="/teachers", status_code=302)

    conflict = db.query(User).filter(User.email == email.strip(), User.id != teacher.user_id).first()
    if conflict:
        teachers = db.query(Teacher).join(User).order_by(User.name.asc()).all()
        return templates.TemplateResponse(
            "teachers_list.html",
            {"request": request, "user": admin_user, "teachers": teachers,
             "error": f"E-Mail '{email}' wird bereits verwendet.", "edit_teacher_id": teacher_id},
            status_code=400,
        )

    teacher.user.name = name.strip()
    teacher.user.email = email.strip()
    db.commit()
    return RedirectResponse(url="/teachers", status_code=302)


@router.post("/teachers/{teacher_id}/password")
def teacher_password_reset(
    teacher_id: int,
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    teacher = db.query(Teacher).filter(Teacher.id == teacher_id).first()
    if teacher:
        teacher.user.password_hash = hash_password(password)
        db.commit()
    return RedirectResponse(url="/teachers", status_code=302)


@router.post("/teachers/{teacher_id}/delete")
def teacher_delete(
    teacher_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    teacher = db.query(Teacher).filter(Teacher.id == teacher_id).first()
    if teacher:
        user = teacher.user
        db.delete(teacher)
        db.flush()
        db.delete(user)
        db.commit()
    return RedirectResponse(url="/teachers", status_code=302)


@router.post("/teachers/{teacher_id}/whatsapp")
def teacher_whatsapp_update(
    teacher_id: int,
    request: Request,
    whatsapp_phone: str = Form(""),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    teacher = db.query(Teacher).filter(Teacher.id == teacher_id).first()
    if teacher:
        cleaned = "".join(c for c in whatsapp_phone if c.isdigit())
        teacher.whatsapp_phone = cleaned or None
        db.commit()
    return RedirectResponse(url="/teachers", status_code=302)


@router.get("/slots")
def slots_list(request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    teachers = db.query(Teacher).join(User).order_by(User.name.asc()).all()
    windows = (
        db.query(AvailabilityWindow)
        .join(Teacher)
        .join(User)
        .order_by(AvailabilityWindow.start_at.asc())
        .all()
    )
    view_mode = request.query_params.get("view", "week")
    if view_mode not in {"day", "week", "month"}:
        view_mode = "week"

    day_param = request.query_params.get("day")
    week_start_param = request.query_params.get("week_start")
    today = date.today()

    try:
        if day_param:
            day_anchor = date.fromisoformat(day_param)
        elif week_start_param:
            day_anchor = date.fromisoformat(week_start_param)
        else:
            day_anchor = today
    except ValueError:
        day_anchor = today

    week_start = day_anchor - timedelta(days=day_anchor.weekday())

    if view_mode == "day":
        range_start = day_anchor
        range_end = day_anchor + timedelta(days=1)
        calendar_days = [
            {
                "date": day_anchor,
                "label": day_anchor.strftime("%a %d.%m"),
            }
        ]
        prev_anchor = (day_anchor - timedelta(days=1)).isoformat()
        next_anchor = (day_anchor + timedelta(days=1)).isoformat()
        range_label = day_anchor.strftime("%d.%m.%Y")
    elif view_mode == "month":
        month_start = day_anchor.replace(day=1)
        days_in_month = calendar.monthrange(day_anchor.year, day_anchor.month)[1]
        next_month_start = (month_start + timedelta(days=32)).replace(day=1)
        range_start = month_start
        range_end = next_month_start
        calendar_days = [
            {
                "date": month_start + timedelta(days=offset),
                "label": (month_start + timedelta(days=offset)).strftime("%d.%m"),
            }
            for offset in range(days_in_month)
        ]
        prev_anchor = (month_start - timedelta(days=1)).replace(day=1).isoformat()
        next_anchor = next_month_start.isoformat()
        range_label = month_start.strftime("%m/%Y")
    else:
        range_start = week_start
        range_end = week_start + timedelta(days=7)
        weekday_labels = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
        calendar_days = [
            {
                "date": day,
                "label": f"{weekday_labels[offset]} {day.strftime('%d.%m')}",
            }
            for offset, day in enumerate([week_start + timedelta(days=offset) for offset in range(7)])
        ]
        prev_anchor = (week_start - timedelta(days=7)).isoformat()
        next_anchor = (week_start + timedelta(days=7)).isoformat()
        range_label = f"{calendar_days[0]['label']} – {calendar_days[-1]['label']}"

    week_dates = [week_start + timedelta(days=offset) for offset in range(7)]
    week_end = range_end
    current_week_start = (today - timedelta(days=today.weekday())).isoformat()
    prev_week_start = (week_start - timedelta(days=7)).isoformat()
    next_week_start = (week_start + timedelta(days=7)).isoformat()
    week_windows = (
        db.query(AvailabilityWindow)
        .join(Teacher)
        .join(User)
        .filter(
            AvailabilityWindow.start_at >= datetime.combine(range_start, datetime.min.time()),
            AvailabilityWindow.start_at < datetime.combine(range_end, datetime.min.time()),
        )
        .order_by(AvailabilityWindow.start_at.asc())
        .all()
    )

    calendar_dates = [day["date"] for day in calendar_days]
    week_grid: dict[int, dict[date, list[AvailabilityWindow]]] = {
        teacher.id: {day: [] for day in calendar_dates} for teacher in teachers
    }
    for window in week_windows:
        day_key = window.start_at.date()
        if window.teacher_id in week_grid and day_key in week_grid[window.teacher_id]:
            week_grid[window.teacher_id][day_key].append(window)

    teacher_ids = [teacher.id for teacher in teachers]
    week_appointments = (
        db.query(Appointment)
        .options(joinedload(Appointment.student).joinedload(Student.user))
        .filter(
            Appointment.teacher_id.in_(teacher_ids),
            Appointment.status == "booked",
            Appointment.start_at < datetime.combine(range_end, datetime.min.time()),
            Appointment.end_at > datetime.combine(range_start, datetime.min.time()),
        )
        .all()
    )
    appointments_by_teacher: dict[int, list[Appointment]] = {teacher_id: [] for teacher_id in teacher_ids}
    for appointment in week_appointments:
        appointments_by_teacher.setdefault(appointment.teacher_id, []).append(appointment)
    window_bookings: dict[int, Appointment] = {}
    for window in week_windows:
        booking = next(
            (
                appointment
                for appointment in appointments_by_teacher.get(window.teacher_id, [])
                if appointment.start_at < window.end_at and appointment.end_at > window.start_at
            ),
            None,
        )
        if booking:
            window_bookings[window.id] = booking

    unseen_requests = (
        db.query(Appointment)
        .options(
            joinedload(Appointment.student).joinedload(Student.user),
            joinedload(Appointment.teacher).joinedload(Teacher.user),
        )
        .filter(
            Appointment.status == "booked",
            Appointment.requires_teacher_confirmation == True,
            Appointment.is_request_seen_by_admin == False,
            Appointment.is_closed == False,
        )
        .order_by(Appointment.start_at.asc())
        .all()
    )
    for request_appointment in unseen_requests:
        request_appointment.is_request_seen_by_admin = True
    if unseen_requests:
        db.commit()

    week_days = [
        {
            "index": offset,
            "date": day,
            "label": f"{['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'][offset]} {day.strftime('%d.%m')}",
        }
        for offset, day in enumerate(week_dates)
    ]

    selected_window_id_param = request.query_params.get("selected_window_id")
    try:
        selected_window_id = int(selected_window_id_param) if selected_window_id_param else None
    except ValueError:
        selected_window_id = None
    selected_window = next((window for window in week_windows if window.id == selected_window_id), None)
    selected_booking = window_bookings.get(selected_window.id) if selected_window else None
    selected_student = selected_booking.student if selected_booking else None

    selected_tab = request.query_params.get("tab", "bearbeiten")
    if selected_tab not in {"bearbeiten", "ausbildung", "termine"}:
        selected_tab = "bearbeiten"
    feedback_code = request.query_params.get("feedback", "").strip()
    feedback_level, feedback_message = SLOTS_FEEDBACK_MESSAGES.get(feedback_code, ("", ""))

    selected_window_students: list[Student] = []
    if selected_window and not selected_booking:
        selected_window_students = (
            db.query(Student)
            .options(joinedload(Student.user))
            .join(User)
            .filter(Student.teacher_id == selected_window.teacher_id)
            .order_by(User.name.asc())
            .all()
        )

    student_stats = {
        "appointments_total": 0,
        "appointments_done": 0,
        "appointments_cancelled": 0,
        "appointments_booked": 0,
    }
    student_future_appointments: list[Appointment] = []
    student_past_appointments: list[Appointment] = []
    if selected_student:
        student_all_appointments = (
            db.query(Appointment)
            .options(joinedload(Appointment.teacher).joinedload(Teacher.user))
            .filter(Appointment.student_id == selected_student.id)
            .order_by(Appointment.start_at.desc())
            .all()
        )
        student_stats = {
            "appointments_total": len(student_all_appointments),
            "appointments_done": sum(1 for appt in student_all_appointments if appt.status == "done"),
            "appointments_cancelled": sum(1 for appt in student_all_appointments if appt.status == "cancelled"),
            "appointments_booked": sum(1 for appt in student_all_appointments if appt.status == "booked"),
        }
        now_dt = datetime.now()
        student_future_appointments = [
            appt
            for appt in student_all_appointments
            if appt.start_at >= now_dt and appt.status == "booked"
        ][:10]
        student_past_appointments = [appt for appt in student_all_appointments if appt.start_at < now_dt][:10]

    master_data_context = get_master_data_context(db)
    master_data_options = master_data_context["master_data_options"]
    product_options = master_data_context["master_data_options"]["products"]
    product_colors = get_product_color_map(product_options)
    show_locked_slots = get_planner_setting_bool(db, PLANNER_SETTING_SHOW_LOCKED_SLOTS)
    alternative_windows: list[dict[str, object]] = []
    if selected_booking and selected_window:
        candidate_windows = (
            db.query(AvailabilityWindow)
            .options(joinedload(AvailabilityWindow.teacher).joinedload(Teacher.user))
            .filter(
                AvailabilityWindow.start_at >= datetime.now(),
                AvailabilityWindow.id != selected_window.id,
            )
            .order_by(AvailabilityWindow.start_at.asc())
            .limit(150)
            .all()
        )
        appointment_duration = normalize_duration_minutes(
            selected_booking.duration_min,
            WEEK_SLOT_DURATION_MINUTES,
        )
        for candidate_window in candidate_windows:
            if not candidate_window.start_at or not candidate_window.end_at:
                continue
            if not candidate_window.teacher_id:
                continue
            candidate_end = candidate_window.start_at + timedelta(minutes=appointment_duration)
            if candidate_end > candidate_window.end_at:
                continue
            if has_booked_overlap_for_teacher(
                db,
                teacher_id=candidate_window.teacher_id,
                start_at=candidate_window.start_at,
                end_at=candidate_end,
                exclude_appointment_id=selected_booking.id,
            ):
                continue
            teacher_name = "Unbekannt"
            if candidate_window.teacher and candidate_window.teacher.user:
                teacher_name = candidate_window.teacher.user.name
            alternative_windows.append(
                {
                    "window_id": candidate_window.id,
                    "teacher_name": teacher_name,
                    "start_at": candidate_window.start_at,
                    "end_at": candidate_end,
                }
            )
            if len(alternative_windows) >= 30:
                break

    return templates.TemplateResponse(
        "slots_list.html",
        {
            "request": request,
            "user": user,
            "teachers": teachers,
            "windows": windows,
            "now": datetime.now(),
            "week_start": week_start,
            "calendar_view_mode": view_mode,
            "calendar_anchor": day_anchor.isoformat(),
            "today_iso": today.isoformat(),
            "calendar_range_label": range_label,
            "calendar_days": calendar_days,
            "prev_anchor": prev_anchor,
            "next_anchor": next_anchor,
            "current_week_start": current_week_start,
            "prev_week_start": prev_week_start,
            "next_week_start": next_week_start,
            "week_days": week_days,
            "week_grid": week_grid,
            "window_bookings": window_bookings,
            "unseen_requests": unseen_requests,
            "selected_window": selected_window,
            "selected_booking": selected_booking,
            "selected_student": selected_student,
            "selected_window_students": selected_window_students,
            "selected_tab": selected_tab,
            "planner_feedback_level": feedback_level,
            "planner_feedback_message": feedback_message,
            "student_stats": student_stats,
            "student_future_appointments": student_future_appointments,
            "student_past_appointments": student_past_appointments,
            "master_data_options": master_data_options,
            "product_options": product_options,
            "product_colors": product_colors,
            "alternative_windows": alternative_windows,
            "master_data_defaults": {
                "appointment_type": selected_student.appointment_type if selected_student and selected_student.appointment_type else master_data_context["master_data_defaults"]["appointment_type"],
                "class": selected_student.training_class if selected_student and selected_student.training_class else master_data_context["master_data_defaults"]["class"],
                "product": selected_student.product_name if selected_student and selected_student.product_name else master_data_context["master_data_defaults"]["product"],
                "vehicle": selected_student.vehicle_name if selected_student and selected_student.vehicle_name else master_data_context["master_data_defaults"]["vehicle"],
            },
            "slot_duration_minutes": WEEK_SLOT_DURATION_MINUTES,
            "default_bookable_hours_before": DEFAULT_BOOKABLE_HOURS_BEFORE,
            "show_locked_slots": show_locked_slots,
        },
    )


@router.post("/slots/{slot_id}/appointments/create")
def slots_create_appointment(
    slot_id: int,
    request: Request,
    student_id: int = Form(...),
    week_start: str = Form(""),
    view: str = Form(""),
    day: str = Form(""),
    selected_window_id: str = Form(""),
    request_message: str = Form(""),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    redirect_url = build_slots_redirect_url(
        week_start=week_start,
        view=view,
        day=day,
        selected_window_id=selected_window_id or str(slot_id),
        tab="bearbeiten",
    )

    def redirect_with_feedback(feedback_code: str) -> RedirectResponse:
        separator = "&" if "?" in redirect_url else "?"
        return RedirectResponse(url=f"{redirect_url}{separator}feedback={feedback_code}", status_code=302)

    window = db.query(AvailabilityWindow).filter(AvailabilityWindow.id == slot_id).first()
    student = (
        db.query(Student)
        .options(joinedload(Student.user))
        .filter(Student.id == student_id)
        .first()
    )
    if not window or not student:
        return redirect_with_feedback("appointment_create_invalid")

    if not student.teacher_id or student.teacher_id != window.teacher_id:
        return redirect_with_feedback("appointment_create_student_mismatch")

    if window.start_at < datetime.now():
        return redirect_with_feedback("appointment_create_past")

    if has_booked_overlap_for_teacher(
        db,
        teacher_id=window.teacher_id,
        start_at=window.start_at,
        end_at=window.end_at,
    ):
        return redirect_with_feedback("appointment_create_overlap")

    duration_min = int((window.end_at - window.start_at).total_seconds() // 60)
    if duration_min <= 0:
        return redirect_with_feedback("appointment_create_duration")

    appointment = Appointment(
        student_id=student.id,
        teacher_id=window.teacher_id,
        start_at=window.start_at,
        end_at=window.end_at,
        duration_min=duration_min,
        status="booked",
        requires_teacher_confirmation=False,
        request_message=request_message.strip() or None,
        is_request_seen_by_admin=True,
    )
    db.add(appointment)
    db.commit()
    return redirect_with_feedback("appointment_created")


@router.post("/slots/appointments/{appointment_id}/cancel")
def slots_cancel_appointment(
    appointment_id: int,
    request: Request,
    view: str = Form("week"),
    day: str = Form(""),
    selected_window_id: str = Form(""),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if appointment and appointment.status == "booked":
        appointment.status = "cancelled"
        appointment.is_read_by_student = True
        appointment.is_closed = True
        appointment.requires_teacher_confirmation = False
        db.commit()

    if view not in {"day", "week", "month"}:
        view = "week"
    redirect_url = f"/slots?view={view}"
    if day:
        redirect_url += f"&day={day}"
    if selected_window_id:
        redirect_url += f"&selected_window_id={selected_window_id}"
    redirect_url += "&tab=bearbeiten"
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post("/slots/appointments/{appointment_id}/details")
def slots_update_appointment_details(
    appointment_id: int,
    request: Request,
    week_start: str = Form(""),
    view: str = Form(""),
    day: str = Form(""),
    selected_window_id: str = Form(""),
    appointment_type: str = Form(""),
    training_class: str = Form(""),
    product_name: str = Form(""),
    vehicle_name: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    appointment = (
        db.query(Appointment)
        .options(joinedload(Appointment.student))
        .filter(Appointment.id == appointment_id)
        .first()
    )
    redirect_url = build_slots_redirect_url(
        week_start=week_start,
        view=view,
        day=day,
        selected_window_id=selected_window_id,
        tab="bearbeiten",
    )
    if not appointment or appointment.status != "booked" or not appointment.student:
        return RedirectResponse(url=redirect_url, status_code=302)

    appointment.student.appointment_type = appointment_type.strip() or None
    appointment.student.training_class = training_class.strip() or None
    appointment.student.product_name = product_name.strip() or None
    appointment.student.vehicle_name = vehicle_name.strip() or None
    appointment.student.notes = notes.strip() or None
    db.commit()
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post("/slots/appointments/{appointment_id}/reschedule")
def slots_reschedule_appointment(
    appointment_id: int,
    request: Request,
    target_window_id: int = Form(...),
    week_start: str = Form(""),
    view: str = Form(""),
    day: str = Form(""),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment or appointment.status != "booked":
        return RedirectResponse(url="/slots", status_code=302)

    target_window = db.query(AvailabilityWindow).filter(AvailabilityWindow.id == target_window_id).first()
    if not target_window:
        return RedirectResponse(url="/slots", status_code=302)

    appointment_duration = normalize_duration_minutes(
        appointment.duration_min,
        WEEK_SLOT_DURATION_MINUTES,
    )
    new_start = target_window.start_at
    new_end = target_window.start_at + timedelta(minutes=appointment_duration)

    redirect_url = build_slots_redirect_url(
        week_start=week_start,
        view=view,
        day=day,
        selected_window_id=str(target_window_id),
        tab="bearbeiten",
    )

    if new_start < datetime.now() or new_end > target_window.end_at:
        return RedirectResponse(url=redirect_url, status_code=302)

    if has_booked_overlap_for_teacher(
        db,
        teacher_id=target_window.teacher_id,
        start_at=new_start,
        end_at=new_end,
        exclude_appointment_id=appointment.id,
    ):
        return RedirectResponse(url=redirect_url, status_code=302)

    appointment.teacher_id = target_window.teacher_id
    appointment.start_at = new_start
    appointment.end_at = new_end
    appointment.duration_min = appointment_duration
    appointment.requires_teacher_confirmation = False
    db.commit()
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post("/slots/settings")
@router.post("/slots/setting")
def slots_settings_update(
    request: Request,
    week_start: str = Form(""),
    show_locked_slots: str | None = Form(None),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    set_planner_setting_value(
        db,
        PLANNER_SETTING_SHOW_LOCKED_SLOTS,
        "1" if show_locked_slots == "on" else "0",
    )
    if week_start:
        return RedirectResponse(url=f"/slots?week_start={week_start}", status_code=302)
    return RedirectResponse(url="/settings", status_code=302)


@router.post("/slots/new")
def slots_create(
    request: Request,
    teacher_id: int = Form(...),
    start_at: str = Form(...),
    end_at: str = Form(...),
    bookable_hours_before: int = Form(DEFAULT_BOOKABLE_HOURS_BEFORE),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    start_dt = parse_datetime_local(start_at)
    end_dt = parse_datetime_local(end_at)

    if (
        start_dt >= end_dt
        or start_dt < datetime.now()
        or bookable_hours_before < 0
        or start_dt.date() != end_dt.date()
    ):
        return RedirectResponse(url="/slots", status_code=302)

    existing = (
        db.query(AvailabilityWindow)
        .filter(
            AvailabilityWindow.teacher_id == teacher_id,
            AvailabilityWindow.start_at < end_dt,
            AvailabilityWindow.end_at > start_dt,
        )
        .first()
    )
    if existing:
        return RedirectResponse(url="/slots", status_code=302)

    window = AvailabilityWindow(
        teacher_id=teacher_id,
        start_at=start_dt,
        end_at=end_dt,
        bookable_from=start_dt - timedelta(hours=bookable_hours_before),
        source="manual",
    )
    db.add(window)
    db.commit()
    return RedirectResponse(url="/slots", status_code=302)


@router.post("/slots/week-create")
def slots_create_week(
    request: Request,
    teacher_id: int = Form(...),
    week_start: str = Form(...),
    day_indexes: list[int] = Form(...),
    day_start_time: str = Form(...),
    day_end_time: str = Form(...),
    bookable_hours_before: int = Form(DEFAULT_BOOKABLE_HOURS_BEFORE),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    if bookable_hours_before < 0:
        return RedirectResponse(url=f"/slots?week_start={week_start}", status_code=302)

    try:
        week_start_date = date.fromisoformat(week_start)
        day_start = datetime.strptime(day_start_time, "%H:%M").time()
        day_end = datetime.strptime(day_end_time, "%H:%M").time()
    except ValueError:
        return RedirectResponse(url=f"/slots?week_start={week_start}", status_code=302)

    if day_start >= day_end:
        return RedirectResponse(url=f"/slots?week_start={week_start}", status_code=302)

    created_windows = 0
    slot_duration = timedelta(minutes=WEEK_SLOT_DURATION_MINUTES)
    now = datetime.now()

    for day_index in sorted(set(day_indexes)):
        if day_index < 0 or day_index > 6:
            continue

        day_date = week_start_date + timedelta(days=day_index)
        cursor = datetime.combine(day_date, day_start)
        day_end_at = datetime.combine(day_date, day_end)

        while cursor + slot_duration <= day_end_at:
            end_at = cursor + slot_duration
            if cursor >= now:
                has_window_overlap = (
                    db.query(AvailabilityWindow)
                    .filter(
                        AvailabilityWindow.teacher_id == teacher_id,
                        AvailabilityWindow.start_at < end_at,
                        AvailabilityWindow.end_at > cursor,
                    )
                    .first()
                    is not None
                )
                if not has_window_overlap:
                    db.add(
                        AvailabilityWindow(
                            teacher_id=teacher_id,
                            start_at=cursor,
                            end_at=end_at,
                            bookable_from=cursor - timedelta(hours=bookable_hours_before),
                            source="generated",
                        )
                    )
                    created_windows += 1

            cursor += slot_duration

    if created_windows > 0:
        db.commit()
    return RedirectResponse(url=f"/slots?week_start={week_start}", status_code=302)


@router.post("/slots/{slot_id}/update")
def slots_update(
    slot_id: int,
    request: Request,
    week_start: str = Form(""),
    view: str = Form(""),
    day: str = Form(""),
    selected_window_id: str = Form(""),
    start_at: str = Form(...),
    end_at: str = Form(...),
    bookable_hours_before: int = Form(DEFAULT_BOOKABLE_HOURS_BEFORE),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    window = db.query(AvailabilityWindow).filter(AvailabilityWindow.id == slot_id).first()
    if not window:
        return RedirectResponse(url="/slots", status_code=302)

    redirect_url = build_slots_redirect_url(
        week_start=week_start,
        view=view,
        day=day,
        selected_window_id=selected_window_id,
    )

    if has_booked_appointments_in_window(db, window):
        return RedirectResponse(url=redirect_url, status_code=302)

    try:
        start_dt = parse_datetime_local(start_at)
        end_dt = parse_datetime_local(end_at)
    except ValueError:
        return RedirectResponse(url=redirect_url, status_code=302)

    if (
        start_dt >= end_dt
        or start_dt < datetime.now()
        or bookable_hours_before < 0
        or start_dt.date() != end_dt.date()
    ):
        return RedirectResponse(url=redirect_url, status_code=302)

    has_window_overlap = (
        db.query(AvailabilityWindow)
        .filter(
            AvailabilityWindow.teacher_id == window.teacher_id,
            AvailabilityWindow.id != window.id,
            AvailabilityWindow.start_at < end_dt,
            AvailabilityWindow.end_at > start_dt,
        )
        .first()
        is not None
    )
    if has_window_overlap:
        return RedirectResponse(url=redirect_url, status_code=302)

    window.start_at = start_dt
    window.end_at = end_dt
    window.bookable_from = start_dt - timedelta(hours=bookable_hours_before)
    db.commit()
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post("/slots/week-generated-delete")
def slots_delete_generated_week(
    request: Request,
    teacher_id: int = Form(...),
    week_start: str = Form(...),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    try:
        week_start_date = date.fromisoformat(week_start)
    except ValueError:
        return RedirectResponse(url="/slots", status_code=302)

    week_end_date = week_start_date + timedelta(days=7)
    windows = (
        db.query(AvailabilityWindow)
        .filter(
            AvailabilityWindow.teacher_id == teacher_id,
            AvailabilityWindow.source == "generated",
            AvailabilityWindow.start_at >= datetime.combine(week_start_date, datetime.min.time()),
            AvailabilityWindow.start_at < datetime.combine(week_end_date, datetime.min.time()),
        )
        .all()
    )

    for window in windows:
        if not has_booked_appointments_in_window(db, window):
            db.delete(window)
    db.commit()
    return RedirectResponse(url=f"/slots?week_start={week_start}", status_code=302)


@router.post("/slots/{slot_id}/delete")
def slots_delete(
    slot_id: int,
    request: Request,
    week_start: str = Form(""),
    view: str = Form(""),
    day: str = Form(""),
    selected_window_id: str = Form(""),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    window = db.query(AvailabilityWindow).filter(AvailabilityWindow.id == slot_id).first()
    if window:
        if not has_booked_appointments_in_window(db, window):
            db.delete(window)
        db.commit()
    return RedirectResponse(
        url=build_slots_redirect_url(
            week_start=week_start,
            view=view,
            day=day,
            selected_window_id=selected_window_id,
        ),
        status_code=302,
    )
