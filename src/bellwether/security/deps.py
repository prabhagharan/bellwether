import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.repositories.users import get_user_by_username
from bellwether.security.jwt import decode_token
from bellwether.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        username = payload.get("sub")
    except jwt.InvalidTokenError:
        raise cred_exc
    if not username:
        raise cred_exc
    user = get_user_by_username(session, username)
    if user is None or not user.is_active:
        raise cred_exc
    return user
