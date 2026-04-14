from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(Enum("admin", "teacher", "student", name="user_roles"), nullable=False)
    name = Column(String(255), nullable=False)

    student = relationship("Student", back_populates="user", uselist=False)
    teacher = relationship("Teacher", back_populates="user", uselist=False)
    push_subscriptions = relationship("PushSubscription", back_populates="user")


class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)
    theory_status = Column(
        Enum("offen", "bestanden", name="theory_status"),
        nullable=False,
        default="offen",
    )
    practical_status = Column(
        Enum("offen", "laeuft", "bestanden", name="practical_status"),
        nullable=False,
        default="offen",
    )
    salutation = Column(String(30), nullable=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    birth_date = Column(String(20), nullable=True)
    birth_place = Column(String(120), nullable=True)
    citizenship_country = Column(String(120), nullable=True)
    postal_code = Column(String(20), nullable=True)
    city = Column(String(120), nullable=True)
    street = Column(String(120), nullable=True)
    house_number = Column(String(30), nullable=True)
    mobile_phone = Column(String(50), nullable=True)
    phone_private = Column(String(50), nullable=True)
    phone_work = Column(String(50), nullable=True)
    appointment_type = Column(String(100), nullable=True)
    training_class = Column(String(100), nullable=True)
    license_key_number = Column(String(80), nullable=True)
    issue_type = Column(String(80), nullable=True)
    enrollment_date = Column(String(20), nullable=True)
    course_name = Column(String(120), nullable=True)
    bf17_enabled = Column(Boolean, nullable=False, default=False)
    branch_exam_location = Column(String(150), nullable=True)
    exam_organization = Column(String(150), nullable=True)
    has_visual_aid = Column(Boolean, nullable=False, default=False)
    info_text = Column(Text, nullable=True)
    product_name = Column(String(120), nullable=True)
    vehicle_name = Column(String(120), nullable=True)
    price_list = Column(String(120), nullable=True)
    payment_method = Column(String(50), nullable=True)
    cost_bearer = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    whatsapp_phone = Column(String(50), nullable=True)
    whatsapp_opted_in = Column(Boolean, nullable=False, default=False)
    reminder_minutes = Column(Integer, nullable=False, default=30)
    signature_data = Column(Text, nullable=True)       # base64 PNG
    contract_signed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="student")
    teacher = relationship("Teacher", back_populates="students")
    appointments = relationship("Appointment", back_populates="student")
    invoices = relationship("Invoice", back_populates="student", order_by="Invoice.invoice_date.desc()")


class Teacher(Base):
    __tablename__ = "teachers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    whatsapp_phone = Column(String(50), nullable=True)
    reminder_minutes = Column(Integer, nullable=False, default=30)

    user = relationship("User", back_populates="teacher")
    students = relationship("Student", back_populates="teacher")
    windows = relationship("AvailabilityWindow", back_populates="teacher")
    appointments = relationship("Appointment", back_populates="teacher")


class AvailabilityWindow(Base):
    __tablename__ = "availability_windows"

    id = Column(Integer, primary_key=True, index=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=False)
    start_at = Column(DateTime, nullable=False)
    end_at = Column(DateTime, nullable=False)
    bookable_from = Column(DateTime, nullable=False)
    source = Column(String(20), nullable=False, default="manual")

    teacher = relationship("Teacher", back_populates="windows")

    __table_args__ = (
        UniqueConstraint("teacher_id", "start_at", "end_at", name="uq_teacher_window"),
    )


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=False)
    start_at = Column(DateTime, nullable=False)
    end_at = Column(DateTime, nullable=False)
    duration_min = Column(Integer, nullable=False, default=60)
    status = Column(
        Enum("booked", "cancelled", "done", name="appointment_status"),
        nullable=False,
        default="booked",
    )
    requires_teacher_confirmation = Column(Boolean, nullable=False, default=False)
    request_message = Column(Text, nullable=True)
    is_request_seen_by_admin = Column(Boolean, nullable=False, default=False)
    is_read_by_student = Column(Boolean, nullable=False, default=False)
    is_closed = Column(Boolean, nullable=False, default=False)
    reminder_sent = Column(Boolean, nullable=False, default=False)

    student = relationship("Student", back_populates="appointments")
    teacher = relationship("Teacher", back_populates="appointments")

    __table_args__ = ()


class ExamInspector(Base):
    __tablename__ = "exam_inspectors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    organization = Column(String(150), nullable=False)
    exam_types = Column(String(50), nullable=False, default="practical")
    is_active = Column(Boolean, nullable=False, default=True)

    registrations = relationship("ExamRegistration", back_populates="inspector")


class ExamRegistration(Base):
    __tablename__ = "exam_registrations"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    exam_type = Column(Enum("theory", "practical", name="exam_type"), nullable=False)
    organization = Column(String(150), nullable=False)
    inspector_id = Column(Integer, ForeignKey("exam_inspectors.id"), nullable=True)
    planned_date = Column(String(20), nullable=True)
    status = Column(
        Enum("vorgeschlagen", "angemeldet", "terminiert", "bestanden", "nicht_bestanden", name="exam_registration_status"),
        nullable=False,
        default="angemeldet",
    )
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    student = relationship("Student")
    inspector = relationship("ExamInspector", back_populates="registrations")


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    invoice_number = Column(String(50), nullable=False, unique=True)
    invoice_date = Column(String(20), nullable=False)   # ISO date
    due_date = Column(String(20), nullable=True)
    items_json = Column(Text, nullable=False, default="[]")  # JSON list
    tax_rate = Column(Integer, nullable=False, default=0)    # % (0 or 19)
    notes = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="offen")  # offen/bezahlt/storniert
    stripe_payment_url = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    student = relationship("Student", back_populates="invoices")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(String(255), nullable=False)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    endpoint = Column(Text, nullable=False, unique=True)
    p256dh = Column(Text, nullable=False)
    auth = Column(Text, nullable=False)
    user_agent = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="push_subscriptions")
