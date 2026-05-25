class Inventory:
    def __init__(self) -> None:
        self._items: dict[str, int] = {}

    def add(self, sku: str, qty: int) -> None:
        self._items[sku] = self._items.get(sku, 0) + qty
