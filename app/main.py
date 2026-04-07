from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from pathlib import Path
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.database import Base, engine
from app.database import SessionLocal
from app.models import User
from app.planner_settings import ensure_default_planner_settings
from app.routes.admin_routes import router as admin_router
from app.routes.appointments_routes import router as appointments_router
from app.routes.auth_routes import router as auth_router
from app.routes.portal_routes import router as portal_router

app = FastAPI(title="FahrManager 360")

app.add_middleware(
    SessionMiddleware,
    secret_key="fahrmanager360-local-dev-secret",
)

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def root():
    return RedirectResponse(url="/login", status_code=302)


def ensure_demo_admin(db: Session) -> None:
    admin_email = "admin@fahrmanager360.local"
    existing_admin = db.query(User).filter(User.email == admin_email).first()
    if existing_admin:
        return

    db.add(
        User(
            email=admin_email,
            name="Admin",
            role="admin",
            password_hash=hash_password("admin123"),
        )
    )
    db.commit()


def run_local_schema_migrations() -> None:
    inspector = inspect(engine)

    if inspector.has_table("appointments"):
        columns = {column["name"] for column in inspector.get_columns("appointments")}
        if "duration_min" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE appointments ADD COLUMN duration_min INTEGER NOT NULL DEFAULT 60")
                )
        if "is_read_by_student" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE appointments ADD COLUMN is_read_by_student BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
        if "is_closed" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE appointments ADD COLUMN is_closed BOOLEAN NOT NULL DEFAULT 0")
                )
        if "requires_teacher_confirmation" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE appointments ADD COLUMN requires_teacher_confirmation BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
        if "request_message" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE appointments ADD COLUMN request_message TEXT"))
        if "is_request_seen_by_admin" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE appointments ADD COLUMN is_request_seen_by_admin BOOLEAN NOT NULL DEFAULT 0"
                    )
                )

    has_old_slots = inspector.has_table("availability_slots")
    has_windows = inspector.has_table("availability_windows")
    if has_old_slots and has_windows:
        with engine.begin() as connection:
            window_count = connection.execute(text("SELECT COUNT(*) FROM availability_windows")).scalar() or 0
            if window_count == 0:
                connection.execute(
                    text(
                        """
                        INSERT INTO availability_windows (teacher_id, start_at, end_at, bookable_from)
                        SELECT teacher_id, start_at, end_at, start_at
                        FROM availability_slots
                        """
                    )
                )

    if has_windows:
        columns = {column["name"] for column in inspector.get_columns("availability_windows")}
        if "bookable_from" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE availability_windows ADD COLUMN bookable_from DATETIME")
                )
                connection.execute(
                    text("UPDATE availability_windows SET bookable_from = start_at WHERE bookable_from IS NULL")
                )
        if "source" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE availability_windows ADD COLUMN source VARCHAR(20) NOT NULL DEFAULT 'manual'"
                    )
                )

    if inspector.has_table("students"):
        columns = {column["name"] for column in inspector.get_columns("students")}
        if "teacher_id" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN teacher_id INTEGER"))
                connection.execute(
                    text(
                        """
                        UPDATE students
                        SET teacher_id = (
                            SELECT appointments.teacher_id
                            FROM appointments
                            WHERE appointments.student_id = students.id
                            ORDER BY appointments.start_at ASC
                            LIMIT 1
                        )
                        WHERE teacher_id IS NULL
                        """
                    )
                )
        if "appointment_type" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN appointment_type VARCHAR(100)"))
        if "training_class" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN training_class VARCHAR(100)"))
        if "salutation" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN salutation VARCHAR(30)"))
        if "first_name" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN first_name VARCHAR(100)"))
        if "last_name" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN last_name VARCHAR(100)"))
        if "birth_date" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN birth_date VARCHAR(20)"))
        if "birth_place" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN birth_place VARCHAR(120)"))
        if "citizenship_country" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN citizenship_country VARCHAR(120)"))
        if "postal_code" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN postal_code VARCHAR(20)"))
        if "city" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN city VARCHAR(120)"))
        if "street" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN street VARCHAR(120)"))
        if "house_number" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN house_number VARCHAR(30)"))
        if "mobile_phone" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN mobile_phone VARCHAR(50)"))
        if "phone_private" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN phone_private VARCHAR(50)"))
        if "phone_work" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN phone_work VARCHAR(50)"))
        if "license_key_number" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN license_key_number VARCHAR(80)"))
        if "issue_type" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN issue_type VARCHAR(80)"))
        if "enrollment_date" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN enrollment_date VARCHAR(20)"))
        if "course_name" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN course_name VARCHAR(120)"))
        if "bf17_enabled" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN bf17_enabled BOOLEAN NOT NULL DEFAULT 0"))
        if "branch_exam_location" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN branch_exam_location VARCHAR(150)"))
        if "exam_organization" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN exam_organization VARCHAR(150)"))
        if "has_visual_aid" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN has_visual_aid BOOLEAN NOT NULL DEFAULT 0"))
        if "info_text" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN info_text TEXT"))
        if "product_name" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN product_name VARCHAR(120)"))
        if "vehicle_name" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN vehicle_name VARCHAR(120)"))
        if "price_list" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN price_list VARCHAR(120)"))
        if "payment_method" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN payment_method VARCHAR(50)"))
        if "cost_bearer" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE students ADD COLUMN cost_bearer VARCHAR(50)"))


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    run_local_schema_migrations()
    db = SessionLocal()
    try:
        ensure_demo_admin(db)
        ensure_default_planner_settings(db)
    finally:
        db.close()


app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(appointments_router)
app.include_router(portal_router)
