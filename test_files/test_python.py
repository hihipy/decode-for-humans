"""
test_python.py

Holistic Python showcase — e-commerce order management system.

Covers: type hints, dataclasses, enums, ABC, generics, decorators,
context managers, generators, comprehensions, pattern matching,
exception hierarchies, protocols, slots, properties, classmethods,
staticmethods, operator overloading, iterators, async I/O, threading,
logging, pathlib, argparse, json, csv, re, functools, itertools.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import functools
import itertools
import json
import logging
import re
import threading
import time
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from enum import Enum, Flag, auto
from pathlib import Path
from typing import (
    Any, ClassVar, Final, Generator, Generic,
    Iterable, Iterator, Protocol, TypeVar, runtime_checkable,
)

logger = logging.getLogger(__name__)

VERSION: Final[str] = "2.0.0"
MAX_RETRIES: Final[int] = 3
TAX_RATE: Final[float] = 0.08
FREE_SHIPPING_THRESHOLD: Final[float] = 75.00
SHIPPING_COST: Final[float] = 9.99
LOW_STOCK_THRESHOLD: Final[int] = 5

T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")


class OrderStatus(Enum):
    DRAFT     = "draft"
    CONFIRMED = "confirmed"
    PAID      = "paid"
    SHIPPED   = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED  = "refunded"

    @property
    def is_terminal(self) -> bool:
        return self in {self.DELIVERED, self.CANCELLED, self.REFUNDED}

    def can_transition_to(self, target: OrderStatus) -> bool:
        allowed: dict[OrderStatus, set[OrderStatus]] = {
            self.DRAFT:     {self.CONFIRMED, self.CANCELLED},
            self.CONFIRMED: {self.PAID, self.CANCELLED},
            self.PAID:      {self.SHIPPED, self.REFUNDED},
            self.SHIPPED:   {self.DELIVERED},
            self.DELIVERED: set(),
            self.CANCELLED: set(),
            self.REFUNDED:  set(),
        }
        return target in allowed[self]


class CustomerTier(Enum):
    STANDARD = 0
    SILVER   = 500
    GOLD     = 2000
    PLATINUM = 10000

    @classmethod
    def for_spend(cls, total_spend: float) -> CustomerTier:
        return max(
            (tier for tier in cls if total_spend >= tier.value),
            key=lambda t: t.value,
        )

    @property
    def discount_pct(self) -> float:
        return {
            self.STANDARD: 0.00,
            self.SILVER:   0.05,
            self.GOLD:     0.10,
            self.PLATINUM: 0.15,
        }[self]


class Permission(Flag):
    READ   = auto()
    WRITE  = auto()
    REFUND = auto()
    ADMIN  = auto()
    ALL    = READ | WRITE | REFUND | ADMIN


class AppError(Exception):
    def __init__(self, message: str, code: str = "ERR_GENERIC") -> None:
        super().__init__(message)
        self.code = code


class InsufficientStockError(AppError):
    def __init__(self, sku: str, requested: int, available: int) -> None:
        super().__init__(
            f"SKU {sku}: requested {requested}, only {available} in stock.",
            code="ERR_STOCK",
        )
        self.sku = sku
        self.requested = requested
        self.available = available


class InvalidTransitionError(AppError):
    def __init__(self, from_status: OrderStatus, to_status: OrderStatus) -> None:
        super().__init__(
            f"Cannot transition {from_status.value} -> {to_status.value}.",
            code="ERR_TRANSITION",
        )


class PaymentError(AppError):
    pass


@runtime_checkable
class Exportable(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


class Registry(Generic[K, V]):
    def __init__(self) -> None:
        self._store: dict[K, V] = {}
        self._lock = threading.RLock()

    def register(self, key: K, value: V) -> None:
        with self._lock:
            self._store[key] = value

    def get(self, key: K) -> V | None:
        with self._lock:
            return self._store.get(key)

    def all(self) -> list[V]:
        with self._lock:
            return list(self._store.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._store


def retry(
    max_attempts: int = MAX_RETRIES,
    delay: float = 1.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_attempts:
                        time.sleep(delay * (2 ** (attempt - 1)))
            raise last_exc
        return wrapper
    return decorator


@dataclass(frozen=True, slots=True)
class Address:
    line1:    str
    city:     str
    country:  str
    postcode: str
    line2:    str = ""

    def __str__(self) -> str:
        parts = [self.line1]
        if self.line2:
            parts.append(self.line2)
        parts += [self.city, self.postcode, self.country]
        return ", ".join(parts)


@dataclass
class Product:
    _registry: ClassVar[Registry[str, "Product"]] = Registry()

    sku:         str
    name:        str
    price:       float
    stock:       int
    category:    str
    tags:        list[str] = field(default_factory=list)
    description: str = ""
    created_at:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.price < 0:
            raise ValueError(f"Price cannot be negative: {self.price}")
        Product._registry.register(self.sku, self)

    @classmethod
    def find(cls, sku: str) -> "Product | None":
        return cls._registry.get(sku)

    @classmethod
    def all_products(cls) -> list["Product"]:
        return cls._registry.all()

    @staticmethod
    def validate_sku(sku: str) -> bool:
        return bool(re.fullmatch(r"[A-Z]{2,5}-\d{4}", sku))

    @property
    def is_available(self) -> bool:
        return self.stock > 0

    @property
    def is_low_stock(self) -> bool:
        return 0 < self.stock <= LOW_STOCK_THRESHOLD

    def reserve(self, quantity: int) -> None:
        if quantity > self.stock:
            raise InsufficientStockError(self.sku, quantity, self.stock)
        self.stock -= quantity

    def restock(self, quantity: int) -> None:
        if quantity <= 0:
            raise ValueError("Restock quantity must be positive.")
        self.stock += quantity

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"is_available": self.is_available}

    def __repr__(self) -> str:
        return f"Product(sku={self.sku!r}, price={self.price:.2f})"


@dataclass
class OrderLine:
    product:    Product
    quantity:   int
    unit_price: float = field(init=False)

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("Quantity must be at least 1.")
        self.unit_price = self.product.price

    @property
    def subtotal(self) -> float:
        return round(self.quantity * self.unit_price, 2)


@dataclass
class Customer:
    customer_id:    str
    email:          str
    first_name:     str
    last_name:      str
    address:        Address
    lifetime_spend: float = 0.00
    created_at:     date  = field(default_factory=date.today)
    _order_ids:     list[str] = field(default_factory=list, repr=False)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def tier(self) -> CustomerTier:
        return CustomerTier.for_spend(self.lifetime_spend)

    @property
    def discount(self) -> float:
        return self.tier.discount_pct

    def record_order(self, order_id: str, amount: float) -> None:
        self._order_ids.append(order_id)
        self.lifetime_spend = round(self.lifetime_spend + amount, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "customer_id":    self.customer_id,
            "email":          self.email,
            "full_name":      self.full_name,
            "tier":           self.tier.name,
            "lifetime_spend": self.lifetime_spend,
        }


class PaymentProcessor(ABC):
    @abstractmethod
    def charge(self, amount: float, reference: str) -> str: ...

    @abstractmethod
    def refund(self, transaction_id: str, amount: float) -> bool: ...


class StripeProcessor(PaymentProcessor):
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @retry(max_attempts=3, delay=0.5, exceptions=(PaymentError,))
    def charge(self, amount: float, reference: str) -> str:
        logger.info("Stripe charge: %.2f ref=%s", amount, reference)
        return f"stripe_txn_{reference}_{int(time.time())}"

    def refund(self, transaction_id: str, amount: float) -> bool:
        logger.info("Stripe refund: %s %.2f", transaction_id, amount)
        return True


class MockProcessor(PaymentProcessor):
    def __init__(self) -> None:
        self._charges: list[tuple[float, str, str]] = []

    def charge(self, amount: float, reference: str) -> str:
        txn = f"mock_{reference}"
        self._charges.append((amount, reference, txn))
        return txn

    def refund(self, transaction_id: str, amount: float) -> bool:
        return True

    @property
    def total_charged(self) -> float:
        return sum(a for a, _, _ in self._charges)


class Order:
    def __init__(self, order_id: str, customer: Customer) -> None:
        self.order_id   = order_id
        self.customer   = customer
        self.lines:     list[OrderLine] = []
        self.status     = OrderStatus.DRAFT
        self.placed_at: datetime | None = None
        self._txn_id:   str | None = None
        self.notes:     list[str] = []

    def add_line(self, product: Product, quantity: int) -> OrderLine:
        for line in self.lines:
            if line.product.sku == product.sku:
                line.quantity += quantity
                return line
        line = OrderLine(product=product, quantity=quantity)
        self.lines.append(line)
        return line

    def remove_line(self, sku: str) -> bool:
        before = len(self.lines)
        self.lines = [l for l in self.lines if l.product.sku != sku]
        return len(self.lines) < before

    @property
    def subtotal(self) -> float:
        return round(sum(l.subtotal for l in self.lines), 2)

    @property
    def discount_amount(self) -> float:
        return round(self.subtotal * self.customer.discount, 2)

    @property
    def discounted_subtotal(self) -> float:
        return round(self.subtotal - self.discount_amount, 2)

    @property
    def shipping_cost(self) -> float:
        return 0.0 if self.discounted_subtotal >= FREE_SHIPPING_THRESHOLD else SHIPPING_COST

    @property
    def tax(self) -> float:
        return round((self.discounted_subtotal + self.shipping_cost) * TAX_RATE, 2)

    @property
    def total(self) -> float:
        return round(self.discounted_subtotal + self.shipping_cost + self.tax, 2)

    def _transition(self, target: OrderStatus) -> None:
        if not self.status.can_transition_to(target):
            raise InvalidTransitionError(self.status, target)
        self.status = target

    def confirm(self) -> None:
        self._transition(OrderStatus.CONFIRMED)
        for line in self.lines:
            line.product.reserve(line.quantity)
        self.placed_at = datetime.now(timezone.utc)

    def pay(self, processor: PaymentProcessor) -> str:
        self._transition(OrderStatus.PAID)
        self._txn_id = processor.charge(self.total, self.order_id)
        self.customer.record_order(self.order_id, self.total)
        return self._txn_id

    def ship(self) -> None:
        self._transition(OrderStatus.SHIPPED)

    def deliver(self) -> None:
        self._transition(OrderStatus.DELIVERED)

    def cancel(self) -> None:
        self._transition(OrderStatus.CANCELLED)
        for line in self.lines:
            line.product.restock(line.quantity)

    def refund(self, processor: PaymentProcessor) -> bool:
        if not self._txn_id:
            raise PaymentError("No transaction on record.")
        self._transition(OrderStatus.REFUNDED)
        return processor.refund(self._txn_id, self.total)

    def receipt_lines(self) -> Iterator[str]:
        yield f"{'ORDER RECEIPT':^50}"
        yield "─" * 50
        yield f"Order    : {self.order_id}"
        yield f"Customer : {self.customer.full_name} [{self.customer.tier.name}]"
        yield f"Status   : {self.status.value.upper()}"
        yield "─" * 50
        for line in self.lines:
            yield f"  {line.product.name:<28} x{line.quantity:>2}  £{line.subtotal:>7.2f}"
        yield "─" * 50
        yield f"  {'Subtotal':<36} £{self.subtotal:>7.2f}"
        if self.discount_amount:
            yield f"  {'Discount':<36} -£{self.discount_amount:>6.2f}"
        yield f"  {'Shipping':<36} £{self.shipping_cost:>7.2f}"
        yield f"  {'Tax (8%)':<36} £{self.tax:>7.2f}"
        yield "═" * 50
        yield f"  {'TOTAL':<36} £{self.total:>7.2f}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "customer": self.customer.to_dict(),
            "status":   self.status.value,
            "total":    self.total,
            "lines":    [{"sku": l.product.sku, "qty": l.quantity} for l in self.lines],
        }


@contextmanager
def csv_writer_ctx(path: Path) -> Generator:
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("w", newline="", encoding="utf-8")
    try:
        writer = csv.DictWriter(
            fh, fieldnames=["order_id", "customer", "status", "total"]
        )
        writer.writeheader()
        yield writer
    finally:
        fh.close()


def daily_batches(
    orders: list[Order], start: date
) -> Generator[list[Order], None, None]:
    for offset in range(7):
        day = start + timedelta(days=offset)
        yield [o for o in orders if o.placed_at and o.placed_at.date() == day]


def category_revenue(orders: Iterable[Order]) -> dict[str, float]:
    totals: defaultdict[str, float] = defaultdict(float)
    active = {OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED}
    for order in orders:
        if order.status not in active:
            continue
        for line in order.lines:
            totals[line.product.category] += line.subtotal
    return dict(sorted(totals.items(), key=lambda kv: kv[1], reverse=True))


def top_products(orders: Iterable[Order], n: int = 5) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for order in orders:
        for line in order.lines:
            counter[line.product.sku] += line.quantity
    return counter.most_common(n)


async def fetch_stock(sku: str) -> dict[str, Any]:
    await asyncio.sleep(0.05)
    return {"sku": sku, "warehouse_qty": 100}


async def sync_all_stock(skus: list[str]) -> list[dict[str, Any]]:
    return await asyncio.gather(*[fetch_stock(s) for s in skus])


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    laptop = Product("LP-1001", "Pro Laptop 15",  1299.00, 20, "Electronics")
    mouse  = Product("MS-2001", "Wireless Mouse",    29.99, 50, "Peripherals")
    desk   = Product("DK-3001", "Standing Desk",   549.00,  8, "Furniture")

    customer = Customer(
        "C-10001", "alex@example.com", "Alex", "Jones",
        Address("12 High Street", "London", "GB", "EC1A 1BB"),
        lifetime_spend=2500.00,
    )
    print(f"Tier: {customer.tier.name} ({customer.discount:.0%} off)")

    processor = MockProcessor()
    order = Order("ORD-20240001", customer)
    order.add_line(laptop, 1)
    order.add_line(mouse,  2)
    order.confirm()
    txn = order.pay(processor)
    order.ship()
    order.deliver()

    print("\n".join(order.receipt_lines()))
    print(f"\nTransaction: {txn}")
    print(f"Total charged (mock): £{processor.total_charged:.2f}")

    rev = category_revenue([order])
    print("\nRevenue by category:")
    for cat, amt in rev.items():
        print(f"  {cat}: £{amt:.2f}")

    with csv_writer_ctx(Path("output/orders.csv")) as w:
        d = order.to_dict()
        w.writerow({
            "order_id": d["order_id"],
            "customer": d["customer"]["full_name"],
            "status":   d["status"],
            "total":    d["total"],
        })

    stock = asyncio.run(sync_all_stock([laptop.sku, mouse.sku, desk.sku]))
    print("\nWarehouse stock:")
    for s in stock:
        print(f"  {s['sku']}: {s['warehouse_qty']} units")

    pairs = list(itertools.combinations([laptop.sku, mouse.sku, desk.sku], 2))
    print(f"\nBundle pairs: {pairs}")

    payload = json.dumps(order.to_dict(), indent=2, default=str)
    reloaded = json.loads(payload)
    assert reloaded["order_id"] == order.order_id
    print("\nJSON round-trip: OK")

    assert isinstance(order, Exportable)
    print(f"Exportable protocol check: OK")


if __name__ == "__main__":
    main()
