from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.models.user import User
from bellwether.security.passwords import hash_password


def get_user_by_username(session: Session, username: str) -> User | None:
    return session.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()


def create_user(session: Session, username: str, password: str) -> User:
    user = User(username=username, hashed_password=hash_password(password))
    session.add(user)
    session.flush()
    return user
