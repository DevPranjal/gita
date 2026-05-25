from typing import Optional


def get_user(user_id: int) -> Optional[dict]:
    if user_id < 0:
        raise ValueError("user_id must be non-negative")
    record = _db_lookup(user_id)
    if record is None:
        return None
    return {"id": record[0], "name": record[1], "active": True}


def _db_lookup(user_id: int) -> Optional[tuple]:
    # placeholder
    return (user_id, "alice")


def greet(user_id: int) -> str:
    user = get_user(user_id)
    if user is None:
        return "unknown"
    return f"hello, {user['name']}"


def audit(user_id: int) -> None:
    user = get_user(user_id)
    print(f"audit: {user}")


def is_known(user_id: int) -> bool:
    return get_user(user_id) is not None
