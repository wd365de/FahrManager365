import io
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import stripe
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Invoice, Student, User
from app.planner_settings import get_planner_setting_value
from app.routes.utils import get_authenticated_user, redirect_to_login
from app.settings import SCHOOL_NAME, SCHOOL_LOGO_URL

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def require_admin(request: Request, db: Session):
    user = get_authenticated_user(request, db)
    if not user or user.role != "admin":
        return None, redirect_to_login()
    return user, None


def _next_invoice_number(db: Session) -> str:
    year = date.today().year
    prefix = f"RE-{year}-"
    last = (
        db.query(Invoice)
        .filter(Invoice.invoice_number.like(f"{prefix}%"))
        .order_by(Invoice.invoice_number.desc())
        .first()
    )
    if last:
        try:
            seq = int(last.invoice_number.split("-")[-1]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


@router.get("/invoices")
def invoices_list(request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    raw_invoices = (
        db.query(Invoice)
        .options(joinedload(Invoice.student).joinedload(Student.user))
        .order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
        .all()
    )
    invoices_with_totals = []
    for inv in raw_invoices:
        items = json.loads(inv.items_json or "[]")
        subtotal = sum(i["quantity"] * i["unit_price"] for i in items)
        total = subtotal * (1 + inv.tax_rate / 100)
        invoices_with_totals.append({"invoice": inv, "total": total})

    students = db.query(Student).join(User).order_by(User.name.asc()).all()
    saved = request.query_params.get("saved") == "1"
    return templates.TemplateResponse(
        "invoices_list.html",
        {
            "request": request,
            "user": user,
            "invoices_with_totals": invoices_with_totals,
            "students": students,
            "saved": saved,
        },
    )


@router.post("/invoices/new")
def invoice_create(
    request: Request,
    student_id: int = Form(...),
    invoice_date: str = Form(...),
    due_date: str = Form(""),
    tax_rate: int = Form(0),
    notes: str = Form(""),
    item_desc: list[str] = Form(default=[]),
    item_qty: list[str] = Form(default=[]),
    item_price: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    items = []
    for desc, qty, price in zip(item_desc, item_qty, item_price):
        try:
            items.append({
                "description": desc.strip(),
                "quantity": float(qty),
                "unit_price": float(price.replace(",", ".")),
            })
        except (ValueError, AttributeError):
            pass

    invoice = Invoice(
        student_id=student_id,
        invoice_number=_next_invoice_number(db),
        invoice_date=invoice_date,
        due_date=due_date.strip() or None,
        items_json=json.dumps(items, ensure_ascii=False),
        tax_rate=max(0, min(100, tax_rate)),
        notes=notes.strip() or None,
        status="offen",
    )
    db.add(invoice)
    db.commit()
    return RedirectResponse(url=f"/invoices/{invoice.id}?saved=1", status_code=302)


@router.get("/invoices/{invoice_id}")
def invoice_detail(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    invoice = (
        db.query(Invoice)
        .options(joinedload(Invoice.student).joinedload(Student.user))
        .filter(Invoice.id == invoice_id)
        .first()
    )
    if not invoice:
        return RedirectResponse(url="/invoices", status_code=302)

    items = json.loads(invoice.items_json or "[]")
    subtotal = sum(i["quantity"] * i["unit_price"] for i in items)
    tax_amount = subtotal * invoice.tax_rate / 100
    total = subtotal + tax_amount

    saved = request.query_params.get("saved") == "1"
    return templates.TemplateResponse(
        "invoice_detail.html",
        {
            "request": request,
            "user": user,
            "invoice": invoice,
            "items": items,
            "subtotal": subtotal,
            "tax_amount": tax_amount,
            "total": total,
            "saved": saved,
        },
    )


@router.post("/invoices/{invoice_id}/status")
def invoice_set_status(
    invoice_id: int,
    request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if invoice and status in ("offen", "bezahlt", "storniert"):
        invoice.status = status
        db.commit()
    return RedirectResponse(url=f"/invoices/{invoice_id}", status_code=302)


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    user, redirect = require_admin(request, db)
    if redirect:
        return redirect

    invoice = (
        db.query(Invoice)
        .options(joinedload(Invoice.student).joinedload(Student.user))
        .filter(Invoice.id == invoice_id)
        .first()
    )
    if not invoice:
        return RedirectResponse(url="/invoices", status_code=302)

    school_name = (get_planner_setting_value(db, SCHOOL_NAME) or "Fahrschule").strip()
    items = json.loads(invoice.items_json or "[]")
    subtotal = sum(i["quantity"] * i["unit_price"] for i in items)
    tax_amount = subtotal * invoice.tax_rate / 100
    total = subtotal + tax_amount

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    bold = ParagraphStyle("bold", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=11)
    normal = ParagraphStyle("normal", parent=styles["Normal"], fontName="Helvetica", fontSize=10)
    small = ParagraphStyle("small", parent=styles["Normal"], fontName="Helvetica", fontSize=8, textColor=colors.grey)

    story = []

    # Header
    story.append(Paragraph(school_name, ParagraphStyle("h1", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=16)))
    story.append(Spacer(1, 6 * mm))

    student_name = invoice.student.user.name if invoice.student and invoice.student.user else "–"
    student_addr = ""
    if invoice.student:
        parts = []
        if invoice.student.street and invoice.student.house_number:
            parts.append(f"{invoice.student.street} {invoice.student.house_number}")
        elif invoice.student.street:
            parts.append(invoice.student.street)
        if invoice.student.postal_code or invoice.student.city:
            parts.append(f"{invoice.student.postal_code or ''} {invoice.student.city or ''}".strip())
        student_addr = "\n".join(parts)

    story.append(Paragraph(student_name, bold))
    if student_addr:
        story.append(Paragraph(student_addr.replace("\n", "<br/>"), normal))
    story.append(Spacer(1, 8 * mm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.black))
    story.append(Spacer(1, 4 * mm))

    # Invoice meta
    meta = [
        ["Rechnungsnummer:", invoice.invoice_number],
        ["Rechnungsdatum:", invoice.invoice_date],
    ]
    if invoice.due_date:
        meta.append(["Fälligkeitsdatum:", invoice.due_date])
    meta_table = Table(meta, colWidths=[50 * mm, 80 * mm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph("Rechnung", ParagraphStyle("h2", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=13)))
    story.append(Spacer(1, 4 * mm))

    # Items table
    table_data = [["Beschreibung", "Menge", "Einzelpreis", "Gesamt"]]
    for item in items:
        line_total = item["quantity"] * item["unit_price"]
        table_data.append([
            item["description"],
            f"{item['quantity']:.0f}" if item["quantity"] == int(item["quantity"]) else f"{item['quantity']:.2f}",
            f"{item['unit_price']:.2f} €",
            f"{line_total:.2f} €",
        ])

    col_widths = [90 * mm, 20 * mm, 30 * mm, 30 * mm]
    items_table = Table(table_data, colWidths=col_widths)
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 4 * mm))

    # Totals
    totals_data = []
    totals_data.append(["Zwischensumme", f"{subtotal:.2f} €"])
    if invoice.tax_rate > 0:
        totals_data.append([f"MwSt. {invoice.tax_rate}%", f"{tax_amount:.2f} €"])
    totals_data.append(["Gesamtbetrag", f"{total:.2f} €"])
    totals_table = Table(totals_data, colWidths=[130 * mm, 40 * mm])
    totals_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -2), "Helvetica"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(totals_table)

    if invoice.tax_rate == 0:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph("Gemäß §4 Nr. 21 UStG steuerfreie Leistung.", small))

    if invoice.notes:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph("Hinweise:", bold))
        story.append(Paragraph(invoice.notes, normal))

    doc.build(story)
    buf.seek(0)
    filename = f"Rechnung_{invoice.invoice_number}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/invoices/{invoice_id}/stripe-link")
def invoice_stripe_link(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    _, redirect = require_admin(request, db)
    if redirect:
        return redirect

    invoice = (
        db.query(Invoice)
        .options(joinedload(Invoice.student).joinedload(Student.user))
        .filter(Invoice.id == invoice_id)
        .first()
    )
    if not invoice:
        return RedirectResponse(url="/invoices", status_code=302)

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not stripe_key:
        return RedirectResponse(url=f"/invoices/{invoice_id}?error=no_stripe_key", status_code=302)

    stripe.api_key = stripe_key
    items = json.loads(invoice.items_json or "[]")
    subtotal = sum(i["quantity"] * i["unit_price"] for i in items)
    tax_factor = 1 + invoice.tax_rate / 100
    total_cents = int(round(subtotal * tax_factor * 100))

    school_name = (get_planner_setting_value(db, SCHOOL_NAME) or "Fahrschule").strip()
    student_name = invoice.student.user.name if invoice.student and invoice.student.user else "Schüler"

    base_url = str(request.base_url).rstrip("/")
    session = stripe.checkout.Session.create(
        payment_method_types=["card", "sepa_debit"],
        line_items=[{
            "price_data": {
                "currency": "eur",
                "product_data": {
                    "name": f"Rechnung {invoice.invoice_number} – {school_name}",
                    "description": f"Fahrausbildung {student_name}",
                },
                "unit_amount": total_cents,
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=f"{base_url}/invoices/{invoice_id}?paid=1",
        cancel_url=f"{base_url}/invoices/{invoice_id}",
        customer_email=invoice.student.user.email if invoice.student and invoice.student.user else None,
        metadata={"invoice_id": str(invoice.id), "invoice_number": invoice.invoice_number},
    )
    invoice.stripe_payment_url = session.url
    db.commit()
    return RedirectResponse(url=f"/invoices/{invoice_id}", status_code=302)
