import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.repositories.users import get_user_by_username
from bellwether.security.jwt import decode_token
from bellwether.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


def resolve_token(token: str, session: Session) -> User | None:
    """Decode a raw JWT and look up the User it names.

    Shared by `get_current_user` (bearer header) and the SSE `/stream`
    endpoint (`?token=` query param) so both paths validate identically.
    Returns None (never raises) on any invalid/expired token or unknown/
    inactive user, so callers can turn that into whatever error they need.
    """
    try:
        payload = decode_token(token)
        username = payload.get("sub")
    except jwt.InvalidTokenError:
        return None
    if not username:
        return None
    user = get_user_by_username(session, username)
    if user is None or not user.is_active:
        return None
    return user


def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication",
        headers={"WWW-Authenticate": "Bearer"},
    )
    user = resolve_token(token, session)
    if user is None:
        raise cred_exc
    return user
