"""
Telegram Bot - GitHub Actions versiyonu
Her 15 dakikada bir sinyal kontrolü yapar.
"""

import asyncio
import os
import logging
from datetime import datetime
import pandas as pd
import ccxt
from telegram import Bot
from telegram.error import TelegramError

# =====================================================
# TELEGRAM AYARLARI (GitHub Secrets'tan)
# =====================================================
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# =====================================================
# SINYAL AYARLARI (HIGH PROFIT OPTIMIZED)
# =====================================================
SYMBOLS = ['ETH/USDT', 'SOL/USDT', 'AVAX/USDT', 'LINK/USDT']
MIN_STRENGTH = 70  # Guclu sinyaller (daha fazla firsat)

# Timeframes
TF_TREND = '4h'    # Trend icin 4 saatlik
TF_MOMENTUM = '1h' # Momentum icin 1 saatlik  
TF_ENTRY = '1h'    # Giris icin 1 saatlik

# Strateji parametreleri (HIGH PROFIT)
PARAMS = {
    'entry_bb_period': 20,
    'entry_bb_std': 2.0,
    'entry_rsi_long_max': 58,    # Biraz gevsetildi
    'entry_rsi_short_min': 42,   # Biraz gevsetildi
    'volume_spike_min': 1.0,     # Normal volume yeterli
    'sl_atr_mult': 1.5,          # Siki SL
    'tp_atr_mult': 4.0,          # Genis TP - R:R = 2.67:1
}

# KALDIRAC - 5x (karlilik icin optimize)
FIXED_LEVERAGE = 5

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')
logger = logging.getLogger('bot')


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Teknik indikatorleri hesapla."""
    df = df.copy()
    
    # EMA
    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
    
    # RSI
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss + 1e-10)
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # MACD
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = ema12 - ema26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    
    # Bollinger Bands
    df['bb_middle'] = df['close'].rolling(window=20).mean()
    bb_std = df['close'].rolling(window=20).std()
    df['bb_upper'] = df['bb_middle'] + (bb_std * 2)
    df['bb_lower'] = df['bb_middle'] - (bb_std * 2)
    df['bb_pct'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'] + 1e-10)
    
    # ATR
    high_low = df['high'] - df['low']
    high_close = abs(df['high'] - df['close'].shift())
    low_close = abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = tr.rolling(window=14).mean()
    
    # Volume
    df['volume_ma'] = df['volume'].rolling(window=20).mean()
    df['volume_ratio'] = df['volume'] / (df['volume_ma'] + 1e-10)
    
    return df


def fetch_data(symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
    """KuCoin'den veri cek (GitHub Actions icin - global erisim)."""
    # KuCoin spot kullan (ABD'den erisilebilir)
    exchange = ccxt.kucoin({
        'enableRateLimit': True
    })
    
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df
    except Exception as e:
        logger.error(f"Veri hatasi {symbol}: {e}")
        return pd.DataFrame()


def get_btc_trend() -> int:
    """BTC trend yonu (4h bazli). 1=up, -1=down, 0=neutral"""
    try:
        df = fetch_data('BTC/USDT', TF_TREND, 100)  # 4h
        if df.empty:
            return 0
        df = calculate_indicators(df)
        last = df.iloc[-1]
        if last['close'] > last['ema_50'] and last['macd_hist'] > 0:
            return 1
        elif last['close'] < last['ema_50'] and last['macd_hist'] < 0:
            return -1
        return 0
    except:
        return 0


def check_signal(symbol: str, btc_trend: int) -> dict:
    """Sinyal kontrol et (Optimized 1h entry)."""
    try:
        df_4h = fetch_data(symbol, TF_TREND, 100)    # 4h trend
        df_1h = fetch_data(symbol, TF_ENTRY, 100)    # 1h entry ve momentum
        
        if df_4h.empty or df_1h.empty:
            return None
        
        df_4h = calculate_indicators(df_4h)
        df_1h = calculate_indicators(df_1h)
        
        bar_4h = df_4h.iloc[-1]  # Trend
        bar_1h = df_1h.iloc[-1]  # Entry ve Momentum
        
        # 4H Trend
        trend_up = (bar_4h['close'] > bar_4h['ema_50']) and (bar_4h['macd_hist'] > 0)
        trend_down = (bar_4h['close'] < bar_4h['ema_50']) and (bar_4h['macd_hist'] < 0)
        
        # 1h Momentum
        mom_up = (bar_1h['ema_9'] > bar_1h['ema_21']) and (bar_1h['macd_hist'] > 0)
        mom_down = (bar_1h['ema_9'] < bar_1h['ema_21']) and (bar_1h['macd_hist'] < 0)
        
        # 1h Entry
        rsi_long = bar_1h['rsi'] < PARAMS['entry_rsi_long_max']
        rsi_short = bar_1h['rsi'] > PARAMS['entry_rsi_short_min']
        bb_long = bar_1h['bb_pct'] < 0.6
        bb_short = bar_1h['bb_pct'] > 0.4
        volume_ok = bar_1h['volume_ratio'] >= PARAMS['volume_spike_min']
        
        signal = None
        
        # LONG (backtest stratejisi ile AYNI)
        if trend_up and (mom_up or bar_1h['macd_hist'] > 0) and rsi_long and bb_long and volume_ok:
            if btc_trend >= 0:
                signal = 'LONG'
        # SHORT (backtest stratejisi ile AYNI)
        elif trend_down and (mom_down or bar_1h['macd_hist'] < 0) and rsi_short and bb_short and volume_ok:
            if btc_trend <= 0:
                signal = 'SHORT'
        
        if not signal:
            return None
        
        # Sinyal kuvveti (backtest stratejisi ile AYNI)
        strength = 0
        if signal == 'LONG':
            if trend_up: strength += 30
            if mom_up: strength += 20
            if bar_1h['macd_hist'] > 0: strength += 15
            if bar_1h['volume_ratio'] >= 1.5: strength += 15
            elif bar_1h['volume_ratio'] >= 1.2: strength += 10
            if 30 <= bar_1h['rsi'] <= 50: strength += 10
            elif 25 <= bar_1h['rsi'] <= 55: strength += 5
            if bar_1h['bb_pct'] < 0.3: strength += 10
            elif bar_1h['bb_pct'] < 0.5: strength += 5
        else:
            if trend_down: strength += 30
            if mom_down: strength += 20
            if bar_1h['macd_hist'] < 0: strength += 15
            if bar_1h['volume_ratio'] >= 1.5: strength += 15
            elif bar_1h['volume_ratio'] >= 1.2: strength += 10
            if 50 <= bar_1h['rsi'] <= 70: strength += 10
            elif 45 <= bar_1h['rsi'] <= 75: strength += 5
            if bar_1h['bb_pct'] > 0.7: strength += 10
            elif bar_1h['bb_pct'] > 0.5: strength += 5
        
        strength = min(strength, 100)
        
        if strength < MIN_STRENGTH:
            return None
        
        # Kaldirac - SABIT 5x (dinamik cok riskli)
        leverage = FIXED_LEVERAGE
        
        # SL/TP (1h bar kullaniliyor)
        entry = bar_1h['close']
        atr = bar_1h['atr']
        
        if signal == 'LONG':
            sl = entry - (atr * PARAMS['sl_atr_mult'])
            tp = entry + (atr * PARAMS['tp_atr_mult'])
        else:
            sl = entry + (atr * PARAMS['sl_atr_mult'])
            tp = entry - (atr * PARAMS['tp_atr_mult'])
        
        sl_pct = abs(sl - entry) / entry * 100
        tp_pct = abs(tp - entry) / entry * 100
        
        return {
            'symbol': symbol, 'signal': signal, 'strength': strength,
            'leverage': leverage, 'entry': entry, 'sl': sl, 'tp': tp,
            'sl_pct': sl_pct, 'tp_pct': tp_pct, 'rsi': bar_1h['rsi'],
            'volume': bar_1h['volume_ratio'], 'btc_trend': btc_trend
        }
    except Exception as e:
        logger.error(f"Hata {symbol}: {e}")
        return None


def format_message(s: dict) -> str:
    """Telegram mesaji formatla."""
    emoji = "🟢" if s['signal'] == 'LONG' else "🔴"
    
    if s['btc_trend'] == 1: btc = "⬆️ Yukari"
    elif s['btc_trend'] == -1: btc = "⬇️ Asagi"
    else: btc = "➡️ Notr"
    
    return f"""
{emoji} *{s['signal']} SINYALI* - {s['symbol']}

💰 *Entry:* ${s['entry']:.4f}
🛑 *Stop-Loss:* ${s['sl']:.4f} (-{s['sl_pct']:.1f}%)
🎯 *Take-Profit:* ${s['tp']:.4f} (+{s['tp_pct']:.1f}%)

📈 *RSI:* {s['rsi']:.1f}
📊 *Volume:* {s['volume']:.1f}x
₿ *BTC:* {btc}

⏱ *TF:* 1h | 🕐 {datetime.now().strftime('%H:%M')} UTC
"""


async def send_message(msg: str):
    """Telegram'a gonder."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Token veya Chat ID eksik!")
        return
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode='Markdown')
        logger.info("Mesaj gonderildi")
    except TelegramError as e:
        logger.error(f"Telegram hatasi: {e}")


async def main():
    """Ana fonksiyon."""
    logger.info("="*40)
    logger.info("SINYAL KONTROLU BASLADI")
    logger.info("="*40)
    
    btc_trend = get_btc_trend()
    btc_txt = "UP" if btc_trend == 1 else "DOWN" if btc_trend == -1 else "NEUTRAL"
    logger.info(f"BTC Trend: {btc_txt}")
    
    signals = []
    for symbol in SYMBOLS:
        sig = check_signal(symbol, btc_trend)
        if sig:
            signals.append(sig)
            logger.info(f"  {symbol}: {sig['signal']} (Kuvvet: {sig['strength']})")
        else:
            logger.info(f"  {symbol}: Sinyal yok")
    
    for sig in signals:
        msg = format_message(sig)
        await send_message(msg)
        await asyncio.sleep(1)
    
    if not signals:
        logger.info("Sinyal bulunamadi")
    
    logger.info("="*40)


if __name__ == "__main__":
    asyncio.run(main())


