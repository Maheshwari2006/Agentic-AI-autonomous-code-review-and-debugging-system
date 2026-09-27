"""Password hashing utilities. No JWT/token issuing exists yet -- this is
the gap the acceptance feature request ("add JWT authentication to the
user API") is meant to close."""
import hashlib


def hash_password(raw_password):
    return hashlib.sha256(raw_password.encode("utf-8")).hexdigest()


def verify_password(raw_password, password_hash):
    return hash_password(raw_password) == password_hash
