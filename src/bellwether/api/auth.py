from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.repositories.users import get_user_by_username
from bellwether.security.passwords import verify_password
from bellwether.security.jwt import create_access_token
from bellwether.security.deps import get_current_user
from bellwether.models.user import User

router = APIRouter()


@router.post("/auth/token")
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):
    user = get_user_by_username(session, form.username)
    if user is None or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    return {"access_token": create_access_token(user.username), "token_type": "bearer"}


@router.get("/me")
def me(current: User = Depends(get_current_user)):
    return {"username": current.username}
