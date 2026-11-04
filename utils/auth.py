import os

def is_user_allowed(user) -> bool:
    if user is None:
        return False
    allowed_str = os.getenv("ALLOWED_USERS", "")
    if not allowed_str.strip():
        return False
    
    allowed = set(u.strip().lstrip("@").lower() for u in allowed_str.split(",") if u.strip())
    uid_str = str(user.id)
    username = (user.username or "").lower()
    
    return uid_str in allowed or (bool(username) and username in allowed)
