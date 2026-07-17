"""Fulfillment Nodes router — DCs and Stores."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID

from app.database.postgres import get_db
from app.models.postgres.node_models import NodeStatus, NodeType
from app.schemas.nodes import NodeCreate, NodeUpdate, NodeResponse, NodeListResponse
from app.services.error_mapping import http_exception_from_domain
from app.services.exceptions import DomainError
from app.services.node_service import NodeService

router = APIRouter(prefix="/nodes", tags=["Fulfillment Nodes"])


@router.post("/", response_model=NodeResponse, status_code=201)
async def create_node(payload: NodeCreate, db: AsyncSession = Depends(get_db)):
    try:
        return await NodeService(db).create_node(payload)
    except DomainError as exc:
        raise http_exception_from_domain(exc) from exc


@router.get("/", response_model=NodeListResponse)
async def list_nodes(
    node_type: Optional[NodeType] = None,
    status: Optional[NodeStatus] = None,
    can_ship: Optional[bool] = None,
    can_pickup: Optional[bool] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    return await NodeService(db).list_nodes(
        node_type=node_type,
        status=status,
        can_ship=can_ship,
        can_pickup=can_pickup,
        page=page,
        page_size=page_size,
    )


@router.get("/{node_id}", response_model=NodeResponse)
async def get_node(node_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        return await NodeService(db).get_node(node_id)
    except DomainError as exc:
        raise http_exception_from_domain(exc) from exc


@router.patch("/{node_id}", response_model=NodeResponse)
async def update_node(node_id: UUID, payload: NodeUpdate, db: AsyncSession = Depends(get_db)):
    try:
        return await NodeService(db).update_node(node_id, payload)
    except DomainError as exc:
        raise http_exception_from_domain(exc) from exc


@router.delete("/{node_id}", status_code=204)
async def deactivate_node(node_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        await NodeService(db).deactivate_node(node_id)
    except DomainError as exc:
        raise http_exception_from_domain(exc) from exc


@router.get("/{node_id}/capacity", response_model=dict)
async def get_node_capacity(node_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        return await NodeService(db).get_capacity(node_id)
    except DomainError as exc:
        raise http_exception_from_domain(exc) from exc
