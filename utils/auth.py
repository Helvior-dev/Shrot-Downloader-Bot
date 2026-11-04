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

def is_user_admin(user) -> bool:
    if user is None:
        return False
    admins_str = os.getenv("ADMIN_USERS", "")
    if not admins_str.strip():
        return False
        
    admins = set(u.strip().lstrip("@").lower() for u in admins_str.split(",") if u.strip())
    uid_str = str(user.id)
    username = (user.username or "").lower()
    
    return uid_str in admins or (bool(username) and username in admins)
