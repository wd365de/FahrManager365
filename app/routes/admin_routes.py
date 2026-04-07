import calendar
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.auth import hash_password
from app.database import get_db
from app.models import Appointment, AvailabilityWindow, Student, Teacher, User
from app.readiness import calculate_student_readiness
from app.planner_settings import (
    get_planner_setting_bool,
    get_planner_setting_value,
    set_planner_setting_value,
)
from app.settings import DEFAULT_BOOKABLE_HOURS_BEFORE, WEEK_SLOT_DURATION_MINUTES
from app.settings import (
    DEFAULT_PRODUCT_ASSIGNMENTS,
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
    MASTER_DATA_VEHICLES,
    PLANNER_SETTING_SHOW_LOCKED_SLOTS,
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
        return None, RedirectResponse(url="/portal", status_code=302)
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
    "phone_private": "Telefon privat",
    "phone_work": "Telefon beruflich",
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


def get_missing_student_required_fields(form_values: dict[str, str]) -> list[str]:
    missing_fields: list[str] = []
    for key in REQUIRED_STUDENT_FIELDS:
        if not str(form_values.get(key, "")).strip():
            missing_fields.append(key)
    return missing_fields


def get_missing_student_required_labels(missing_fields: list[str]) -> list[str]:
    return [REQUIRED_STUDENT_FIELDS[key] for key in missing_fields if key in REQUIRED_STUDENT_FIELDS]


def parse_product_assignments(raw_value: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for product_name in parse_product_names(raw_value):
        rows.append({"product_name": product_name, "assignment": product_name})
    return rows


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

    appointment_types = parse_master_data_entries(appointment_types_raw)
    classes = parse_master_data_entries(classes_raw)
    products = parse_master_data_entries(products_raw)
    product_assignments = parse_product_assignments(product_assignments_raw)
    vehicles = parse_master_data_entries(vehicles_raw)
    payment_methods = parse_master_data_entries(payment_methods_raw)
    courses = parse_master_data_entries(courses_raw)
    issue_types = parse_master_data_entries(issue_types_raw)
    price_lists = parse_master_data_entries(price_lists_raw)

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

    imported_product_names = parse_product_names(DEFAULT_PRODUCT_ASSIGNMENTS)
    set_planner_setting_value(db, MASTER_DATA_PRODUCT_ASSIGNMENTS, "\n".join(imported_product_names))
    unique_product_names = []
    for product_name in imported_product_names:
        if product_name not in unique_product_names:
            unique_product_names.append(product_name)
    set_planner_setting_value(db, MASTER_DATA_PRODUCTS, "\n".join(unique_product_names))
    return RedirectResponse(url="/master-data", status_code=302)


@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    students_count = db.query(Student).count()
    teachers_count = db.query(Teacher).count()
    open_slots = (
        db.query(AvailabilityWindow)
        .filter(AvailabilityWindow.end_at >= datetime.now())
        .count()
    )
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "students_count": students_count,
            "teachers_count": teachers_count,
            "open_slots": open_slots,
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
    field_errors = get_missing_student_required_labels(field_error_keys)
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
    field_errors = get_missing_student_required_labels(field_error_keys)
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
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    if db.query(User).filter(User.email == email).first():
        return RedirectResponse(url="/teachers", status_code=302)

    user = User(
        name=name,
        email=email,
        password_hash=hash_password(password),
        role="teacher",
    )
    db.add(user)
    db.flush()

    teacher = Teacher(user_id=user.id)
    db.add(teacher)
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
    show_locked_slots = get_planner_setting_bool(db, PLANNER_SETTING_SHOW_LOCKED_SLOTS)

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
            "selected_tab": selected_tab,
            "student_stats": student_stats,
            "student_future_appointments": student_future_appointments,
            "student_past_appointments": student_past_appointments,
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
    return RedirectResponse(url="/slots", status_code=302)


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
