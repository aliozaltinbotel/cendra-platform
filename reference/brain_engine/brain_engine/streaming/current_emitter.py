from contextvars import ContextVar, Token
from typing import Optional

from brain_engine.streaming.ag_ui_emitter import AGUIEmitter

_current_emitter: ContextVar[Optional[AGUIEmitter]] = ContextVar(
    "ag_ui_emitter", default=None
)


def set_current_emitter(emitter: AGUIEmitter) -> Token:
    return _current_emitter.set(emitter)


def get_current_emitter() -> Optional[AGUIEmitter]:
    return _current_emitter.get()


def reset_current_emitter(token: Token) -> None:
    _current_emitter.reset(token)
