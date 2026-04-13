"""Telegram bot integration — backward-compatible shim.

All logic now lives in :mod:`messaging.adapters.telegram`.  This module
re-exports the public functions so that existing imports continue to work.
"""
from messaging.adapters.telegram import (  # noqa: F401
    start_telegram_bot,
    stop_telegram_bot,
    restart_telegram_bot,
    send_message_to_all,
)
