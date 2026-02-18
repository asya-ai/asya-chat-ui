from sqlmodel import SQLModel

from app import models  # noqa: F401

metadata = SQLModel.metadata
