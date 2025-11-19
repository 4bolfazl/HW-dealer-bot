import asyncio

from bot.config import cfg
from bot.db.Database import Database
from bot.telegram.handlers import build_application


async def main():
    db = Database(cfg["database_path"])
    app = await build_application(cfg, db)
    print("Bot has been built.")
    await app.initialize()
    await app.start()
    try:
        await app.updater.start_polling(drop_pending_updates=True)
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
