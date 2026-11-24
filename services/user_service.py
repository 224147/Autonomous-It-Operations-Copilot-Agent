"""User management."""
from __future__ import annotations

from sqlalchemy import func, select

from backend.db import Database
from backend.models import User
from backend.schemas import UserCreate, UserUpdate
from backend.security import hash_password, validate_password_strength, verify_password


class UserError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class UserService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list(self) -> list[User]:
        with self._db.session() as session:
            return list(session.scalars(select(User).order_by(User.id)))

    def get(self, user_id: int) -> User | None:
        with self._db.session() as session:
            return session.get(User, user_id)

    def create(self, data: UserCreate) -> User:
        validate_password_strength(data.password)
        email = data.email.lower()
        with self._db.session() as session:
            if session.scalar(select(User).where(User.email == email)):
                raise UserError("A user with this email already exists", 409)
            user = User(email=email, full_name=data.full_name, role=data.role, hashed_password=hash_password(data.password))
            session.add(user)
            session.flush()
            session.refresh(user)
            return user

    def update(self, user_id: int, data: UserUpdate, acting_user_id: int) -> User:
        with self._db.session() as session:
            user = session.get(User, user_id)
            if user is None:
                raise UserError("User not found", 404)
            demoting = (data.role and data.role != "admin" and user.role == "admin") or data.is_active is False
            if demoting and user.role == "admin":
                admins = session.scalar(select(func.count(User.id)).where(User.role == "admin", User.is_active.is_(True)))
                if admins <= 1:
                    raise UserError("Cannot demote or deactivate the last active administrator", 409)
            if user_id == acting_user_id and (data.is_active is False or (data.role and data.role != user.role)):
                raise UserError("You cannot change your own role or deactivate yourself", 409)
            if data.full_name is not None:
                user.full_name = data.full_name
            if data.role is not None:
                user.role = data.role
            if data.is_active is not None:
                user.is_active = data.is_active
            if data.password is not None:
                if user.auth_provider != "local":
                    raise UserError("Password is managed by the identity provider", 400)
                validate_password_strength(data.password)
                user.hashed_password = hash_password(data.password)
            session.flush()
            session.refresh(user)
            return user

    def change_password(self, user_id: int, current: str, new: str) -> None:
        validate_password_strength(new)
        with self._db.session() as session:
            user = session.get(User, user_id)
            if user is None or user.auth_provider != "local":
                raise UserError("Password cannot be changed for this account", 400)
            if not verify_password(current, user.hashed_password):
                raise UserError("Current password is incorrect", 400)
            user.hashed_password = hash_password(new)
