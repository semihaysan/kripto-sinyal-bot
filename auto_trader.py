"""
Otomatik Trading Modülü - KuCoin Futures
=========================================
Telegram sinyallerini otomatik olarak KuCoin Futures'ta işleme dönüştürür.

ÖNEMLİ:
- ccxt.kucoinfutures kullanılır (Spot/swap değil; Futures ayrı API).
- API Key: futures.kucoin.com üzerinden Futures API Key oluşturun.
- SL ve TP, KuCoin/ccxt limiti nedeniyle 2 ayrı trigger emri ile verilir.
- Testnet mümkünse KUCOIN_SANDBOX=true; yoksa AUTO_TRADE_DRY_RUN=true ile test edin (emir göndermez).
"""

import os
import logging
import ccxt
from datetime import datetime, timedelta
from typing import Optional, Dict, List

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')
logger = logging.getLogger('auto_trader')

# =====================================================
# KUCOIN FUTURES API (GitHub Secrets)
# =====================================================
KUCOIN_API_KEY = os.environ.get('KUCOIN_API_KEY')
KUCOIN_API_SECRET = os.environ.get('KUCOIN_API_SECRET')
KUCOIN_API_PASSPHRASE = os.environ.get('KUCOIN_API_PASSPHRASE')
KUCOIN_SANDBOX = os.environ.get('KUCOIN_SANDBOX', 'false').lower() == 'true'
# Sandbox yoksa: DRY_RUN=true ile gerçek API'ye bağlanır, bakiye/pozisyon okur; emir GÖNDERMEZ.
AUTO_TRADE_DRY_RUN = os.environ.get('AUTO_TRADE_DRY_RUN', os.environ.get('DRY_RUN', 'false')).lower() == 'true'

# Risk
RISK_PER_TRADE_PCT = 1.0 / 100   # Trade başına %1 risk
MAX_POSITION_SIZE_USD = 1000.0   # Maks. pozisyon notional (USD)
FIXED_LEVERAGE = 5


def _to_futures_symbol(symbol: str) -> str:
    """ETH/USDT -> ETH/USDT:USDT (KuCoin Futures perpetual)."""
    if not symbol or ':' in symbol:
        return symbol
    base, quote = symbol.split('/') if '/' in symbol else (symbol, 'USDT')
    return f"{base}/{quote}:{quote}"


class AutoTrader:
    """KuCoin Futures otomatik işlem sınıfı."""

    def __init__(self):
        if not all([KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE]):
            raise ValueError("KuCoin Futures API bilgileri eksik. GitHub Secrets: KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE")

        # Futures için ccxt.kucoinfutures (Spot kucoin ile karıştırma)
        self.exchange = ccxt.kucoinfutures({
            'apiKey': KUCOIN_API_KEY,
            'secret': KUCOIN_API_SECRET,
            'password': KUCOIN_API_PASSPHRASE,
            'enableRateLimit': True,
            'sandbox': KUCOIN_SANDBOX,
        })

        try:
            self.exchange.load_markets()
            logger.info(f"KuCoin Futures bağlandı (Sandbox: {KUCOIN_SANDBOX})")
        except Exception as e:
            logger.error(f"KuCoin Futures bağlantı hatası: {e}")
            raise

    def get_account_balance(self) -> float:
        try:
            bal = self.exchange.fetch_balance()
            usdt = bal.get('USDT', {}) or {}
            free = float(usdt.get('free', 0) or 0)
            logger.info(f"USDT (Futures): ${free:.2f}")
            return free
        except Exception as e:
            logger.error(f"Bakiye hatası: {e}")
            return 0.0

    def has_open_position(self, symbol: str) -> bool:
        """Aynı sembolde açık pozisyon var mı?"""
        fsym = _to_futures_symbol(symbol)
        try:
            positions = self.exchange.fetch_positions() or []
            for p in positions:
                if _to_futures_symbol(p.get('symbol', '') or '') != fsym:
                    continue
                c = float(p.get('contracts') or p.get('contractSize') or 0)
                if c != 0:
                    return True
            return False
        except Exception as e:
            logger.warning(f"Pozisyon kontrolü atlanıyor {symbol}: {e}")
            return False

    def calculate_position_size(self, entry: float, stop_loss: float, balance: float) -> float:
        risk = balance * RISK_PER_TRADE_PCT
        price_risk = abs(entry - stop_loss)
        if price_risk <= 0:
            return 0.0
        # PnL = (entry-exit)*q; risk_amount = price_risk * q => q = risk/price_risk (kaldıraç PnL’i değiştirmez)
        size = risk / price_risk
        max_q = MAX_POSITION_SIZE_USD / entry
        size = min(size, max_q)
        return round(size, 4)

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        try:
            fsym = _to_futures_symbol(symbol)
            self.exchange.set_leverage(leverage, fsym)
            logger.info(f"{symbol} kaldıraç {leverage}x")
            return True
        except Exception as e:
            logger.error(f"set_leverage {symbol}: {e}")
            return False

    def _place_stop_order(
        self, symbol: str, side: str, amount: float, stop_price: float,
        reduce_only: bool = True, order_type: str = 'stop_market'
    ) -> bool:
        """Tek bir stop/trigger emri (SL veya TP için). KuCoin: stopPrice + triggerPrice."""
        fsym = _to_futures_symbol(symbol)
        try:
            params = {
                'reduceOnly': reduce_only,
                'stopPrice': stop_price,
                'triggerPrice': stop_price,
            }
            self.exchange.create_order(fsym, order_type, side, amount, None, params=params)
            return True
        except Exception as e:
            logger.warning(f"Stop emir hatası (SL/TP) {symbol}: {e}")
            return False

    def place_order(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        quantity: float,
        leverage: int = FIXED_LEVERAGE,
    ) -> Optional[Dict]:
        """Pozisyon aç; SL ve TP için 2 ayrı trigger emri ver (ccxt tek seferde ikisini desteklemiyor)."""
        fsym = _to_futures_symbol(symbol)
        order_side = 'buy' if side == 'LONG' else 'sell'
        close_side = 'sell' if side == 'LONG' else 'buy'

        try:
            # Borsa hassasiyetine gore miktari yuvarla (DRY_RUN dahil)
            amount_str = self.exchange.amount_to_precision(fsym, quantity)
            quantity = float(amount_str)
            if quantity <= 0:
                logger.error(f"Pozisyon miktari borsa hassasiyetine gore 0: {amount_str}")
                return None

            # Sandbox yoksa: emir gondermeden akisi test et (bakiye/pozisyon okunur, emir GITMEZ)
            if AUTO_TRADE_DRY_RUN:
                logger.info(f"[DRY_RUN] {symbol} {side} açılacaktı: miktar={quantity}, entry≈{entry_price:.4f}, SL={stop_loss:.4f}, TP={take_profit:.4f}, kaldıraç={leverage}x")
                logger.info("[DRY_RUN] Piyasa emri, SL ve TP emirleri gönderilmedi.")
                return {'order_id': 'DRY_RUN', 'symbol': symbol, 'side': side, 'entry_price': entry_price, 'quantity': quantity, 'stop_loss': stop_loss, 'take_profit': take_profit, 'leverage': leverage}

            self.set_leverage(symbol, leverage)
            logger.info(f"{symbol} {side} açılıyor: miktar={quantity}, entry≈{entry_price:.4f}, SL={stop_loss:.4f}, TP={take_profit:.4f}")
            order = self.exchange.create_market_order(fsym, order_side, quantity, params={'leverage': leverage})
            logger.info(f"Pozisyon açıldı: order id={order.get('id')}")

            # SL ve TP ayrı ayrı (KuCoin/ccxt aynı create_order’da ikisini kabul etmiyor)
            ok_sl = self._place_stop_order(symbol, close_side, quantity, stop_loss, reduce_only=True)
            ok_tp = self._place_stop_order(symbol, close_side, quantity, take_profit, reduce_only=True)
            if not ok_sl:
                logger.warning(f"SL emri verilemedi: SL={stop_loss:.4f} — manuel kapatma gerekebilir.")
            if not ok_tp:
                logger.warning(f"TP emri verilemedi: TP={take_profit:.4f} — manuel kapatma gerekebilir.")

            return {
                'order_id': order.get('id'),
                'symbol': symbol,
                'side': side,
                'entry_price': entry_price,
                'quantity': quantity,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'leverage': leverage,
            }
        except Exception as e:
            logger.error(f"place_order hatası {symbol}: {e}")
            return None

    def execute_signal(self, signal_data: Dict) -> bool:
        symbol = signal_data.get('symbol', '')
        side = signal_data.get('signal', '')
        entry = float(signal_data.get('entry', 0))
        sl = float(signal_data.get('sl', 0))
        tp = float(signal_data.get('tp', 0))
        leverage = int(signal_data.get('leverage', FIXED_LEVERAGE))

        if not all([symbol, side, entry, sl, tp]):
            logger.error("Sinyal alanları eksik: symbol, signal, entry, sl, tp")
            return False

        if self.has_open_position(symbol):
            logger.info(f"Zaten açık pozisyon var: {symbol} — atlanıyor.")
            return False

        balance = self.get_account_balance()
        # DRY_RUN'da gerçek bakiye 0 bile olsa "sanki bakiye varmış" gibi simülasyon yap.
        # Canlı modda (DRY_RUN=false) ise bakiye koruması devam eder.
        if AUTO_TRADE_DRY_RUN:
            if balance < 10:
                virtual_balance = 1000.0  # Sadece hesaplama için kullanılan sanal bakiye
                logger.info(f"[DRY_RUN] Gerçek bakiye düşük (${balance:.2f}), sanal bakiye ${virtual_balance:.2f} ile simülasyon yapılacak.")
                effective_balance = virtual_balance
            else:
                effective_balance = balance
        else:
            if balance < 10:
                logger.error(f"Yetersiz bakiye: ${balance:.2f}")
                return False
            effective_balance = balance

        quantity = self.calculate_position_size(entry, sl, effective_balance)
        if quantity <= 0:
            logger.error("Pozisyon büyüklüğü 0.")
            return False

        result = self.place_order(
            symbol=symbol, side=side,
            entry_price=entry, stop_loss=sl, take_profit=tp,
            quantity=quantity, leverage=leverage,
        )
        if result:
            logger.info(f"✅ {symbol} {side} açıldı.")
            return True
        return False


def get_recent_position_closes(symbols: List[str]) -> List[Dict]:
    """
    Son ~16 dakikada TP veya SL ile kapanan pozisyonları tespit et.
    KuCoin Futures: reduce_only + stop/stop_market tipi dolu emirler.
    API yoksa veya hata olursa [] döner.
    """
    out: List[Dict] = []
    try:
        trader = AutoTrader()
    except (ValueError, Exception):
        return out
    since_ms = int((datetime.utcnow() - timedelta(minutes=16)).timestamp() * 1000)
    for sym in symbols:
        fsym = _to_futures_symbol(sym)
        try:
            orders = trader.exchange.fetch_closed_orders(fsym, since=since_ms, limit=50)
        except Exception as e:
            logger.debug(f"fetch_closed_orders {sym} atlandı: {e}")
            continue
        for o in orders:
            status = (o.get('status') or '').lower()
            if status not in ('closed', 'filled', 'done'):
                continue
            info = o.get('info') or {}
            red = o.get('reduceOnly') or info.get('reduceOnly')
            if not red:
                continue
            otype = (o.get('type') or info.get('type') or '').lower()
            if otype not in ('stop_market', 'stop', 'market'):
                continue
            fill = float(o.get('average') or o.get('price') or info.get('dealPrice') or 0)
            if fill <= 0:
                continue
            stop_price = info.get('stopPrice') or info.get('triggerPrice') or o.get('stopPrice')
            try:
                stop_price = float(stop_price) if stop_price is not None else None
            except (TypeError, ValueError):
                stop_price = None
            side = 'LONG' if (o.get('side') or '').lower() == 'sell' else 'SHORT'
            # SL vs TP: 1) Entry varsa tetik vs entry. 2) Yoksa tetik vs işlem: LONG'da TP => işlem>=tetik, SL => işlem<=tetik; SHORT'da TP => işlem<=tetik, SL => işlem>=tetik.
            entry_raw = info.get('entryPrice') or info.get('avgEntryPrice') or info.get('entry') or o.get('entryPrice')
            try:
                entry = float(entry_raw) if entry_raw is not None else None
            except (TypeError, ValueError):
                entry = None
            if entry is not None and stop_price is not None:
                close_type = 'TP' if (side == 'LONG' and stop_price > entry) or (side == 'SHORT' and stop_price < entry) else 'SL'
            elif stop_price is not None:
                # Entry yok: işlem vs tetik. LONG TP => satış fiyatı (işlem) >= tetik; LONG SL => işlem <= tetik. SHORT TP => alış işlem <= tetik; SHORT SL => işlem >= tetik.
                if side == 'LONG':
                    close_type = 'TP' if fill > stop_price else ('SL' if fill < stop_price else None)
                else:
                    close_type = 'TP' if fill < stop_price else ('SL' if fill > stop_price else None)
            else:
                close_type = None
            out.append({
                'symbol': sym,
                'side': side,
                'close_price': fill,
                'trigger_price': stop_price,
                'close_type': close_type,  # 'TP' | 'SL' | None
                'order_id': o.get('id'),
            })
    return out


def execute_auto_trade(signal_data: Dict) -> bool:
    """telegram_bot_github.py'den çağrılan giriş noktası."""
    try:
        trader = AutoTrader()
        return trader.execute_signal(signal_data)
    except ValueError as e:
        logger.error(f"KuCoin Futures ayarı eksik: {e}")
        return False
    except Exception as e:
        logger.error(f"execute_auto_trade: {e}")
        return False
