"""Shared PostgreSQL domain enums — single source of truth (WO-087)."""
import enum


class PaymentTerms(str, enum.Enum):
    PREPAID = "PREPAID"
    NET_15 = "NET_15"
    NET_30 = "NET_30"
    NET_60 = "NET_60"
    NET_90 = "NET_90"
    COD = "COD"
    UPON_RECEIPT = "UPON_RECEIPT"
