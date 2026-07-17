"""Unit tests for domain exception classes (WO-037)."""
from app.services.exceptions import (
    AccountOnHoldError,
    BrandAccessDeniedError,
    CreditLimitExceededError,
    DomainError,
    InsufficientInventoryError,
    InvalidStatusTransitionError,
    OrderNotFoundError,
)


def test_domain_error_stores_message_and_details():
    err = DomainError("something failed", details={"order_id": "abc"})
    assert err.message == "something failed"
    assert err.details == {"order_id": "abc"}
    assert str(err) == "something failed"


def test_order_not_found_error():
    err = OrderNotFoundError("Order missing", details={"id": "1"})
    assert isinstance(err, DomainError)
    assert err.message == "Order missing"


def test_invalid_status_transition_error():
    err = InvalidStatusTransitionError("CONFIRMED -> DELIVERED not allowed")
    assert err.message.startswith("CONFIRMED")


def test_credit_limit_exceeded_error():
    assert CreditLimitExceededError("limit hit").details == {}


def test_account_on_hold_error():
    assert AccountOnHoldError("on hold").message == "on hold"


def test_brand_access_denied_error():
    assert BrandAccessDeniedError("denied", details={"brand_id": "x"}).details["brand_id"] == "x"


def test_insufficient_inventory_error():
    assert InsufficientInventoryError("no stock").message == "no stock"
