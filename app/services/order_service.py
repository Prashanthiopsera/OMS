"""Order business logic — no FastAPI imports."""
from __future__ import annotations

import logging
import random
import string
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.postgres.order_models import (
    FulfillmentAllocation,
    Order,
    OrderItem,
    OrderStatus,
)
from app.schemas.orders import CancelOrderRequest, OrderCreate, OrderStatusUpdate
from app.services.exceptions import (
    AccountOnHoldError,
    BrandAccessDeniedError,
    CreditLimitExceededError,
    InvalidStatusTransitionError,
    OrderNotFoundError,
)
from app.services.lifecycle_engine import resolve_lifecycle, validate_transition

logger = logging.getLogger(__name__)

try:
    from app.models.postgres.b2b_models import AccountType, ApprovalStatus, CustomerAccount

    _B2B_MODELS_AVAILABLE = True
except ImportError:
    _B2B_MODELS_AVAILABLE = False


def generate_order_number() -> str:
    prefix = "ORD"
    ts = datetime.utcnow().strftime("%Y%m%d")
    rand = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}-{ts}-{rand}"


def calculate_order_totals(payload: OrderCreate) -> tuple[Decimal, Decimal, Decimal]:
    subtotal = sum(
        (item.unit_price * item.quantity) - item.discount_amount
        for item in payload.line_items
    )
    tax_amount = sum(item.tax_amount * item.quantity for item in payload.line_items)
    total = subtotal + tax_amount + payload.shipping_amount - payload.discount_amount
    return subtotal, tax_amount, total


class OrderService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def assert_brand_access(order_brand_id: Optional[str], brand_ids: Optional[list[str]]) -> None:
        if brand_ids is None:
            return
        if not brand_ids:
            raise BrandAccessDeniedError("You have no brand access in this environment")
        if order_brand_id and order_brand_id not in brand_ids:
            raise BrandAccessDeniedError(
                f"You do not have access to brand {order_brand_id}",
                details={"brand_id": order_brand_id},
            )

    async def get_order(self, order_id: UUID, *, for_update: bool = False) -> Order:
        query = (
            select(Order)
            .options(
                selectinload(Order.line_items),
                selectinload(Order.fulfillment_allocations).selectinload(FulfillmentAllocation.node),
                selectinload(Order.shipments),
            )
            .where(Order.id == order_id)
        )
        if for_update:
            query = query.with_for_update()
        result = await self.db.execute(query)
        order = result.scalar_one_or_none()
        if not order:
            raise OrderNotFoundError(f"Order {order_id} not found", details={"order_id": str(order_id)})
        return order

    async def create_order(
        self,
        payload: OrderCreate,
        brand_ids: Optional[list[str]] = None,
    ) -> Order:
        order_brand_id = str(payload.brand_id) if getattr(payload, "brand_id", None) else None
        self.assert_brand_access(order_brand_id, brand_ids)

        subtotal, tax_amount, total = calculate_order_totals(payload)
        order_total_float = float(total)

        _ot = payload.order_type.value if hasattr(payload, "order_type") and payload.order_type else None
        _bid = str(payload.brand_id) if hasattr(payload, "brand_id") and payload.brand_id else None
        lc, _ = await resolve_lifecycle(
            self.db,
            payload.fulfillment_type.value,
            payload.channel.value,
            pipeline_type="ORDER",
            order_type=_ot,
            brand_id=_bid,
        )

        approval_status: Optional[str] = None
        b2b_account = None
        customer_account_id = getattr(payload, "customer_account_id", None)

        if customer_account_id and _B2B_MODELS_AVAILABLE:
            acct_result = await self.db.execute(
                select(CustomerAccount)
                .where(CustomerAccount.id == customer_account_id)
                .with_for_update()
            )
            account = acct_result.scalar_one_or_none()
            if not account:
                raise OrderNotFoundError("Customer account not found")

            if account.account_type == AccountType.ON_HOLD:
                raise AccountOnHoldError(
                    f"Account {account.account_number} is ON_HOLD — new orders are blocked",
                    details={"account_number": account.account_number},
                )

            raw_approval_status = getattr(payload, "approval_status", None)
            if raw_approval_status is not None:
                approval_status = (
                    raw_approval_status.value
                    if hasattr(raw_approval_status, "value")
                    else str(raw_approval_status)
                )
            else:
                if (
                    account.credit_limit is not None
                    and float(account.credit_used or 0) + order_total_float > float(account.credit_limit)
                ):
                    raise CreditLimitExceededError(
                        (
                            f"Credit limit exceeded for account {account.account_number}: "
                            f"limit={account.credit_limit}, used={account.credit_used}, "
                            f"order={order_total_float}"
                        ),
                        details={
                            "account_number": account.account_number,
                            "credit_limit": float(account.credit_limit),
                            "credit_used": float(account.credit_used or 0),
                            "order_total": order_total_float,
                        },
                    )
                approval_status = ApprovalStatus.PENDING.value if hasattr(ApprovalStatus, "PENDING") else None

            b2b_account = account

        pending_val = (
            ApprovalStatus.PENDING.value
            if _B2B_MODELS_AVAILABLE and hasattr(ApprovalStatus, "PENDING")
            else "__never__"
        )
        initial_order_status = (
            OrderStatus.PENDING if approval_status == pending_val else OrderStatus.CONFIRMED
        )

        order = Order(
            order_number=generate_order_number(),
            channel=payload.channel,
            fulfillment_type=payload.fulfillment_type,
            status=initial_order_status,
            customer_email=str(payload.customer_email),
            customer_phone=payload.customer_phone,
            customer_name=payload.customer_name,
            customer_id=payload.customer_id,
            subtotal=subtotal,
            tax_amount=tax_amount,
            shipping_amount=payload.shipping_amount,
            discount_amount=payload.discount_amount,
            total_amount=total,
            currency=payload.currency,
            pickup_node_id=payload.pickup_node_id,
            lifecycle_id=lc.id if lc else None,
            external_order_id=payload.external_order_id,
            tags=payload.tags,
            notes=payload.notes,
            metadata_=payload.metadata,
        )

        if customer_account_id and _B2B_MODELS_AVAILABLE:
            if hasattr(order, "customer_account_id"):
                order.customer_account_id = customer_account_id
            if hasattr(order, "approval_status") and approval_status is not None:
                order.approval_status = approval_status

        if payload.shipping_address:
            addr = payload.shipping_address
            order.shipping_name = addr.name
            order.shipping_address1 = addr.address1
            order.shipping_address2 = addr.address2
            order.shipping_city = addr.city
            order.shipping_state = addr.state
            order.shipping_postal_code = addr.postal_code
            order.shipping_country = addr.country
            order.shipping_latitude = addr.latitude
            order.shipping_longitude = addr.longitude

            if addr.latitude is None or addr.longitude is None:
                try:
                    from app.services.geocoding import geocode_address

                    coords = await geocode_address(
                        postal_code=addr.postal_code or "",
                        city=addr.city or "",
                        state=addr.state or "",
                        country=addr.country or "US",
                    )
                    if coords:
                        order.shipping_latitude, order.shipping_longitude = coords
                except Exception:
                    pass

        self.db.add(order)
        await self.db.flush()

        for item_data in payload.line_items:
            item_total = (
                (item_data.unit_price * item_data.quantity)
                - item_data.discount_amount
                + (item_data.tax_amount * item_data.quantity)
            )
            self.db.add(
                OrderItem(
                    order_id=order.id,
                    sku=item_data.sku,
                    product_name=item_data.product_name,
                    quantity=item_data.quantity,
                    unit_price=item_data.unit_price,
                    discount_amount=item_data.discount_amount,
                    tax_amount=item_data.tax_amount,
                    total_price=item_total,
                    weight_lbs=item_data.weight_lbs,
                    metadata_=item_data.metadata,
                )
            )

        await self.db.flush()

        if b2b_account is not None and approval_status != pending_val:
            b2b_account.credit_used = Decimal(
                str(float(b2b_account.credit_used or 0) + order_total_float)
            )
            await self.db.flush()

        return await self.get_order(order.id)

    async def update_status(
        self,
        order_id: UUID,
        payload: OrderStatusUpdate,
        brand_ids: Optional[list[str]] = None,
    ) -> tuple[Order, OrderStatus]:
        order = await self.get_order(order_id)

        if brand_ids is not None:
            if not brand_ids or str(order.brand_id) not in brand_ids:
                raise BrandAccessDeniedError("Access denied")

        old_status = order.status
        allowed, reason = await validate_transition(self.db, order, payload.status.value)
        if not allowed:
            raise InvalidStatusTransitionError(reason)

        order.status = payload.status
        if payload.notes:
            order.notes = payload.notes

        now = datetime.utcnow()
        if payload.status == OrderStatus.CONFIRMED and not order.confirmed_at:
            order.confirmed_at = now
        elif payload.status in (OrderStatus.DELIVERED, OrderStatus.PICKED_UP):
            order.delivered_at = now
        elif payload.status == OrderStatus.CANCELLED:
            order.cancelled_at = now

        await self.db.flush()
        await self.db.refresh(order)
        return order, old_status

    async def cancel_order(
        self,
        order_id: UUID,
        payload: CancelOrderRequest,
        brand_ids: Optional[list[str]] = None,
    ) -> Order:
        order = await self.get_order(order_id)

        if brand_ids is not None:
            if not brand_ids or str(order.brand_id) not in brand_ids:
                raise BrandAccessDeniedError("Access denied")

        if order.status in (OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.CANCELLED):
            raise InvalidStatusTransitionError(
                f"Cannot cancel order in status: {order.status.value}",
                details={"status": order.status.value},
            )

        order.status = OrderStatus.CANCELLED
        order.cancelled_at = datetime.utcnow()
        order.notes = f"Cancelled: {payload.reason}"

        if (
            _B2B_MODELS_AVAILABLE
            and hasattr(order, "customer_account_id")
            and order.customer_account_id is not None
            and hasattr(order, "approval_status")
            and order.approval_status == ApprovalStatus.APPROVED.value
        ):
            acct_result = await self.db.execute(
                select(CustomerAccount)
                .where(CustomerAccount.id == order.customer_account_id)
                .with_for_update()
            )
            cancel_account = acct_result.scalar_one_or_none()
            if cancel_account is not None:
                released = float(order.total_amount or 0)
                new_used = max(0.0, float(cancel_account.credit_used or 0) - released)
                cancel_account.credit_used = Decimal(str(new_used))

        await self.db.flush()
        await self.db.refresh(order)
        return order
