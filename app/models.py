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

    user = relationship("User", back_populates="student")
    teacher = relationship("Teacher", back_populates="students")
    appointments = relationship("Appointment", back_populates="student")


class Teacher(Base):
    __tablename__ = "teachers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

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

    student = relationship("Student", back_populates="appointments")
    teacher = relationship("Teacher", back_populates="appointments")

    __table_args__ = (
        UniqueConstraint("teacher_id", "start_at", "end_at", name="uq_teacher_appointment"),
    )


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
