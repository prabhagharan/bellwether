from sqlalchemy import select, func
from sqlalchemy.orm import Session
from bellwether.config import get_settings
from bellwether.models.user import User
from bellwether.repositories.users import create_user


def seed_admin(session: Session) -> User | None:
    user_count = session.execute(select(func.count()).select_from(User)).scalar_one()
    if user_count > 0:
        return None
    settings = get_settings()
    admin = create_user(session, settings.admin_username, settings.admin_password)
    session.commit()
    return admin
