"""The owner's channel: the Bot API messenger, the bridge to the interactions, and a fake Bot API.

:class:`FakeBotAPI` is a **test double**: a real HTTP server on localhost
that speaks the subset of the Telegram Bot API the bridge uses. Real
Telegram (a bot token, long poll) comes in P10.
"""

from operonx_project.telegram.bridge import TelegramBridge
from operonx_project.telegram.fake import FakeBotAPI
from operonx_project.telegram.messenger import Messenger, TelegramError, TelegramMessenger

__all__ = ["FakeBotAPI", "Messenger", "TelegramBridge", "TelegramError", "TelegramMessenger"]
