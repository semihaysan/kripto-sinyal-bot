"""
Telegram Bot Test - Bağlantıyı kontrol et
"""
import asyncio
import os
from telegram import Bot
from telegram.error import TelegramError

# GitHub Secrets'tan (veya .env'den)
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

async def test_telegram():
    """Telegram bot çalışıyor mu test et."""
    print("="*50)
    print("TELEGRAM BOT TEST")
    print("="*50)
    
    if not TELEGRAM_BOT_TOKEN:
        print("HATA: TELEGRAM_BOT_TOKEN bulunamadi!")
        print("   GitHub Secrets'ta tanimli mi kontrol et.")
        return
    
    if not TELEGRAM_CHAT_ID:
        print("HATA: TELEGRAM_CHAT_ID bulunamadi!")
        print("   GitHub Secrets'ta tanimli mi kontrol et.")
        return
    
    print(f"OK Token: {TELEGRAM_BOT_TOKEN[:10]}...")
    print(f"OK Chat ID: {TELEGRAM_CHAT_ID}")
    print()
    print("Mesaj gönderiliyor...")
    
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        msg = """
*Bot Calisma Testi*

Bu bir test mesajidir.
Telegram baglantisi basarili.

Bot aktif ve bildirim gonderebiliyor.
"""
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=msg,
            parse_mode='Markdown'
        )
        print("BASARILI: Mesaj gonderildi!")
        print("   Telegram'i kontrol et.")
    except TelegramError as e:
        print(f"HATA: Telegram hatasi: {e}")
        print()
        print("Olası sebepler:")
        print("  - Token yanlış veya geçersiz")
        print("  - Chat ID yanlış")
        print("  - Bot, chat'e mesaj gönderme yetkisi yok")
        print("  - Bot'u chat'e ekledin mi? /start komutunu gönderdin mi?")
    except Exception as e:
        print(f"HATA: Beklenmeyen hata: {e}")
    
    print("="*50)

if __name__ == "__main__":
    asyncio.run(test_telegram())
