"""SQLAlchemy tables for professionals, services, users, and bookings."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative metadata base."""


class Professional(Base):
    """A clinic professional who can be booked."""

    __tablename__ = "professionals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    is_senior: Mapped[bool] = mapped_column(Boolean, default=False)

    bookings: Mapped[list["Booking"]] = relationship(back_populates="professional")


class Service(Base):
    """A fixed-duration clinic service (A–E)."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    seniors_only: Mapped[bool] = mapped_column(Boolean, default=False)

    bookings: Mapped[list["Booking"]] = relationship(back_populates="service")


class User(Base):
    """Patient account (name + email; no password in v1)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(256), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    bookings: Mapped[list["Booking"]] = relationship(back_populates="user")


class Booking(Base):
    """A patient booking. Patient name/email stay off public busy queries."""

    __tablename__ = "bookings"
    __table_args__ = (Index("ix_bookings_pro_start", "professional_id", "starts_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    professional_id: Mapped[int] = mapped_column(ForeignKey("professionals.id"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    patient_name: Mapped[str] = mapped_column(String(128))
    patient_email: Mapped[str] = mapped_column(String(256))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32))

    professional: Mapped[Professional] = relationship(back_populates="bookings")
    service: Mapped[Service] = relationship(back_populates="bookings")
    user: Mapped[User | None] = relationship(back_populates="bookings")


STATUS_CONFIRMED = "confirmed"
STATUS_PENDING_DOCTOR = "pending_doctor"
STATUS_CANCELLED = "cancelled"
ACTIVE_STATUSES = (STATUS_CONFIRMED, STATUS_PENDING_DOCTOR)
