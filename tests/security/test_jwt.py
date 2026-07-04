import pytest
import jwt
from bellwether.security.jwt import create_access_token, decode_token


def test_roundtrip():
    token = create_access_token("alice")
    payload = decode_token(token)
    assert payload["sub"] == "alice"


def test_expired_token_rejected():
    token = create_access_token("alice", expires_minutes=-1)
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token)
