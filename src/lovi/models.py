from typing import Literal

from pydantic import BaseModel, Field


class MediaInfo(BaseModel):
    url: str
    title: str = "Ролик"
    author: str = ""
    platform: Literal["YouTube", "TikTok", "Instagram"]
    duration: int = 0
    heights: list[int] = Field(default_factory=list)
    has_audio: bool = True
    has_thumbnail: bool = False


class Selection(BaseModel):
    kind: Literal["video", "audio", "cover"]
    height: int = 0

    @classmethod
    def from_key(cls, key: str) -> "Selection":
        if key in {"audio", "cover"}:
            return cls(kind=key)
        if key.startswith("v") and key[1:].isdigit():
            height = int(key[1:])
            if height in {0, 240, 360, 480, 720, 1080, 1440, 2160}:
                return cls(kind="video", height=height)
        raise ValueError("Неизвестный формат")

    @property
    def key(self) -> str:
        return f"v{self.height}" if self.kind == "video" else self.kind


class MediaSession(BaseModel):
    user_id: int
    chat_id: int
    info: MediaInfo

    def permits(self, selection: Selection) -> bool:
        if selection.kind == "audio":
            return self.info.has_audio
        if selection.kind == "cover":
            return self.info.has_thumbnail
        return selection.height in self.info.heights


class Artifact(BaseModel):
    path: str
    kind: Literal["video", "audio", "cover"]
    width: int = 0
    height: int = 0
    duration: int = 0
    video_codec: str = "h264"


class CachedFile(BaseModel):
    file_id: str
    kind: Literal["video", "audio", "cover"]
    as_document: bool = False
