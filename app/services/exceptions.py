"""Domain exceptions raised by service layer — translated to HTTP responses in routers."""
from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for service-layer domain errors."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class OrderNotFoundError(DomainError):
    """Raised when an order ID does not exist."""


class InvalidStatusTransitionError(DomainError):
    """Raised when a lifecycle forbids the requested status change."""


class CreditLimitExceededError(DomainError):
    """Raised when a B2B order would exceed account credit limit."""


class AccountOnHoldError(DomainError):
    """Raised when a customer account is on hold and cannot place orders."""


class BrandAccessDeniedError(DomainError):
    """Raised when the caller lacks access to the requested brand scope."""


class InsufficientInventoryError(DomainError):
    """Raised when inventory cannot satisfy a reservation or allocation."""


class InventoryNotFoundError(DomainError):
    """Raised when an inventory item or SKU lookup fails."""


class NodeNotFoundError(DomainError):
    """Raised when a fulfillment node ID does not exist."""


class ConnectorNotFoundError(DomainError):
    """Raised when a connector ID does not exist."""


class ReturnNotFoundError(DomainError):
    """Raised when a return/RMA ID does not exist."""


class InvoiceNotFoundError(DomainError):
    """Raised when an invoice ID does not exist."""


class DuplicateResourceError(DomainError):
    """Raised when a unique constraint would be violated."""
