"""Generate .env lines for the web UI admin login.

Usage (on the server, from the project directory):
    docker compose run --rm --no-deps webserver python3 set_admin_password.py
or, anywhere with werkzeug installed:
    python3 set_admin_password.py

Prompts for a password and prints ADMIN_PASSWORD_HASH and FLASK_SECRET_KEY
lines to paste into .env, then restart the webserver container. To change the
password later, replace only ADMIN_PASSWORD_HASH; replacing FLASK_SECRET_KEY
as well signs out every logged-in session.
"""
import getpass
import secrets
import sys

from werkzeug.security import generate_password_hash

MIN_LENGTH = 12

password = getpass.getpass("New admin password: ")
if len(password) < MIN_LENGTH:
    sys.exit(f"Password must be at least {MIN_LENGTH} characters.")
if getpass.getpass("Repeat password: ") != password:
    sys.exit("Passwords do not match.")

print("\nAdd/replace these lines in .env (keep the single quotes):\n")
print(f"ADMIN_PASSWORD_HASH='{generate_password_hash(password)}'")
print("\nFirst-time setup only (a new key signs everyone out):")
print(f"FLASK_SECRET_KEY='{secrets.token_hex(32)}'")
print("\nThen: docker compose up -d webserver")
