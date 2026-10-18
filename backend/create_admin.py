#!/usr/bin/env python3
"""
Secure CLI utility to provision the first or additional administrator accounts.
Prompts for credentials interactively without echoing passwords to the console.
Strictly refuses to overwrite existing user accounts.
"""

import argparse
import getpass
import os
import sys

# Ensure backend package can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

load_dotenv()

from backend.database import (
    create_user,
    get_user_by_email,
    init_db,
    normalize_email,
)
from backend.schemas import EMAIL_REGEX
from backend.security import hash_password


def prompt_admin_credentials():
    print("=" * 60)
    print(" Store-Share: Bootstrap Administrator Account")
    print("=" * 60)

    # 1. Email input & validation
    while True:
        email = input("Enter admin email address: ").strip()
        if not email or not EMAIL_REGEX.match(email):
            print("Invalid email format. Please try again.")
            continue
        break

    normalized_email = normalize_email(email)

    # 2. Check if user already exists
    existing = get_user_by_email(normalized_email)
    if existing:
        print(f"\n[SECURITY REFUSAL] An account with email '{normalized_email}' already exists.")
        print("This tool will never overwrite or alter an existing account.")
        sys.exit(1)

    # 3. Secure password input
    while True:
        password = getpass.getpass("Enter admin password (min 8 characters): ")
        if len(password) < 8:
            print("Password must be at least 8 characters long.")
            continue
        if len(password) > 128:
            print("Password cannot exceed 128 characters.")
            continue

        confirm = getpass.getpass("Confirm admin password: ")
        if password != confirm:
            print("Passwords do not match. Please try again.\n")
            continue
        break

    return normalized_email, password


def create_admin_account(email: str, password: str) -> None:
    # Ensure database tables exist
    init_db()

    normalized = normalize_email(email)
    if not EMAIL_REGEX.match(normalized):
        print(f"Error: Invalid email format '{normalized}'.")
        sys.exit(1)

    if len(password) < 8 or len(password) > 128:
        print("Error: Password must be between 8 and 128 characters.")
        sys.exit(1)

    existing = get_user_by_email(normalized)
    if existing:
        print(f"\n[SECURITY REFUSAL] An account with email '{normalized}' already exists.")
        print("Refusing to overwrite existing user record.")
        sys.exit(1)

    # Hash with Argon2id; never log or print password
    password_hash = hash_password(password)

    user = create_user(
        email=normalized,
        password_hash=password_hash,
        role="ADMIN",
        is_active=True,
    )

    print("\n" + "=" * 60)
    print(f" SUCCESS: Administrator account created successfully!")
    print(f" User ID: {user['id']}")
    print(f" Email:   {user['email']}")
    print(f" Role:    {user['role']}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Bootstrap Store-Share Admin Account")
    parser.add_argument("--email", help="Admin email (optional for non-interactive automation)")
    parser.add_argument(
        "--password-env",
        help="Environment variable name containing password (for secure automation only)",
    )
    args = parser.parse_args()

    # Support scripted non-interactive execution if explicitly provided
    if args.email and args.password_env:
        pwd = os.environ.get(args.password_env)
        if not pwd:
            print(f"Error: Environment variable '{args.password_env}' is empty or not set.")
            sys.exit(1)
        create_admin_account(args.email, pwd)
    elif args.email:
        # Prompt only for password securely
        existing = get_user_by_email(normalize_email(args.email))
        if existing:
            print(f"[SECURITY REFUSAL] An account with email '{args.email}' already exists.")
            sys.exit(1)
        pwd = getpass.getpass(f"Enter password for admin {args.email}: ")
        confirm = getpass.getpass("Confirm password: ")
        if pwd != confirm:
            print("Passwords do not match.")
            sys.exit(1)
        create_admin_account(args.email, pwd)
    else:
        email, pwd = prompt_admin_credentials()
        create_admin_account(email, pwd)


if __name__ == "__main__":
    main()
