"""Inventory router — real-time stock management."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from typing import Optional, List
from uuid import UUID

from app.database.postgres import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.brand import get_accessible_brand_ids
from app.models.postgres.brand_models import Brand, InventoryMode
from app.models.postgres.inventory_models import (
    InventoryItem, InventoryAdjustment, InventoryReservation
)
from app.models.postgres.node_models import FulfillmentNode
from app.schemas.inventory import (
    InventoryItemCreate, InventoryItemUpdate, InventoryItemResponse,
    InventoryAdjustmentCreate, InventoryAdjustmentResponse,
    BulkInventoryCheck, InventoryCheckResult, InventoryTransfer,
    ProductSummary, ProductUpdate,
)
from app.services.error_mapping import http_exception_from_domain
from app.services.exceptions import DomainError
from app.services.inventory_service import InventoryService

router = APIRouter(prefix="/inventory", tags=["Inventory"], dependencies=[Depends(get_current_user)])


@router.post("/", response_model=InventoryItemResponse, status_code=201)
async def create_inventory_item(payload: InventoryItemCreate, db: AsyncSession = Depends(get_db)):
    service = InventoryService(db)
    try:
        return await service.create_item(payload)
    except DomainError as exc:
        raise http_exception_from_domain(exc) from exc


@router.get("/", response_model=List[InventoryItemResponse])
async def list_inventory(
    request: Request,
    node_id: Optional[UUID] = None,
    sku: Optional[str] = None,
    brand_id: Optional[str] = Query(default=None),
    low_stock_only: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    accessible_brand_ids: Optional[List[str]] = Depends(get_accessible_brand_ids),
):
    """List inventory items.

    Brand-scope rules (applied in this order):
    1. Brand-scoped users (non-superadmin with UserBrandRole assignments) only see
       inventory belonging to their accessible brands. An empty assignment set returns
       no results.
    2. When brand_id query param is provided for a brand using ISOLATED inventory mode,
       results are additionally filtered to stock owned by that brand.
    3. SHARED mode brands (or when brand_id is omitted) apply no extra brand filter
       beyond the scope restriction from rule 1.
    """
    service = InventoryService(db)
    try:
        return await service.list_items(
            node_id=node_id,
            sku=sku,
            brand_id=brand_id,
            low_stock_only=low_stock_only,
            page=page,
            page_size=page_size,
            accessible_brand_ids=accessible_brand_ids,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid brand_id format")


@router.get("/sku/{sku}", response_model=List[InventoryItemResponse])
async def get_inventory_by_sku(sku: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(InventoryItem).where(InventoryItem.sku == sku, InventoryItem.is_active == True)
    )
    items = result.scalars().all()
    if not items:
        raise HTTPException(status_code=404, detail=f"No inventory found for SKU: {sku}")
    return items


@router.get("/products", response_model=List[ProductSummary])
async def list_products(
    search: Optional[str] = None,
    node_id: Optional[UUID] = None,
    low_stock_only: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Return all distinct SKUs with aggregated stock totals across all nodes."""
    service = InventoryService(db)
    return await service.list_products(
        search=search,
        node_id=node_id,
        low_stock_only=low_stock_only,
        page=page,
        page_size=page_size,
    )


@router.patch("/products/{sku}", response_model=dict)
async def update_product(
    sku: str,
    payload: ProductUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    brand_ids: Optional[List[str]] = Depends(get_accessible_brand_ids),
):
    """Update product-level attributes for all inventory items with this SKU."""
    service = InventoryService(db)
    try:
        return await service.update_product(sku, payload, brand_ids=brand_ids)
    except DomainError as exc:
        raise http_exception_from_domain(exc) from exc


@router.get("/{item_id}", response_model=InventoryItemResponse)
async def get_inventory_item(
    item_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    brand_ids: Optional[List[str]] = Depends(get_accessible_brand_ids),
):
    service = InventoryService(db)
    try:
        item = await service.get_item(item_id)
        service.assert_brand_access(str(item.brand_id) if item.brand_id else None, brand_ids)
        return item
    except DomainError as exc:
        raise http_exception_from_domain(exc) from exc


@router.patch("/{item_id}", response_model=InventoryItemResponse)
async def update_inventory_item(
    item_id: UUID,
    payload: InventoryItemUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    brand_ids: Optional[List[str]] = Depends(get_accessible_brand_ids),
):
    service = InventoryService(db)
    try:
        return await service.update_item(item_id, payload, brand_ids=brand_ids)
    except DomainError as exc:
        raise http_exception_from_domain(exc) from exc


@router.post("/{item_id}/adjust", response_model=InventoryAdjustmentResponse)
async def adjust_inventory(
    item_id: UUID,
    payload: InventoryAdjustmentCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    brand_ids: Optional[List[str]] = Depends(get_accessible_brand_ids),
):
    service = InventoryService(db)
    try:
        adj, item = await service.adjust_inventory(item_id, payload, brand_ids=brand_ids)
    except DomainError as exc:
        raise http_exception_from_domain(exc) from exc

    background_tasks.add_task(
        _trigger_inventory_sync, str(item_id), int(item.quantity_available)
    )
    return adj


async def _trigger_inventory_sync(inventory_item_id: str, quantity_available: int) -> None:
    """Enqueue outbound inventory push to all connected platforms."""
    try:
        from app.workers.celery_app import celery_app
        celery_app.send_task(
            "app.workers.inventory_sync.push_inventory_to_connectors",
            args=[inventory_item_id, quantity_available],
            queue="connectors",
        )
    except Exception:
        pass


@router.post("/check-availability", response_model=List[InventoryCheckResult])
async def check_availability(payload: BulkInventoryCheck, db: AsyncSession = Depends(get_db)):
    return await InventoryService(db).check_availability(payload)


@router.post("/transfer", response_model=dict)
async def transfer_inventory(
    payload: InventoryTransfer,
    request: Request,
    db: AsyncSession = Depends(get_db),
    brand_ids: Optional[List[str]] = Depends(get_accessible_brand_ids),
):
    """Transfer inventory between nodes."""
    service = InventoryService(db)
    try:
        return await service.transfer_inventory(payload, brand_ids=brand_ids)
    except DomainError as exc:
        raise http_exception_from_domain(exc) from exc
