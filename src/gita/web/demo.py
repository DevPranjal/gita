"""Create an isolated, reproducible sample Git repository for the web demo."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
from textwrap import dedent


INITIAL_FILES = {
    "README.md": """
        # Relay

        A small order-management service for independent retailers.

        ## Modules
        Catalog, pricing, orders, stock reservations, payments and fulfillment.
        Amounts are integer cents. The service uses Python's standard library.

        ## Development
        Run `python -m compileall -q relay` to check the source.
        This is a fictional demonstration repository, not a payment processor.
    """,
    ".gitignore": "__pycache__/\n*.pyc\n",
    "pyproject.toml": """
        [project]
        name = "relay-orders"
        version = "0.1.0"
        requires-python = ">=3.11"
        description = "Order operations for independent retailers"
    """,
    "relay/__init__.py": '"""Relay order operations."""\n',
    "relay/models.py": """
        from dataclasses import dataclass, field


        @dataclass(frozen=True)
        class LineItem:
            sku: str
            quantity: int
            unit_price: int

            def subtotal(self) -> int:
                return self.quantity * self.unit_price


        @dataclass
        class Order:
            order_id: str
            customer_id: str
            items: list[LineItem] = field(default_factory=list)
            status: str = "draft"

            def total(self) -> int:
                return sum(item.subtotal() for item in self.items)

            def item_count(self) -> int:
                return sum(item.quantity for item in self.items)


        @dataclass(frozen=True)
        class Address:
            street: str
            city: str
            postal_code: str
            country: str = "US"

            def label(self) -> str:
                return f"{self.street}, {self.city} {self.postal_code}, {self.country}"
    """,
    "relay/catalog.py": """
        from dataclasses import dataclass


        @dataclass(frozen=True)
        class Product:
            sku: str
            name: str
            price: int
            category: str
            active: bool = True

            def display_price(self) -> str:
                return f"${self.price / 100:.2f}"


        class Catalog:
            def __init__(self, products: list[Product]):
                self.products = {product.sku: product for product in products}

            def find(self, sku: str) -> Product:
                return self.products[sku]

            def search(self, term: str) -> list[Product]:
                return [product for product in self.products.values() if term in product.name]

            def by_category(self, category: str) -> list[Product]:
                return [product for product in self.products.values() if product.category == category]

            def available(self) -> list[Product]:
                return [product for product in self.products.values() if product.active]
    """,
    "relay/pricing.py": """
        from decimal import Decimal, ROUND_HALF_UP


        def calculate_total(subtotal: int, discount_percent: int = 0) -> int:
            discount = subtotal * discount_percent // 100
            return subtotal - discount


        def calculate_tax(amount: int, rate: str = "0.08") -> int:
            return int((Decimal(amount) * Decimal(rate)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


        def format_money(cents: int, currency: str = "USD") -> str:
            return f"{currency} {cents / 100:.2f}"


        def shipping_cost(subtotal: int) -> int:
            return 0 if subtotal >= 7500 else 599
    """,
    "relay/inventory.py": """
        class Inventory:
            def __init__(self, stock: dict[str, int]):
                self.stock = dict(stock)
                self.reserved: dict[str, int] = {}

            def available(self, sku: str) -> int:
                return self.stock.get(sku, 0) - self.reserved.get(sku, 0)

            def reserve(self, sku: str, quantity: int) -> bool:
                if self.available(sku) < quantity:
                    return False
                self.reserved[sku] = self.reserved.get(sku, 0) + quantity
                return True

            def release(self, sku: str, quantity: int) -> None:
                self.reserved[sku] = max(0, self.reserved.get(sku, 0) - quantity)

            def restock(self, sku: str, quantity: int) -> None:
                self.stock[sku] = self.stock.get(sku, 0) + quantity

            def low_stock(self, threshold: int = 5) -> list[str]:
                return [sku for sku in self.stock if self.available(sku) < threshold]
    """,
    "relay/orders.py": """
        from .models import LineItem, Order
        from .pricing import calculate_total


        def create_order(order_id: str, customer_id: str) -> Order:
            return Order(order_id, customer_id)


        def add_item(order: Order, sku: str, quantity: int, unit_price: int) -> None:
            order.items.append(LineItem(sku, quantity, unit_price))


        def checkout(order: Order, discount_percent: int = 0) -> dict:
            order.status = "confirmed"
            return {"order_id": order.order_id, "total": calculate_total(order.total(), discount_percent)}


        def cancel_order(order: Order) -> None:
            order.status = "cancelled"


        def summarize_order(order: Order) -> dict:
            return {"id": order.order_id, "status": order.status, "items": order.item_count()}
    """,
    "relay/customers.py": """
        from dataclasses import dataclass


        @dataclass
        class Customer:
            customer_id: str
            email: str
            name: str
            lifetime_spend: int = 0

            def display_name(self) -> str:
                return self.name.strip() or self.email

            def loyalty_tier(self) -> str:
                return "gold" if self.lifetime_spend >= 100000 else "standard"


        def normalize_email(email: str) -> str:
            return email.strip().lower()


        def validate_email(email: str) -> bool:
            return "@" in email and "." in email.rsplit("@", 1)[-1]
    """,
    "relay/payments.py": """
        from dataclasses import dataclass


        @dataclass(frozen=True)
        class Receipt:
            order_id: str
            amount: int
            reference: str

            def as_dict(self) -> dict:
                return {"order_id": self.order_id, "amount": self.amount, "reference": self.reference}


        class PaymentLedger:
            def __init__(self):
                self.receipts: dict[str, Receipt] = {}

            def record(self, order_id: str, amount: int) -> Receipt:
                receipt = Receipt(order_id, amount, f"pay_{order_id}")
                self.receipts[order_id] = receipt
                return receipt

            def find(self, order_id: str) -> Receipt | None:
                return self.receipts.get(order_id)

            def total_collected(self) -> int:
                return sum(receipt.amount for receipt in self.receipts.values())
    """,
    "relay/shipping.py": """
        from datetime import date, timedelta


        def estimate_delivery(placed_on: date, express: bool = False) -> date:
            return placed_on + timedelta(days=2 if express else 5)


        def tracking_url(carrier: str, reference: str) -> str:
            return f"https://tracking.example.com/{carrier}/{reference}"


        def shipping_label(order_id: str, address: str) -> str:
            return f"RELAY / {order_id}\\n{address}"
    """,
    "relay/notifications.py": """
        def order_confirmation(order_id: str, customer_name: str) -> dict:
            return {"subject": f"Order {order_id} confirmed", "body": f"Thanks, {customer_name}."}


        def shipment_notice(order_id: str, tracking: str) -> dict:
            return {"subject": f"Order {order_id} shipped", "body": tracking}


        def low_stock_notice(skus: list[str]) -> dict:
            return {"subject": "Stock alert", "body": ", ".join(skus)}
    """,
    "relay/analytics.py": """
        def daily_revenue(orders: list[dict]) -> int:
            return sum(order["total"] for order in orders)


        def average_order_value(orders: list[dict]) -> int:
            return daily_revenue(orders) // len(orders) if orders else 0


        def top_products(items: list[dict], limit: int = 5) -> list[tuple[str, int]]:
            counts: dict[str, int] = {}
            for item in items:
                counts[item["sku"]] = counts.get(item["sku"], 0) + item["quantity"]
            return sorted(counts.items(), key=lambda entry: entry[1], reverse=True)[:limit]
    """,
}


def git(repo: Path, *arguments: str, env: dict | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments], check=True, capture_output=True,
        text=True, encoding="utf-8", env=env, timeout=30,
    )
    return result.stdout.strip()


def create_demo(repo: Path) -> Path:
    repo = repo.resolve()
    if repo.exists():
        raise FileExistsError(f"Refusing to overwrite {repo}. Choose a new directory.")
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Relay Demo")
    git(repo, "config", "user.email", "demo@relay.example")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "config", "core.hooksPath", str(repo / ".git" / "demo-no-hooks"))
    sequence = 0

    def write(path: str, content: str) -> None:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dedent(content).lstrip("\n"), encoding="utf-8", newline="\n")

    def replace(path: str, before: str, after: str) -> None:
        target = repo / path
        content = target.read_text(encoding="utf-8")
        if before not in content:
            raise ValueError(f"Missing demo edit in {path}: {before}")
        target.write_text(content.replace(before, after), encoding="utf-8", newline="\n")

    def commit(message: str) -> None:
        nonlocal sequence
        sequence += 1
        name = ("Maya Chen", "Alex Rivera", "Sam Patel")[(sequence - 1) % 3]
        stamp = f"2026-08-{sequence:02d}T10:00:00+00:00"
        env = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_COMMITTER_NAME=name,
                   GIT_AUTHOR_EMAIL=name.lower().replace(" ", ".") + "@relay.example",
                   GIT_COMMITTER_EMAIL="demo@relay.example",
                   GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        git(repo, "add", ".")
        git(repo, "commit", "-m", message, env=env)

    for path, content in INITIAL_FILES.items():
        write(path, content)
    commit("feat: introduce Relay order operations")
    git(repo, "tag", "v0.1.0")

    replace("relay/catalog.py", "if term in product.name", "if term.casefold() in product.name.casefold() and product.active")
    commit("fix(catalog): match active products without case sensitivity")
    replace("relay/inventory.py", "if self.available(sku) < quantity:", 'if quantity <= 0:\n            raise ValueError("Quantity must be positive")\n        if self.available(sku) < quantity:')
    commit("fix(inventory): reject non-positive reservations")
    replace("relay/pricing.py", "    discount = subtotal * discount_percent // 100", '    if subtotal < 0 or not 0 <= discount_percent <= 100:\n        raise ValueError("Invalid subtotal or discount")\n    discount = subtotal * discount_percent // 100')
    commit("fix(pricing): validate discount boundaries")
    replace("relay/orders.py", '    order.items.append(LineItem(sku, quantity, unit_price))', '    if order.status != "draft" or quantity <= 0 or unit_price < 0:\n        raise ValueError("Invalid order item")\n    order.items.append(LineItem(sku, quantity, unit_price))')
    commit("fix(orders): validate items before checkout")
    replace("relay/payments.py", '        receipt = Receipt(order_id, amount, f"pay_{order_id}")', '        if amount < 0:\n            raise ValueError("Amount must be nonnegative")\n        if order_id in self.receipts:\n            receipt = self.receipts[order_id]\n            if receipt.amount != amount:\n                raise ValueError("Conflicting payment amount")\n            return receipt\n        receipt = Receipt(order_id, amount, f"pay_{order_id}")')
    commit("feat(payments): make repeated payment recording idempotent")
    replace("relay/customers.py", 'return "gold" if self.lifetime_spend >= 100000 else "standard"', 'if self.lifetime_spend >= 100000:\n            return "gold"\n        return "silver" if self.lifetime_spend >= 25000 else "standard"')
    commit("feat(customers): add silver loyalty tier")
    for path in ("relay/pricing.py", "relay/orders.py"):
        replace(path, "calculate_total", "calculate_order_total")
    commit("refactor(pricing): rename calculate_total to calculate_order_total")
    replace("pyproject.toml", 'version = "0.1.0"', 'version = "0.2.0"')
    commit("chore: release Relay 0.2.0")
    git(repo, "tag", "v0.2.0")

    replace("relay/pricing.py", "subtotal >= 7500", "subtotal >= 5000")
    commit("experiment(shipping): lower free-shipping threshold")
    replace("relay/pricing.py", "subtotal >= 5000", "subtotal >= 7500")
    commit("revert: restore free-shipping threshold after margin review")
    replace("relay/pricing.py", "discount_percent: int = 0) -> int:", "discount_percent: int = 0, *, shipping: int = 0) -> int:")
    replace("relay/pricing.py", "if subtotal < 0 or not", "if subtotal < 0 or shipping < 0 or not")
    replace("relay/pricing.py", "return subtotal - discount", "return subtotal - discount + shipping")
    commit("feat(pricing): include shipping in the order total")
    replace("relay/orders.py", "from .pricing import calculate_order_total", "from .pricing import calculate_order_total, shipping_cost")
    replace("relay/orders.py", '    order.status = "confirmed"', '    if not order.items or order.status != "draft":\n        raise ValueError("Only nonempty draft orders can be checked out")\n    order.status = "confirmed"')
    replace("relay/orders.py", "calculate_order_total(order.total(), discount_percent)", "calculate_order_total(order.total(), discount_percent, shipping=shipping_cost(order.total()))")
    commit("feat(checkout): apply shipping and guard order transitions")
    write("relay/fulfillment/__init__.py", '"""Fulfillment and carrier operations."""\n')
    (repo / "relay/shipping.py").rename(repo / "relay/fulfillment/shipping.py")
    commit("refactor: move shipping into the fulfillment package")
    replace("relay/fulfillment/shipping.py", "    return placed_on + timedelta(days=2 if express else 5)", "    delivery = placed_on + timedelta(days=2 if express else 5)\n    while delivery.weekday() >= 5:\n        delivery += timedelta(days=1)\n    return delivery")
    commit("fix(fulfillment): move weekend delivery estimates to Monday")
    replace("relay/analytics.py", 'sum(order["total"] for order in orders)', 'sum(order["total"] for order in orders if order.get("status") != "cancelled")')
    replace("relay/analytics.py", "    return daily_revenue(orders) // len(orders) if orders else 0", '    completed = [order for order in orders if order.get("status") != "cancelled"]\n    return daily_revenue(completed) // len(completed) if completed else 0')
    commit("fix(analytics): exclude cancelled orders from revenue")
    write("relay/fulfillment/returns.py", """
        from datetime import date


        def eligible_for_return(delivered_on: date, requested_on: date, window_days: int = 30) -> bool:
            return 0 <= (requested_on - delivered_on).days <= window_days


        def refund_amount(paid: int, restocking_fee: int = 0) -> int:
            if paid < 0 or restocking_fee < 0:
                raise ValueError("Amounts must be nonnegative")
            return max(0, paid - restocking_fee)


        def return_reference(order_id: str) -> str:
            return f"ret_{order_id}"
    """)
    commit("feat(fulfillment): introduce returns and refund calculations")
    replace("pyproject.toml", 'version = "0.2.0"', 'version = "0.3.0"')
    replace("README.md", "## Development", "## Release 0.3\nShipping-aware checkout, weekend delivery dates, and returns.\n\n## Development")
    commit("docs: publish Relay 0.3 release notes")
    git(repo, "tag", "v0.3.0")
    return repo


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    repo = create_demo(args.destination)
    print(f"Created {repo} with {git(repo, 'rev-list', '--count', 'HEAD')} commits.")


if __name__ == "__main__":
    main()