from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column, JSON
from sqlmodel import Field, Relationship, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(index=True, sa_column_kwargs={"unique": True})
    username: Optional[str] = Field(default=None, index=True, sa_column_kwargs={"unique": True})
    display_name: Optional[str] = Field(default=None)
    hashed_password: str
    is_active: bool = Field(default=True)
    is_super_admin: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    memberships: List["OrgMembership"] = Relationship(back_populates="user")
    chats: List["Chat"] = Relationship(back_populates="user")
    usage_events: List["UsageEvent"] = Relationship(back_populates="user")


class Org(SQLModel, table=True):
    __tablename__ = "orgs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(index=True, sa_column_kwargs={"unique": True})
    is_active: bool = Field(default=True)
    is_frozen: bool = Field(default=False)
    web_tools_enabled: bool = Field(default=False)
    web_search_enabled: bool = Field(default=True)
    web_scrape_enabled: bool = Field(default=True)
    web_grounding_openai: bool = Field(default=False)
    web_grounding_gemini: bool = Field(default=False)
    exec_network_enabled: bool = Field(default=False)
    exec_policy: str = Field(default="off")
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    memberships: List["OrgMembership"] = Relationship(back_populates="org")
    roles: List["Role"] = Relationship(back_populates="org")
    invites: List["Invite"] = Relationship(back_populates="org")
    chats: List["Chat"] = Relationship(back_populates="org")
    usage_events: List["UsageEvent"] = Relationship(back_populates="org")


class Role(SQLModel, table=True):
    __tablename__ = "roles"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(foreign_key="orgs.id", index=True)
    name: str
    is_default: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    org: Org = Relationship(back_populates="roles")
    memberships: List["OrgMembership"] = Relationship(back_populates="role")
    permissions: List["RolePermission"] = Relationship(back_populates="role")


class RolePermission(SQLModel, table=True):
    __tablename__ = "role_permissions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    role_id: UUID = Field(foreign_key="roles.id", index=True)
    permission: str = Field(index=True)

    role: Role = Relationship(back_populates="permissions")


class OrgMembership(SQLModel, table=True):
    __tablename__ = "org_memberships"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(foreign_key="orgs.id", index=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    role_id: UUID = Field(foreign_key="roles.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    org: Org = Relationship(back_populates="memberships")
    user: User = Relationship(back_populates="memberships")
    role: Role = Relationship(back_populates="memberships")


class Invite(SQLModel, table=True):
    __tablename__ = "invites"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(foreign_key="orgs.id", index=True)
    email: str = Field(index=True)
    token: str = Field(index=True, sa_column_kwargs={"unique": True})
    expires_at: datetime
    accepted_at: Optional[datetime] = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    created_by_user_id: Optional[UUID] = Field(
        default=None, foreign_key="users.id", nullable=True
    )

    org: Org = Relationship(back_populates="invites")


class ChatModel(SQLModel, table=True):
    __tablename__ = "chat_models"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    provider: str = Field(index=True)
    model_name: str
    display_name: str
    is_active: bool = Field(default=True)
    context_length: Optional[int] = Field(default=None)
    supports_image_input: Optional[bool] = Field(default=None)
    supports_image_output: Optional[bool] = Field(default=None)
    reasoning_effort: Optional[str] = Field(default=None)

    chats: List["Chat"] = Relationship(back_populates="model")
    usage_events: List["UsageEvent"] = Relationship(back_populates="model")
    org_links: List["OrgModel"] = Relationship(back_populates="model")


class OrgModel(SQLModel, table=True):
    __tablename__ = "org_models"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(foreign_key="orgs.id", index=True)
    model_id: UUID = Field(foreign_key="chat_models.id", index=True)
    is_enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    org: Org = Relationship()
    model: ChatModel = Relationship(back_populates="org_links")


class OrgProviderConfig(SQLModel, table=True):
    __tablename__ = "org_provider_configs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(foreign_key="orgs.id", index=True)
    provider: str = Field(index=True)
    is_enabled: bool = Field(default=True)
    api_key_override: Optional[str] = Field(default=None)
    base_url_override: Optional[str] = Field(default=None)
    endpoint_override: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    org: Org = Relationship()


class Chat(SQLModel, table=True):
    __tablename__ = "chats"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(foreign_key="orgs.id", index=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    model_id: Optional[UUID] = Field(default=None, foreign_key="chat_models.id")
    title: Optional[str] = Field(default=None)
    is_deleted: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    org: Org = Relationship(back_populates="chats")
    user: User = Relationship(back_populates="chats")
    model: Optional[ChatModel] = Relationship(back_populates="chats")
    messages: List["ChatMessage"] = Relationship(back_populates="chat")


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    chat_id: UUID = Field(foreign_key="chats.id", index=True)
    model_id: Optional[UUID] = Field(default=None, foreign_key="chat_models.id")
    parent_id: Optional[UUID] = Field(
        default=None, foreign_key="chat_messages.id", index=True
    )
    branch_id: Optional[UUID] = Field(default=None, index=True)
    is_current: bool = Field(default=True, index=True)
    role: str = Field(index=True)
    content: str
    sources: Optional[List[dict]] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    chat: Chat = Relationship(back_populates="messages")


class ChatMessageAttachment(SQLModel, table=True):
    __tablename__ = "chat_message_attachments"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    message_id: UUID = Field(foreign_key="chat_messages.id", index=True)
    file_name: str
    content_type: str
    data_base64: str
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class UsageEvent(SQLModel, table=True):
    __tablename__ = "usage_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(foreign_key="orgs.id", index=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    chat_id: Optional[UUID] = Field(default=None, foreign_key="chats.id", index=True)
    message_id: Optional[UUID] = Field(
        default=None, foreign_key="chat_messages.id", index=True
    )
    model_id: Optional[UUID] = Field(
        default=None, foreign_key="chat_models.id", index=True
    )
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    cached_tokens: int = Field(default=0)
    thinking_tokens: int = Field(default=0)
    image_width: Optional[int] = Field(default=None)
    image_height: Optional[int] = Field(default=None)
    image_count: Optional[int] = Field(default=None)
    image_format: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    org: Org = Relationship(back_populates="usage_events")
    user: User = Relationship(back_populates="usage_events")
    model: Optional[ChatModel] = Relationship(back_populates="usage_events")
