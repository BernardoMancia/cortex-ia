from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_web_app(*args: Any, **kwargs: Any) -> FastAPI:
    from web.server import create_web_app as _create_web_app

    return _create_web_app(*args, **kwargs)


__all__ = ["create_web_app"]
