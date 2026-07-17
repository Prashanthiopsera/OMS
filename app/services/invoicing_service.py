"""Invoicing / AR business logic — no FastAPI imports."""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.postgres.b2b_models import CustomerAccount
from app.models.postgres.invoice_models import (
    CreditMemo,
    CreditMemoStatus,
    Invoice,
    InvoicePayment,
    InvoiceStatus,
    PaymentMethod,
)
from app.schemas.invoices import (
    ARAgingBucket,
    ARAgingResponse,
    CreditMemoCreate,
    InvoiceCreate,
    InvoiceStatusUpdate,
    PaymentCreate,
)
from app.services.exceptions import InvoiceNotFoundError, OrderNotFoundError


def generate_invoice_number() -> str:
    month_str = datetime.now(tz=timezone.utc).strftime("%Y%m")
    suffix = secrets.token_hex(3).upper()
    return f"INV-{month_str}-{suffix}"


def generate_memo_number() -> str:
    month_str = datetime.now(tz=timezone.utc).strftime("%Y%m")
    suffix = secrets.token_hex(3).upper()
    return f"CM-{month_str}-{suffix}"


def compute_due_date(payment_terms: str, issued: date) -> date:
    terms_map = {
        "NET_15": 15,
        "NET_30": 30,
        "NET_60": 60,
        "NET_90": 90,
        "NET30": 30,
        "NET60": 60,
        "NET90": 90,
        "COD": 0,
        "UPON_RECEIPT": 0,
        "PREPAID": 0,
    }
    days = terms_map.get(payment_terms.upper(), 0)
    return issued + timedelta(days=days)


class InvoicingService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def load_invoice(self, invoice_id: UUID) -> Invoice:
        result = await self.db.execute(
            select(Invoice)
            .options(
                selectinload(Invoice.customer_account),
                selectinload(Invoice.order),
                selectinload(Invoice.line_items),
                selectinload(Invoice.payments),
            )
            .where(Invoice.id == invoice_id)
        )
        inv = result.scalar_one_or_none()
        if not inv:
            raise InvoiceNotFoundError("Invoice not found")
        return inv

    async def create_invoice_from_order(self, order) -> Invoice:
        existing_result = await self.db.execute(
            select(Invoice).where(Invoice.order_id == order.id)
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            return existing

        if not order.customer_account_id:
            raise OrderNotFoundError("Order has no customer_account_id — cannot create B2B invoice")

        account = await self.db.get(CustomerAccount, order.customer_account_id)
        if not account:
            raise OrderNotFoundError("Customer account not found")

        today = datetime.now(tz=timezone.utc).date()
        payment_terms_snapshot = order.payment_terms or account.payment_terms or "PREPAID"
        due = compute_due_date(payment_terms_snapshot, today)

        inv_number = None
        for _ in range(5):
            candidate = generate_invoice_number()
            clash = await self.db.execute(select(Invoice).where(Invoice.invoice_number == candidate))
            if not clash.scalar_one_or_none():
                inv_number = candidate
                break
        if not inv_number:
            raise InvoiceNotFoundError("Could not generate unique invoice number")

        invoice = Invoice(
            invoice_number=inv_number,
            customer_account_id=order.customer_account_id,
            order_id=order.id,
            status=InvoiceStatus.DRAFT,
            subtotal=Decimal(str(order.subtotal or 0)),
            tax_amount=Decimal(str(order.tax_amount or 0)),
            total_amount=Decimal(str(order.total_amount or 0)),
            currency=order.currency or "USD",
            issued_date=today,
            due_date=due,
            payment_terms=payment_terms_snapshot,
            notes=None,
            metadata_={},
        )
        self.db.add(invoice)
        await self.db.flush()
        await self.db.refresh(invoice)
        return invoice

    async def create_invoice(self, payload: InvoiceCreate) -> Invoice:
        from app.models.postgres.order_models import Order

        account = await self.db.get(CustomerAccount, payload.customer_account_id)
        if not account:
            raise OrderNotFoundError("Customer account not found")

        if payload.order_id:
            order = await self.db.get(Order, payload.order_id)
            if not order:
                raise OrderNotFoundError("Order not found")
            if order.customer_account_id != payload.customer_account_id:
                raise OrderNotFoundError("Order does not belong to the specified customer account")

        today = payload.issued_date or datetime.now(tz=timezone.utc).date()
        terms = payload.payment_terms or account.payment_terms or "PREPAID"
        due = payload.due_date or compute_due_date(terms, today)

        inv_number = None
        for _ in range(5):
            candidate = generate_invoice_number()
            clash = await self.db.execute(select(Invoice).where(Invoice.invoice_number == candidate))
            if not clash.scalar_one_or_none():
                inv_number = candidate
                break
        if not inv_number:
            raise InvoiceNotFoundError("Could not generate unique invoice number")

        invoice = Invoice(
            invoice_number=inv_number,
            customer_account_id=payload.customer_account_id,
            order_id=payload.order_id,
            status=InvoiceStatus.DRAFT,
            subtotal=payload.subtotal,
            tax_amount=payload.tax_amount,
            total_amount=payload.total_amount,
            currency=payload.currency or "USD",
            issued_date=today,
            due_date=due,
            payment_terms=terms,
            notes=payload.notes,
            metadata_=payload.metadata or {},
        )
        self.db.add(invoice)
        await self.db.flush()
        await self.db.refresh(invoice)
        return invoice

    async def update_status(self, invoice_id: UUID, payload: InvoiceStatusUpdate) -> Invoice:
        invoice = await self.load_invoice(invoice_id)
        invoice.status = payload.status
        if payload.notes is not None:
            invoice.notes = payload.notes
        await self.db.flush()
        await self.db.refresh(invoice)
        return invoice

    async def record_payment(self, invoice_id: UUID, payload: PaymentCreate) -> InvoicePayment:
        invoice = await self.load_invoice(invoice_id)
        payment = InvoicePayment(
            invoice_id=invoice_id,
            amount=payload.amount,
            payment_method=payload.payment_method or PaymentMethod.OTHER,
            payment_date=payload.payment_date or datetime.now(tz=timezone.utc).date(),
            reference_number=payload.reference_number,
            notes=payload.notes,
        )
        self.db.add(payment)
        await self.db.flush()

        total_paid = sum(Decimal(str(p.amount or 0)) for p in invoice.payments) + payload.amount
        if total_paid >= Decimal(str(invoice.total_amount or 0)):
            invoice.status = InvoiceStatus.PAID
        elif total_paid > 0:
            invoice.status = InvoiceStatus.PARTIALLY_PAID

        await self.db.flush()
        await self.db.refresh(payment)
        return payment

    async def create_credit_memo(self, payload: CreditMemoCreate) -> CreditMemo:
        account = await self.db.get(CustomerAccount, payload.customer_account_id)
        if not account:
            raise OrderNotFoundError("Customer account not found")

        memo_number = None
        for _ in range(5):
            candidate = generate_memo_number()
            clash = await self.db.execute(
                select(CreditMemo).where(CreditMemo.memo_number == candidate)
            )
            if not clash.scalar_one_or_none():
                memo_number = candidate
                break
        if not memo_number:
            raise InvoiceNotFoundError("Could not generate unique credit memo number")

        memo = CreditMemo(
            memo_number=memo_number,
            customer_account_id=payload.customer_account_id,
            invoice_id=payload.invoice_id,
            status=CreditMemoStatus.ISSUED,
            amount=payload.amount,
            currency=payload.currency or "USD",
            reason=payload.reason,
            notes=payload.notes,
        )
        self.db.add(memo)
        await self.db.flush()
        await self.db.refresh(memo)
        return memo

    async def get_ar_aging(self) -> ARAgingResponse:
        today = datetime.now(tz=timezone.utc).date()
        stmt = select(Invoice).where(
            Invoice.status.notin_([InvoiceStatus.PAID, InvoiceStatus.VOID, InvoiceStatus.DRAFT])
        )
        result = await self.db.execute(stmt)
        invoices = result.scalars().all()

        current_bucket = ARAgingBucket(count=0, total_amount=Decimal("0"))
        bucket_1_30 = ARAgingBucket(count=0, total_amount=Decimal("0"))
        bucket_31_60 = ARAgingBucket(count=0, total_amount=Decimal("0"))
        bucket_61_90 = ARAgingBucket(count=0, total_amount=Decimal("0"))
        bucket_over_90 = ARAgingBucket(count=0, total_amount=Decimal("0"))
        total_outstanding = Decimal("0")

        for inv in invoices:
            amount = Decimal(str(inv.total_amount or 0))
            days_overdue = (today - inv.due_date).days

            if days_overdue <= 0:
                current_bucket.count += 1
                current_bucket.total_amount += amount
            elif days_overdue <= 30:
                bucket_1_30.count += 1
                bucket_1_30.total_amount += amount
            elif days_overdue <= 60:
                bucket_31_60.count += 1
                bucket_31_60.total_amount += amount
            elif days_overdue <= 90:
                bucket_61_90.count += 1
                bucket_61_90.total_amount += amount
            else:
                bucket_over_90.count += 1
                bucket_over_90.total_amount += amount

            total_outstanding += amount

        return ARAgingResponse(
            current=current_bucket,
            days_1_30=bucket_1_30,
            days_31_60=bucket_31_60,
            days_61_90=bucket_61_90,
            over_90=bucket_over_90,
            total_outstanding=total_outstanding,
        )
