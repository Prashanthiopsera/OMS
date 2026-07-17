"""Map service-layer domain errors to HTTP status codes for routers."""
from __future__ import annotations

from fastapi import HTTPException

from app.services.exceptions import (
    AccountOnHoldError,
    BrandAccessDeniedError,
    ConnectorNotFoundError,
    CreditLimitExceededError,
    DomainError,
    DuplicateResourceError,
    InsufficientInventoryError,
    InvalidStatusTransitionError,
    InventoryNotFoundError,
    InvoiceNotFoundError,
    NodeNotFoundError,
    OrderNotFoundError,
    ReturnNotFoundError,
)


def http_exception_from_domain(err: DomainError) -> HTTPException:
    if isinstance(err, OrderNotFoundError):
        return HTTPException(status_code=404, detail=err.message)
    if isinstance(err, BrandAccessDeniedError):
        return HTTPException(status_code=403, detail=err.message)
    if isinstance(err, InvalidStatusTransitionError):
        status = 400 if err.message.startswith("Cannot cancel") else 422
        return HTTPException(status_code=status, detail=err.message)
    if isinstance(err, (CreditLimitExceededError, AccountOnHoldError)):
        return HTTPException(status_code=422, detail=err.message)
    if isinstance(err, InsufficientInventoryError):
        return HTTPException(status_code=409, detail=err.message)
    if isinstance(err, DuplicateResourceError):
        return HTTPException(status_code=409, detail=err.message)
    if isinstance(err, (InventoryNotFoundError, NodeNotFoundError, ConnectorNotFoundError,
                        ReturnNotFoundError, InvoiceNotFoundError)):
        return HTTPException(status_code=404, detail=err.message)
    return HTTPException(status_code=500, detail=err.message)
