"""SSH connection and prompt contracts."""

from typing import Optional

from pydantic import BaseModel, Field

from ...domain.config import ConnectionConfig
from .common import StrictModel


class ConnectionModel(StrictModel):
    target: str
    identity_file: Optional[str] = None
    # `ssh_options` is CLI-only for now: options such as ProxyCommand run local
    # commands, so the web client relies on ~/.ssh/config instead.


class ConnectRequest(StrictModel):
    target: str
    identity_file: Optional[str] = None

    def to_config(self) -> ConnectionConfig:
        return ConnectionConfig(
            target=self.target, identity_file=self.identity_file or None
        )


class PromptModel(BaseModel):
    id: str
    kind: str = Field(description="`secret` (hidden text answer) or `confirm` (yes/no).")
    message: str


class ConnectionResponse(BaseModel):
    state: str = Field(description="disconnected, connecting, connected, or failed.")
    target: Optional[str] = None
    identity_file: Optional[str] = None
    remote_user: Optional[str] = None
    remote_host: Optional[str] = None
    prompt: Optional[PromptModel] = None
    error: Optional[str] = None
    connected_at: Optional[float] = None


class PromptAnswer(StrictModel):
    """Answer for an SSH prompt. Never logged or stored."""

    answer: Optional[str] = None
    cancel: bool = False
