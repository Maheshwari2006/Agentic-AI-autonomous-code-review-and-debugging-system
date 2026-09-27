from app.auth import hash_password, verify_password


def test_hash_and_verify_roundtrip():
    h = hash_password("s3cret")
    assert verify_password("s3cret", h)
    assert not verify_password("wrong", h)
