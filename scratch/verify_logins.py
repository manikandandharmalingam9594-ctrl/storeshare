from backend.database import get_db_connection, get_user_by_email
from backend.security import verify_password

candidates = [
    "AdminSecret123!",
    "ProviderSecret123!",
    "ConsumerSecret123!",
    "Secret123!",
    "admin123",
    "password123",
    "storeshare123",
]

protected_emails = [
    "admin@storeshare.io",
    "manikandandharmalingam9594@gmail.com",
    "24d126@psgitech.ac.in",
]

for email in protected_emails:
    user = get_user_by_email(email)
    if not user:
        print(f"User not found: {email}")
        continue
    print(f"User ID {user['id']} ({user['email']}, {user['role']}) active={user['is_active']}")
    matched = None
    for pwd in candidates:
        if verify_password(pwd, user['password_hash']):
            matched = pwd
            break
    if matched:
        print(f"  -> Matches known test/demo credential candidate: '{matched}'")
    else:
        print("  -> Uses custom password (not in candidate list, hash is preserved untouched).")
