"""Test bootstrap helpers for copaw unit tests."""

from __future__ import annotations

import sys
import types


def _install_nio_stub() -> None:
    """Provide a tiny ``nio`` stub for unit tests that don't hit the network."""
    if "nio" in sys.modules:
        return

    nio = types.ModuleType("nio")
    responses = types.ModuleType("nio.responses")

    class _Dummy:  # pylint: disable=too-few-public-methods
        def __init__(self, *args, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    exported = [
        "AsyncClient",
        "AsyncClientConfig",
        "LoginResponse",
        "MatrixRoom",
        "MegolmEvent",
        "RoomEncryptedAudio",
        "RoomEncryptedFile",
        "RoomEncryptedImage",
        "RoomEncryptedVideo",
        "RoomMessageAudio",
        "RoomMessageFile",
        "RoomMessageImage",
        "RoomMessageText",
        "RoomMessageVideo",
        "SyncResponse",
        "UploadResponse",
    ]

    for name in exported:
        setattr(nio, name, type(name, (_Dummy,), {}))

    responses.JoinedMembersResponse = type("JoinedMembersResponse", (_Dummy,), {})
    responses.WhoamiResponse = type("WhoamiResponse", (_Dummy,), {})

    nio.responses = responses
    sys.modules["nio"] = nio
    sys.modules["nio.responses"] = responses


try:
    import nio  # type: ignore  # noqa: F401
except ModuleNotFoundError:
    _install_nio_stub()
