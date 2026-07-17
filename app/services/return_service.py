"""Return/RMA business logic — no FastAPI imports."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.postgres.order_models import FulfillmentAllocation, Order, OrderStatus
from app.models.postgres.return_models import (
    OrderReturn,
    Refund,
    RefundStatus,
    ReturnItem,
    ReturnStatus,
)
from app.schemas.returns import RefundCreate, ReturnCreate, ReturnUpdate
from app.services.exceptions import (
    InvalidStatusTransitionError,
    OrderNotFoundError,
    ReturnNotFoundError,
)


def generate_rma_number() -> str:
    month_str = datetime.now(tz=timezone.utc).strftime("%Y%m")
    suffix = secrets.token_hex(3).upper()
    return f"RMA-{month_str}-{suffix}"


def generate_refund_number() -> str:
    month_str = datetime.now(tz=timezone.utc).strftime("%Y%m")
    suffix = secrets.token_hex(3).upper()
    return f"REF-{month_str}-{suffix}"


class ReturnService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def load_return(self, return_id: UUID) -> OrderReturn:
        result = await self.db.execute(
            select(OrderReturn)
            .options(
                selectinload(OrderReturn.items),
                selectinload(OrderReturn.refund),
            )
            .where(OrderReturn.id == return_id)
        )
        order_return = result.scalar_one_or_none()
        if not order_return:
            raise ReturnNotFoundError("Return not found")
        return order_return

    async def create_return(self, payload: ReturnCreate) -> OrderReturn:
        order = await self.db.get(Order, payload.order_id)
        if not order:
            raise OrderNotFoundError("Order not found")
        if order.status in (OrderStatus.CANCELLED, OrderStatus.PENDING):
            raise InvalidStatusTransitionError(
                f"Returns cannot be created for orders in {order.status.value} status"
            )

        rma_number = None
        for _ in range(5):
            candidate = generate_rma_number()
            clash = await self.db.execute(
                select(OrderReturn).where(OrderReturn.return_number == candidate)
            )
            if not clash.scalar_one_or_none():
                rma_number = candidate
                break
        if not rma_number:
            raise ReturnNotFoundError("Could not generate unique RMA number")

        order_return = OrderReturn(
            return_number=rma_number,
            order_id=payload.order_id,
            status=ReturnStatus.REQUESTED,
            reason=payload.reason,
            customer_notes=payload.customer_notes,
        )
        self.db.add(order_return)
        await self.db.flush()

        for item_payload in payload.items:
            self.db.add(
                ReturnItem(
                    return_id=order_return.id,
                    order_item_id=item_payload.order_item_id,
                    sku=item_payload.sku,
                    description=item_payload.description,
                    quantity_requested=item_payload.quantity_requested,
                    restock=item_payload.restock,
                )
            )

        await self.db.flush()
        return await self.load_return(order_return.id)

    async def update_status(
        self, return_id: UUID, payload: ReturnUpdate
    ) -> tuple[OrderReturn, list[tuple[str, int]]]:
        order_return = await self.load_return(return_id)

        order_return.status = payload.status
        if payload.staff_notes is not None:
            order_return.staff_notes = payload.staff_notes
        if payload.return_tracking_number is not None:
            order_return.return_tracking_number = payload.return_tracking_number
        if payload.return_carrier is not None:
            order_return.return_carrier = payload.return_carrier

        now = datetime.now(tz=timezone.utc)
        restocked: list[tuple[str, int]] = []

        if payload.status == ReturnStatus.RECEIVED and not order_return.received_at:
            order_return.received_at = now

        if payload.status == ReturnStatus.RESTOCKED:
            if order_return.restocked_at is not None:
                raise InvalidStatusTransitionError("Return has already been restocked")
            order_return.restocked_at = now
            restocked = await self._restock_return_items(order_return)

        await self.db.flush()
        return await self.load_return(return_id), restocked

    async def create_refund(self, return_id: UUID, payload: RefundCreate) -> Refund:
        order_return = await self.load_return(return_id)

        if order_return.refund:
            raise InvalidStatusTransitionError(
                "A refund already exists for this return. Use the existing refund record."
            )

        order = await self.db.get(Order, order_return.order_id)
        if order and order.total_amount is not None:
            existing_sum_row = await self.db.execute(
                select(func.coalesce(func.sum(Refund.amount), 0))
                .where(Refund.order_id == order_return.order_id)
                .where(Refund.status != RefundStatus.FAILED)
            )
            existing_total = Decimal(str(existing_sum_row.scalar_one()))
            if existing_total + payload.amount > Decimal(str(order.total_amount)):
                raise InvalidStatusTransitionError(
                    f"Refund would exceed order total. Already refunded: {existing_total}, "
                    f"order total: {order.total_amount}."
                )

        refund_number = None
        for _ in range(5):
            candidate = generate_refund_number()
            clash = await self.db.execute(select(Refund).where(Refund.refund_number == candidate))
            if not clash.scalar_one_or_none():
                refund_number = candidate
                break
        if not refund_number:
            raise ReturnNotFoundError("Could not generate unique refund number")

        refund = Refund(
            refund_number=refund_number,
            order_id=order_return.order_id,
            return_id=return_id,
            status=RefundStatus.PENDING,
            refund_method=payload.refund_method,
            amount=payload.amount,
            currency=payload.currency,
            transaction_id=payload.transaction_id,
            reason=payload.reason,
            notes=payload.notes,
        )
        self.db.add(refund)
        await self.db.flush()
        await self.db.refresh(refund)
        return refund

    async def _restock_return_items(self, order_return: OrderReturn) -> list[tuple[str, int]]:
        from app.models.postgres.inventory_models import (
            InventoryAdjustment,
            InventoryAdjustmentReason,
            InventoryItem,
        )

        alloc_result = await self.db.execute(
            select(FulfillmentAllocation.node_id)
            .where(FulfillmentAllocation.order_id == order_return.order_id)
            .limit(1)
        )
        node_id_row = alloc_result.first()
        node_id = node_id_row[0] if node_id_row else None

        restocked: list[tuple[str, int]] = []

        for item in order_return.items or []:
            if not item.restock:
                continue
            qty = float(item.quantity_received or item.quantity_requested)
            if qty <= 0:
                continue

            inv_stmt = select(InventoryItem).where(InventoryItem.sku == item.sku)
            if node_id:
                inv_stmt = inv_stmt.where(InventoryItem.node_id == node_id)
            inv_result = await self.db.execute(inv_stmt.limit(1))
            inv_item = inv_result.scalar_one_or_none()

            if not inv_item:
                continue

            before = inv_item.quantity_on_hand
            delta = int(round(qty))
            inv_item.quantity_on_hand = before + delta
            inv_item.quantity_available = inv_item.quantity_on_hand - (inv_item.quantity_reserved or 0)

            self.db.add(
                InventoryAdjustment(
                    inventory_item_id=inv_item.id,
                    reason=InventoryAdjustmentReason.RETURNED,
                    quantity_delta=delta,
                    quantity_before=before,
                    quantity_after=inv_item.quantity_on_hand,
                    notes=f"Restocked via RMA {order_return.return_number}",
                )
            )
            restocked.append((str(inv_item.id), inv_item.quantity_available))

        return restocked

    async def list_returns(
        self,
        *,
        status: Optional[ReturnStatus] = None,
        order_id: Optional[UUID] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[OrderReturn], int]:
        stmt = select(OrderReturn)
        if status:
            stmt = stmt.where(OrderReturn.status == status)
        if order_id:
            stmt = stmt.where(OrderReturn.order_id == order_id)
        if from_date:
            stmt = stmt.where(OrderReturn.created_at >= from_date)
        if to_date:
            stmt = stmt.where(OrderReturn.created_at <= to_date)

        total = (await self.db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()

        stmt = (
            stmt.options(selectinload(OrderReturn.items), selectinload(OrderReturn.refund))
            .order_by(OrderReturn.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        returns = list((await self.db.execute(stmt)).scalars().all())
        return returns, total
