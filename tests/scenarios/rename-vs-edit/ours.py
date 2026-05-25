from typing import Optional


def fetch_user(user_id: int) -> Optional[dict]:
    record = _db_lookup(user_id)
    if record is None:
        return None
    return {"id": record[0], "name": record[1]}


def _db_lookup(user_id: int) -> Optional[tuple]:
    # placeholder
    return (user_id, "alice")


def greet(user_id: int) -> str:
    user = fetch_user(user_id)
    if user is None:
        return "unknown"
    return f"hello, {user['name']}"


def audit(user_id: int) -> None:
    user = fetch_user(user_id)
    print(f"audit: {user}")


def is_known(user_id: int) -> bool:
    return fetch_user(user_id) is not None
