"""Dev launcher — one command to start everything.

Usage: python scripts/dev.py
"""

import asyncio
import logging
import sys
from pathlib import Path

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.infrastructure.config import Settings
from src.interface.telegram.bot import CognitiveOSBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def main():
    settings = Settings()
    settings.ensure_dirs()

    if not settings.telegram_bot_token:
        print("⚠️  TELEGRAM_BOT_TOKEN not set. Add it to .env file.")
        print("   The bot will not start without a token.")
        return

    bot = CognitiveOSBot(settings)
    await bot.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
