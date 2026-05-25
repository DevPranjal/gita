class Inventory:
    def __init__(self) -> None:
        self._items: dict[str, int] = {}

    def add(self, sku: str, qty: int) -> None:
        self._items[sku] = self._items.get(sku, 0) + qty

    def remove(self, sku: str, qty: int) -> None:
        current = self._items.get(sku, 0)
        if qty > current:
            raise ValueError(f"cannot remove {qty} of {sku}; only {current} in stock")
        self._items[sku] = current - qty
