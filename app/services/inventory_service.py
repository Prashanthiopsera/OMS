"""Inventory business logic — no FastAPI imports."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres.brand_models import Brand, InventoryMode
from app.models.postgres.inventory_models import (
    InventoryAdjustment,
    InventoryAdjustmentReason,
    InventoryItem,
)
from app.models.postgres.node_models import FulfillmentNode, NodeStatus
from app.schemas.inventory import (
    BulkInventoryCheck,
    InventoryAdjustmentCreate,
    InventoryCheckResult,
    InventoryItemCreate,
    InventoryItemUpdate,
    InventoryTransfer,
    ProductSummary,
    ProductUpdate,
)
from app.services.exceptions import (
    BrandAccessDeniedError,
    DuplicateResourceError,
    InsufficientInventoryError,
    InventoryNotFoundError,
    NodeNotFoundError,
)


class InventoryService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def assert_brand_access(item_brand_id: Optional[str], brand_ids: Optional[list[str]]) -> None:
        if brand_ids is None:
            return
        if not brand_ids or (item_brand_id and item_brand_id not in brand_ids):
            raise BrandAccessDeniedError("Access denied")

    async def get_item(self, item_id: UUID) -> InventoryItem:
        item = await self.db.get(InventoryItem, item_id)
        if not item:
            raise InventoryNotFoundError("Inventory item not found")
        return item

    async def create_item(self, payload: InventoryItemCreate) -> InventoryItem:
        node = await self.db.get(FulfillmentNode, payload.node_id)
        if not node:
            raise NodeNotFoundError("Node not found")

        result = await self.db.execute(
            select(InventoryItem).where(
                and_(InventoryItem.node_id == payload.node_id, InventoryItem.sku == payload.sku)
            )
        )
        if result.scalar_one_or_none():
            raise DuplicateResourceError("Inventory item already exists for this node/SKU")

        item = InventoryItem(
            **payload.model_dump(),
            quantity_available=payload.quantity_on_hand,
        )
        self.db.add(item)
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def update_item(
        self,
        item_id: UUID,
        payload: InventoryItemUpdate,
        brand_ids: Optional[list[str]] = None,
    ) -> InventoryItem:
        item = await self.get_item(item_id)
        self.assert_brand_access(str(item.brand_id) if item.brand_id else None, brand_ids)

        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, field, value)
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def adjust_inventory(
        self,
        item_id: UUID,
        payload: InventoryAdjustmentCreate,
        brand_ids: Optional[list[str]] = None,
    ) -> tuple[InventoryAdjustment, InventoryItem]:
        item = await self.get_item(item_id)
        self.assert_brand_access(str(item.brand_id) if item.brand_id else None, brand_ids)

        quantity_before = item.quantity_on_hand
        quantity_after = max(0, quantity_before + payload.quantity_delta)

        item.quantity_on_hand = quantity_after
        item.quantity_available = max(0, quantity_after - item.quantity_reserved)

        adj = InventoryAdjustment(
            inventory_item_id=item_id,
            reason=payload.reason,
            quantity_delta=payload.quantity_delta,
            quantity_before=quantity_before,
            quantity_after=quantity_after,
            reference_id=payload.reference_id,
            notes=payload.notes,
            created_by=payload.created_by,
        )
        self.db.add(adj)
        await self.db.flush()
        await self.db.refresh(adj)
        return adj, item

    async def check_availability(self, payload: BulkInventoryCheck) -> list[InventoryCheckResult]:
        results = []
        for item_req in payload.items:
            sku = item_req.get("sku")
            qty_needed = item_req.get("quantity", 1)

            result = await self.db.execute(
                select(InventoryItem, FulfillmentNode)
                .join(FulfillmentNode, InventoryItem.node_id == FulfillmentNode.id)
                .where(
                    InventoryItem.sku == sku,
                    InventoryItem.is_active.is_(True),
                    InventoryItem.quantity_available > 0,
                    FulfillmentNode.status == NodeStatus.ACTIVE,
                )
                .order_by(InventoryItem.quantity_available.desc())
            )
            rows = result.all()

            available_by_node = [
                {
                    "node_id": str(inv.id),
                    "node_code": node.code,
                    "node_name": node.name,
                    "quantity_available": inv.quantity_available,
                }
                for inv, node in rows
            ]
            total_available = sum(r["quantity_available"] for r in available_by_node)

            results.append(
                InventoryCheckResult(
                    sku=sku,
                    requested_quantity=qty_needed,
                    available_by_node=available_by_node,
                    total_available=total_available,
                    fulfillable=total_available >= qty_needed,
                )
            )
        return results

    async def transfer_inventory(
        self,
        payload: InventoryTransfer,
        brand_ids: Optional[list[str]] = None,
    ) -> dict:
        result = await self.db.execute(
            select(InventoryItem).where(
                InventoryItem.node_id == payload.from_node_id,
                InventoryItem.sku == payload.sku,
            )
        )
        source = result.scalar_one_or_none()
        if not source:
            raise InventoryNotFoundError("Source inventory item not found")

        self.assert_brand_access(str(source.brand_id) if source.brand_id else None, brand_ids)

        if source.quantity_available < payload.quantity:
            raise InsufficientInventoryError(
                f"Insufficient quantity. Available: {source.quantity_available}",
                details={"available": source.quantity_available, "requested": payload.quantity},
            )

        result = await self.db.execute(
            select(InventoryItem).where(
                InventoryItem.node_id == payload.to_node_id,
                InventoryItem.sku == payload.sku,
            )
        )
        dest = result.scalar_one_or_none()
        if not dest:
            dest = InventoryItem(
                node_id=payload.to_node_id,
                sku=payload.sku,
                product_name=source.product_name,
                quantity_on_hand=0,
                quantity_available=0,
            )
            self.db.add(dest)
            await self.db.flush()

        transfer_ref = f"TRANSFER-{payload.from_node_id}-{payload.to_node_id}"

        source_before = source.quantity_on_hand
        source.quantity_on_hand -= payload.quantity
        source.quantity_available = max(0, source.quantity_on_hand - source.quantity_reserved)
        self.db.add(
            InventoryAdjustment(
                inventory_item_id=source.id,
                reason=InventoryAdjustmentReason.TRANSFER_OUT,
                quantity_delta=-payload.quantity,
                quantity_before=source_before,
                quantity_after=source.quantity_on_hand,
                reference_id=transfer_ref,
                notes=payload.notes,
            )
        )

        dest_before = dest.quantity_on_hand
        dest.quantity_on_hand += payload.quantity
        dest.quantity_available = max(0, dest.quantity_on_hand - dest.quantity_reserved)
        self.db.add(
            InventoryAdjustment(
                inventory_item_id=dest.id,
                reason=InventoryAdjustmentReason.TRANSFER_IN,
                quantity_delta=payload.quantity,
                quantity_before=dest_before,
                quantity_after=dest.quantity_on_hand,
                reference_id=transfer_ref,
                notes=payload.notes,
            )
        )

        await self.db.flush()
        return {"message": "Transfer completed", "reference": transfer_ref}

    async def list_items(
        self,
        *,
        node_id: Optional[UUID] = None,
        sku: Optional[str] = None,
        brand_id: Optional[str] = None,
        low_stock_only: bool = False,
        page: int = 1,
        page_size: int = 50,
        accessible_brand_ids: Optional[list[str]] = None,
    ) -> list[InventoryItem]:
        if accessible_brand_ids is not None and not accessible_brand_ids:
            return []

        query = select(InventoryItem).where(InventoryItem.is_active.is_(True))
        if node_id:
            query = query.where(InventoryItem.node_id == node_id)
        if sku:
            query = query.where(InventoryItem.sku == sku)
        if low_stock_only:
            query = query.where(InventoryItem.quantity_available <= InventoryItem.reorder_point)

        if accessible_brand_ids is not None:
            brand_uuids = [UUID(bid) for bid in accessible_brand_ids]
            query = query.where(InventoryItem.brand_id.in_(brand_uuids))

        if brand_id:
            try:
                brand_uuid = UUID(brand_id)
            except ValueError as exc:
                raise ValueError("Invalid brand_id format") from exc
            brand = await self.db.get(Brand, brand_uuid)
            if brand and brand.inventory_mode == InventoryMode.ISOLATED.value:
                query = query.where(InventoryItem.brand_id == brand_uuid)

        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def update_product(
        self,
        sku: str,
        payload: ProductUpdate,
        brand_ids: Optional[list[str]] = None,
    ) -> dict:
        result = await self.db.execute(
            select(InventoryItem).where(InventoryItem.sku == sku, InventoryItem.is_active.is_(True))
        )
        items = list(result.scalars().all())
        if not items:
            raise InventoryNotFoundError(f"No inventory found for SKU: {sku}")

        if brand_ids is not None:
            items = [item for item in items if str(item.brand_id) in brand_ids]

        update_data = payload.model_dump(exclude_unset=True)
        for item in items:
            for field, value in update_data.items():
                setattr(item, field, value)
        await self.db.flush()
        return {"updated": len(items), "sku": sku}

    async def list_products(
        self,
        *,
        search: Optional[str] = None,
        node_id: Optional[UUID] = None,
        low_stock_only: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> list[ProductSummary]:
        query = (
            select(
                InventoryItem.sku,
                func.max(InventoryItem.product_name).label("product_name"),
                func.sum(InventoryItem.quantity_on_hand).label("total_on_hand"),
                func.sum(InventoryItem.quantity_available).label("total_available"),
                func.sum(InventoryItem.quantity_reserved).label("total_reserved"),
                func.count(InventoryItem.id).label("nodes_count"),
                func.max(InventoryItem.unit_cost).label("unit_cost"),
                func.max(InventoryItem.weight_lbs).label("weight_lbs"),
                func.max(InventoryItem.reorder_point).label("reorder_point"),
                func.max(InventoryItem.updated_at).label("updated_at"),
            )
            .where(InventoryItem.is_active.is_(True))
            .group_by(InventoryItem.sku)
            .order_by(InventoryItem.sku)
        )
        if node_id:
            query = query.where(InventoryItem.node_id == node_id)
        if search:
            query = query.where(
                or_(
                    InventoryItem.sku.ilike(f"%{search}%"),
                    InventoryItem.product_name.ilike(f"%{search}%"),
                )
            )
        if low_stock_only:
            query = query.having(
                func.sum(InventoryItem.quantity_available) <= func.max(InventoryItem.reorder_point)
            )
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        rows = result.all()
        return [
            ProductSummary(
                sku=row.sku,
                product_name=row.product_name,
                total_on_hand=row.total_on_hand or 0,
                total_available=row.total_available or 0,
                total_reserved=row.total_reserved or 0,
                nodes_count=row.nodes_count or 0,
                unit_cost=row.unit_cost or 0.0,
                weight_lbs=row.weight_lbs or 0.0,
                reorder_point=row.reorder_point or 0,
                updated_at=row.updated_at,
            )
            for row in rows
        ]
