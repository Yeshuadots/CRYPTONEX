import requests
import pandas as pd
import numpy as np
import asyncio
import json
import os
import re
from datetime import datetime, timezone

import telegram

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    PreCheckoutQueryHandler,
    MessageHandler,
    filters
)

from ta.trend import EMAIndicator, MACD, ADXIndicator, CCIIndicator
from ta.momentum import RSIIndicator, StochRSIIndicator, ROCIndicator
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import MFIIndicator, OnBalanceVolumeIndicator


# ============================================================
# НАСТРОЙКИ
# ============================================================

# ============================================================
# TELEGRAM BOT TOKEN
# ============================================================
# ВСТАВЬТЕ СЮДА НОВЫЙ ТОКЕН ОТ @BotFather
BOT_TOKEN = "454534534534мойтокен"

BINANCE_URL = "https://api.binance.com"

TIMEFRAMES = ["15m", "1h", "4h", "1d"]

MAX_PAIRS = 40
CANDLE_LIMIT = 300
MIN_SCORE = 72
REQUEST_TIMEOUT = 15


BOT_DESCRIPTION = """🚀 <b>CRYPTONEX</b>

<b>Ваш персональный радар крипторынка.</b>

Пока рынок меняется 24/7, CRYPTONEX непрерывно отслеживает Binance и ищет моменты, в которых рыночные условия начинают складываться в единую картину.

🧠 <b>Глубокий анализ</b>
Тренд • импульс • объём • денежный поток • волатильность • структура движения

⏱ <b>4 уровня рынка</b>
15m • 1H • 4H • 1D

🔎 Система сопоставляет данные разных таймфреймов и отбрасывает ситуации, в которых сигналы противоречат друг другу.

🎯 <b>Готовый сценарий</b>
Entry • TP1 • TP2 • Stop Loss • Risk/Reward

⭐ <b>Signal Score</b>
Каждый setup проходит многофакторную оценку перед тем, как попасть в вашу ленту.

🔔 <b>Мониторинг 24/7</b>
Не нужно постоянно следить за графиками. Когда появляется интересная рыночная ситуация — CRYPTONEX сообщает вам.

📊 <b>История и backtest</b>
Анализируйте прошлые сигналы и проверяйте эффективность стратегии на исторических данных.

<b>CRYPTONEX — меньше времени на наблюдение. Больше контроля над рынком.</b>
"""


# ============================================================
# LOGOS
# ============================================================

# Основной источник логотипов.
# Если для конкретной монеты логотип не найден,
# бот просто отправит текстовый сигнал.

LOGO_URL = (
    "https://raw.githubusercontent.com/"
    "spothq/cryptocurrency-icons/master/128/color/"
    "{coin}.png"
)


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

# Пользователи, которые включили автоматические сигналы.
SUBSCRIBERS = set()

# Уже отправленные сигналы: (chat_id, symbol, direction, 1h_candle_time)
SENT_SIGNALS = set()

# История отправленных сигналов. Это история фактов отправки,
# а не выдуманная статистика прибыльности.
SIGNAL_HISTORY = []

# Пользователи и сроки trial/premium.
USERS = {}

# Автоматическая проверка рынка каждые 15 минут.
AUTO_SCAN_MINUTES = 15

SUBSCRIBERS_FILE = "subscribers.json"
HISTORY_FILE = "signal_history.json"
USERS_FILE = "users.json"

TRIAL_DAYS = 30
PREMIUM_DAYS = 30
PREMIUM_PRICE_STARS = 499
SUBSCRIPTION_PERIOD_SECONDS = 2592000

# ============================================================
# ADMIN ACCESS
# ============================================================
# Вставьте сюда свой Telegram ID. Узнать его можно командой /myid.
ADMIN_IDS = {5456517584}  # Убедитесь, что это ваш ID


def load_json_file(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default


def save_json_file(path, data):
    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
    except Exception as error:
        print(f"JSON save error ({path}): {error}")


def load_state():
    global SUBSCRIBERS, SIGNAL_HISTORY, SENT_SIGNALS, USERS

    subscribers = load_json_file(SUBSCRIBERS_FILE, [])
    SUBSCRIBERS = {
        int(chat_id)
        for chat_id in subscribers
        if str(chat_id).lstrip("-").isdigit()
    }

    history = load_json_file(HISTORY_FILE, [])
    if isinstance(history, list):
        SIGNAL_HISTORY = history[-100:]

    users = load_json_file(USERS_FILE, {})
    USERS = users if isinstance(users, dict) else {}

    SENT_SIGNALS.clear()
    for item in SIGNAL_HISTORY:
        try:
            SENT_SIGNALS.add((
                int(item["chat_id"]),
                item["symbol"],
                item["direction"],
                int(item.get("signal_candle_time", 0))
            ))
        except Exception:
            continue


def save_state():
    save_json_file(SUBSCRIBERS_FILE, sorted(SUBSCRIBERS))
    save_json_file(HISTORY_FILE, SIGNAL_HISTORY[-100:])
    save_json_file(USERS_FILE, USERS)


def ensure_user(chat_id):
    key = str(chat_id)

    if key not in USERS:
        now = datetime.now(timezone.utc)
        trial_until = now.timestamp() + TRIAL_DAYS * 86400

        USERS[key] = {
            "trial_until": trial_until,
            "premium_until": 0,
            "premium": False,
            "created_at": now.timestamp(),
            "last_payment_charge_id": "",
        }

        return True

    return False


def user_access(chat_id):
    ensure_user(chat_id)

    data = USERS[str(chat_id)]
    now = datetime.now(timezone.utc).timestamp()

    trial_until = float(data.get("trial_until", 0) or 0)
    premium_until = float(data.get("premium_until", 0) or 0)

    if premium_until > now:
        return "premium", premium_until

    if trial_until > now:
        return "trial", trial_until

    return "expired", 0


def access_days_left(until_timestamp):
    if not until_timestamp:
        return 0

    seconds = max(0, until_timestamp - datetime.now(timezone.utc).timestamp())
    return int((seconds + 86399) // 86400)


def has_active_access(chat_id):
    status, _ = user_access(chat_id)
    return status in {"trial", "premium"}


def is_admin(chat_id):
    return int(chat_id) in ADMIN_IDS


def admin_user_label(chat_id):
    data = USERS.get(str(chat_id), {})
    username = data.get("username", "")
    first_name = data.get("first_name", "")

    if username:
        return f"@{username.lstrip('@')}"
    if first_name:
        return first_name
    return str(chat_id)


def admin_status_label(chat_id):
    status, until_timestamp = user_access(chat_id)
    days = access_days_left(until_timestamp)

    if status == "premium":
        return f"👑 Premium — {days} дн."
    if status == "trial":
        return f"🎁 Trial — {days} дн."
    return "🔒 Доступ завершён"


load_state()


def binance_get(endpoint, params=None):

    response = session.get(
        BINANCE_URL + endpoint,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# ПОЛУЧАЕМ ЛИКВИДНЫЕ ПАРЫ
# ============================================================

def get_liquid_pairs():

    tickers = binance_get(
        "/api/v3/ticker/24hr"
    )

    excluded = {
        "USDT",
        "USDC",
        "FDUSD",
        "TUSD",
        "DAI",
        "USDE",
        "USD1",
        "RLUSD",
        "BUSD",
        "XAUT"
    }

    pairs = []

    for item in tickers:

        symbol = item["symbol"]

        if not symbol.endswith("USDT"):
            continue

        base = symbol[:-4]

        if base in excluded:
            continue

        try:

            volume = float(
                item["quoteVolume"]
            )

        except:

            continue

        if volume < 10_000_000:
            continue

        pairs.append(
            (symbol, volume)
        )

    pairs.sort(
        key=lambda x: x[1],
        reverse=True
    )

    return [
        pair[0]
        for pair in pairs[:MAX_PAIRS]
    ]


# ============================================================
# СВЕЧИ BINANCE
# ============================================================

def get_candles(symbol, timeframe, limit=CANDLE_LIMIT, end_time=None):
    params = {
        "symbol": symbol,
        "interval": timeframe,
        "limit": min(int(limit), 1000),
    }

    if end_time is not None:
        params["endTime"] = int(end_time)

    data = binance_get(
        "/api/v3/klines",
        params
    )

    columns = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "buy_base",
        "buy_quote",
        "ignore"
    ]

    df = pd.DataFrame(
        data,
        columns=columns
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df["time"] = pd.to_numeric(df["time"], errors="coerce")
    df["close_time"] = pd.to_numeric(df["close_time"], errors="coerce")

    return df.dropna(
        subset=["time", "close_time", "open", "high", "low", "close", "volume"]
    )


def get_historical_candles(symbol, timeframe, total_limit=1000):
    """
    Загружает исторические свечи несколькими запросами.
    Binance ограничивает один запрос примерно 1000 свечами.
    """
    total_limit = max(300, min(int(total_limit), 3000))
    batches = []
    remaining = total_limit
    end_time = None

    while remaining > 0:
        batch_size = min(1000, remaining)
        df = get_candles(
            symbol,
            timeframe,
            limit=batch_size,
            end_time=end_time
        )

        if df.empty:
            break

        batches.append(df)

        oldest_open = int(df["time"].iloc[0])
        next_end = oldest_open - 1

        if end_time is not None and next_end >= end_time:
            break

        end_time = next_end
        remaining -= len(df)

        if len(df) < batch_size:
            break

    if not batches:
        return pd.DataFrame()

    result = (
        pd.concat(batches, ignore_index=True)
        .drop_duplicates(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )

    return result.tail(total_limit).reset_index(drop=True)



# ============================================================
# BACKTEST ENGINE
# ============================================================

BACKTEST_BARS = 300
BACKTEST_HOLD_BARS = 12
BACKTEST_MIN_SCORE = MIN_SCORE
BACKTEST_HISTORY_LIMIT = 1000


def evaluate_backtest_signal(df, index):
    """
    Walk-forward проверка на одном таймфрейме.
    Используются только данные до index включительно.
    """
    if index < 220:
        return None

    window = df.iloc[:index + 1].copy()
    window = add_indicators(window)

    if len(window) < 10:
        return None

    row = window.iloc[-1]
    prev = window.iloc[-2]

    buy = 0
    sell = 0

    # Trend
    if row["ema20"] > row["ema50"] > row["ema200"]:
        buy += 4
    elif row["ema20"] < row["ema50"] < row["ema200"]:
        sell += 4
    elif row["ema20"] > row["ema50"]:
        buy += 2
    elif row["ema20"] < row["ema50"]:
        sell += 2

    if row["close"] > row["ema200"]:
        buy += 2
    else:
        sell += 2

    # Momentum / slope
    if row["ema20_slope"] > 0.05 and row["ema50_slope"] > 0:
        buy += 2
    elif row["ema20_slope"] < -0.05 and row["ema50_slope"] < 0:
        sell += 2

    rsi = float(row["rsi"])
    if 52 <= rsi <= 68:
        buy += 2
    elif 42 <= rsi < 48:
        sell += 1
    elif rsi > 70 and row["ema20"] > row["ema50"]:
        buy += 1
    elif rsi > 70 and row["ema20"] < row["ema50"]:
        sell += 1
    elif rsi < 30 and row["ema20"] > row["ema50"]:
        buy += 1

    if row["macd"] > row["macd_signal"] and row["macd_hist_delta"] > 0:
        buy += 3
    elif row["macd"] < row["macd_signal"] and row["macd_hist_delta"] < 0:
        sell += 3
    elif row["macd"] > row["macd_signal"]:
        buy += 1
    elif row["macd"] < row["macd_signal"]:
        sell += 1

    adx = float(row["adx"])
    if adx >= 25:
        if row["di_plus"] > row["di_minus"]:
            buy += 3
        elif row["di_minus"] > row["di_plus"]:
            sell += 3
    elif adx >= 18:
        if row["di_plus"] > row["di_minus"]:
            buy += 1
        elif row["di_minus"] > row["di_plus"]:
            sell += 1

    vr = float(row["volume_ratio"])
    if vr >= 1.5:
        if row["close"] > row["open"]:
            buy += 2
        elif row["close"] < row["open"]:
            sell += 2
    elif vr >= 1.2:
        if row["close"] > row["open"]:
            buy += 1
        elif row["close"] < row["open"]:
            sell += 1

    mfi = float(row["mfi"])
    if 50 <= mfi <= 70:
        buy += 1
    elif 30 <= mfi < 50:
        sell += 1

    if row["obv"] > row["obv_ema20"] and row["obv"] > prev["obv"]:
        buy += 1
    elif row["obv"] < row["obv_ema20"] and row["obv"] < prev["obv"]:
        sell += 1

    cci = float(row["cci"])
    if cci > 100:
        buy += 1
    elif cci < -100:
        sell += 1

    if row["stoch_k"] > row["stoch_d"] and row["stoch_k"] > prev["stoch_k"]:
        buy += 1
    elif row["stoch_k"] < row["stoch_d"] and row["stoch_k"] < prev["stoch_k"]:
        sell += 1

    roc = float(row["roc"])
    if roc > 0:
        buy += 1
    elif roc < 0:
        sell += 1

    total = buy + sell
    if total <= 0:
        return None

    if buy > sell:
        direction = "BUY"
    elif sell > buy:
        direction = "SELL"
    else:
        return None

    raw_score = max(buy, sell) / total * 100
    balance_penalty = abs(buy - sell) / total
    score = raw_score * (0.70 + 0.30 * balance_penalty)

    if score < BACKTEST_MIN_SCORE:
        return None

    entry = float(row["close"])
    atr = float(row["atr"])

    if not np.isfinite(atr) or atr <= 0:
        return None

    if direction == "BUY":
        sl = entry - atr * 1.6
        tp1 = entry + atr * 2.0
        tp2 = entry + atr * 3.0
    else:
        sl = entry + atr * 1.6
        tp1 = entry - atr * 2.0
        tp2 = entry - atr * 3.0

    return {
        "index": index,
        "direction": direction,
        "score": score,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
    }


def run_backtest_on_dataframe(df):
    """
    Оценивает последнюю часть истории.
    Важно: это тест одной таймфреймовой модели, а не всей MTF-стратегии.
    """
    df = df.copy()

    if len(df) < 240:
        return {
            "signals": 0,
            "tp1": 0,
            "tp2": 0,
            "sl": 0,
            "open": 0,
            "win_rate_tp1": 0,
            "win_rate_tp2": 0,
            "expectancy_r": 0,
        }

    start = max(220, len(df) - BACKTEST_BARS)

    signals = []
    tp1_count = 0
    tp2_count = 0
    sl_count = 0
    open_count = 0
    r_results = []

    for i in range(start, len(df) - 1):
        signal = evaluate_backtest_signal(df, i)
        if not signal:
            continue

        end = min(len(df), i + 1 + BACKTEST_HOLD_BARS)
        future = df.iloc[i + 1:end]

        result = "OPEN"
        r_result = 0.0

        for _, candle in future.iterrows():
            high = float(candle["high"])
            low = float(candle["low"])

            if signal["direction"] == "BUY":
                hit_sl = low <= signal["sl"]
                hit_tp2 = high >= signal["tp2"]
                hit_tp1 = high >= signal["tp1"]
            else:
                hit_sl = high >= signal["sl"]
                hit_tp2 = low <= signal["tp2"]
                hit_tp1 = low <= signal["tp1"]

            # Если TP и SL попали в одну свечу, считаем SL первым:
            # без тиковых данных порядок внутри свечи неизвестен.
            if hit_sl:
                result = "SL"
                r_result = -1.0
                break

            if hit_tp2:
                result = "TP2"
                r_result = 1.875  # TP2 3.0 ATR / risk 1.6 ATR
                break

            if hit_tp1:
                result = "TP1"
                r_result = 1.25   # TP1 2.0 ATR / risk 1.6 ATR
                break

        if result == "TP2":
            tp2_count += 1
            tp1_count += 1
        elif result == "TP1":
            tp1_count += 1
        elif result == "SL":
            sl_count += 1
        else:
            open_count += 1

        r_results.append(r_result)
        signals.append({
            "direction": signal["direction"],
            "score": round(signal["score"], 1),
            "result": result,
            "r": r_result,
        })

    total = len(signals)
    expectancy_r = sum(r_results) / total if total else 0

    return {
        "signals": total,
        "tp1": tp1_count,
        "tp2": tp2_count,
        "sl": sl_count,
        "open": open_count,
        "win_rate_tp1": (tp1_count / total * 100) if total else 0,
        "win_rate_tp2": (tp2_count / total * 100) if total else 0,
        "expectancy_r": expectancy_r,
        "signals_data": signals,
    }


def format_backtest_report(symbol, timeframe, result):
    if result["signals"] == 0:
        return (
            "📊 <b>BACKTEST CRYPTONEX</b>\n\n"
            f"🪙 <b>{symbol}</b>\n"
            f"⏱ <b>{timeframe}</b>\n\n"
            "Недостаточно исторических сигналов для оценки.\n"
            "Попробуйте более ликвидную пару или другой таймфрейм."
        )

    return (
        f"📊 <b>BACKTEST CRYPTONEX</b>\n\n"
        f"🪙 <b>{symbol}</b>\n"
        f"⏱ <b>{timeframe}</b>\n\n"
        f"📌 Сигналов: <b>{result['signals']}</b>\n"
        f"🎯 TP1: <b>{result['tp1']}</b> ({result['win_rate_tp1']:.1f}%)\n"
        f"🎯 TP2: <b>{result['tp2']}</b> ({result['win_rate_tp2']:.1f}%)\n"
        f"🛑 SL: <b>{result['sl']}</b>\n"
        f"⏳ Не определились: <b>{result['open']}</b>\n"
        f"📈 Expectancy: <b>{result['expectancy_r']:+.2f}R</b>\n\n"
        f"⚖️ Горизонт проверки: {BACKTEST_HOLD_BARS} свечей\n"
        "🔒 Используются только доступные на тот момент исторические свечи.\n"
        "ℹ️ Backtest оценивает текущую модель на одном таймфрейме."
    )


async def backtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Команда:
    /backtest BTCUSDT 1h
    """
    args = context.args or []

    symbol = args[0].upper() if len(args) >= 1 else "BTCUSDT"
    timeframe = args[1] if len(args) >= 2 else "1h"

    if not re.fullmatch(r"[A-Z0-9]{4,20}", symbol):
        await update.message.reply_text("Неверный символ пары. Пример: BTCUSDT")
        return

    allowed = {"15m", "1h", "4h", "1d"}
    if timeframe not in allowed:
        await update.message.reply_text(
            "Использование: /backtest BTCUSDT 1h\n"
            "Таймфрейм: 15m, 1h, 4h или 1d."
        )
        return

    await update.message.reply_text(
        f"⏳ Загружаю историю для {symbol} • {timeframe}..."
    )

    try:
        df = await asyncio.to_thread(
            get_historical_candles,
            symbol,
            timeframe,
            BACKTEST_HISTORY_LIMIT
        )

        if df.empty:
            raise ValueError("Binance не вернул исторические данные.")

        result = await asyncio.to_thread(
            run_backtest_on_dataframe,
            df
        )

        await update.message.reply_text(
            format_backtest_report(symbol, timeframe, result),
            parse_mode="HTML"
        )

    except Exception as error:
        await update.message.reply_text(
            f"⚠️ <b>Ошибка backtest</b>\n\n"
            f"<code>{str(error)[:400]}</code>",
            parse_mode="HTML"
        )


# ============================================================
# ИНДИКАТОРЫ
# ============================================================

def add_indicators(df):

    df = df.copy()

    df["ema20"] = EMAIndicator(df["close"], window=20).ema_indicator()
    df["ema50"] = EMAIndicator(df["close"], window=50).ema_indicator()
    df["ema200"] = EMAIndicator(df["close"], window=200).ema_indicator()

    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()

    macd = MACD(
        df["close"],
        window_fast=12,
        window_slow=26,
        window_sign=9
    )
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()

    adx = ADXIndicator(
        df["high"],
        df["low"],
        df["close"],
        window=14
    )
    df["adx"] = adx.adx()
    df["di_plus"] = adx.adx_pos()
    df["di_minus"] = adx.adx_neg()

    df["atr"] = AverageTrueRange(
        df["high"],
        df["low"],
        df["close"],
        window=14
    ).average_true_range()

    bb = BollingerBands(df["close"], window=20, window_dev=2)
    df["bb_high"] = bb.bollinger_hband()
    df["bb_low"] = bb.bollinger_lband()
    df["bb_mid"] = bb.bollinger_mavg()
    df["bb_width"] = (df["bb_high"] - df["bb_low"]) / df["bb_mid"]
    df["bb_position"] = (
        (df["close"] - df["bb_low"]) /
        (df["bb_high"] - df["bb_low"])
    )

    df["volume_ma"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_ma"]

    # Дополнительные подтверждения: money flow, OBV, CCI, Stoch RSI и ROC.
    df["mfi"] = MFIIndicator(
        df["high"], df["low"], df["close"], df["volume"], window=14
    ).money_flow_index()

    df["obv"] = OnBalanceVolumeIndicator(
        close=df["close"], volume=df["volume"]
    ).on_balance_volume()

    df["obv_ema20"] = EMAIndicator(df["obv"], window=20).ema_indicator()

    df["cci"] = CCIIndicator(
        df["high"], df["low"], df["close"], window=20
    ).cci()

    stoch = StochRSIIndicator(
        df["close"], window=14, smooth1=3, smooth2=3
    )
    df["stoch_rsi"] = stoch.stochrsi()
    df["stoch_k"] = stoch.stochrsi_k()
    df["stoch_d"] = stoch.stochrsi_d()

    df["roc"] = ROCIndicator(df["close"], window=12).roc()

    # Наклон EMA и импульс MACD — полезнее простого сравнения значений.
    df["ema20_slope"] = df["ema20"].pct_change(3) * 100
    df["ema50_slope"] = df["ema50"].pct_change(3) * 100
    df["macd_hist_delta"] = df["macd_hist"].diff()

    return df.replace([np.inf, -np.inf], np.nan).dropna()


# ============================================================
# АНАЛИЗ ТАЙМФРЕЙМА
# ============================================================

def analyze_timeframe(df):

    # Важнейшее исправление: анализируем последнюю ЗАКРЫТУЮ свечу,
    # а не формирующуюся. Это уменьшает ложные сигналы внутри свечи.
    row = df.iloc[-2]
    prev = df.iloc[-3]

    buy = 0
    sell = 0
    max_score = 0

    reasons_buy = []
    reasons_sell = []

    def add_buy(points, reason):
        nonlocal buy, max_score
        buy += points
        max_score += points
        reasons_buy.append(reason)

    def add_sell(points, reason):
        nonlocal sell, max_score
        sell += points
        max_score += points
        reasons_sell.append(reason)

    # 1. Trend structure — самый большой вес.
    if row["ema20"] > row["ema50"] > row["ema200"]:
        add_buy(4, "EMA20 > EMA50 > EMA200")
    elif row["ema20"] < row["ema50"] < row["ema200"]:
        add_sell(4, "EMA20 < EMA50 < EMA200")
    elif row["ema20"] > row["ema50"]:
        add_buy(2, "EMA20 выше EMA50")
    elif row["ema20"] < row["ema50"]:
        add_sell(2, "EMA20 ниже EMA50")

    if row["close"] > row["ema200"]:
        add_buy(2, "Цена выше EMA200")
    else:
        add_sell(2, "Цена ниже EMA200")

    # 2. Trend slope.
    if row["ema20_slope"] > 0.05 and row["ema50_slope"] > 0:
        add_buy(2, "Положительный наклон тренда")
    elif row["ema20_slope"] < -0.05 and row["ema50_slope"] < 0:
        add_sell(2, "Отрицательный наклон тренда")

    # 3. RSI — не переворачиваем BUY в SELL только из-за перекупленности.
    # В сильном тренде RSI > 70 может быть признаком импульса, а не разворота.
    rsi = float(row["rsi"])
    if 52 <= rsi <= 68:
        add_buy(2, f"RSI {rsi:.1f} — здоровый bullish диапазон")
    elif 42 <= rsi < 48:
        add_sell(1, f"RSI {rsi:.1f} — слабость")
    elif rsi < 30:
        # Перепроданность сама по себе не разворот, только слабое подтверждение.
        if row["ema20"] > row["ema50"]:
            add_buy(1, f"RSI {rsi:.1f} — глубокая перепроданность в восходящем тренде")
    elif rsi > 70:
        if row["ema20"] > row["ema50"]:
            add_buy(1, f"RSI {rsi:.1f} — сильный импульс")
        elif row["ema20"] < row["ema50"]:
            add_sell(1, f"RSI {rsi:.1f} — риск разворота вниз")

    # 4. MACD + изменение гистограммы.
    if row["macd"] > row["macd_signal"] and row["macd_hist_delta"] > 0:
        add_buy(3, "MACD bullish + импульс усиливается")
    elif row["macd"] < row["macd_signal"] and row["macd_hist_delta"] < 0:
        add_sell(3, "MACD bearish + импульс усиливается")
    elif row["macd"] > row["macd_signal"]:
        add_buy(1, "MACD bullish")
    elif row["macd"] < row["macd_signal"]:
        add_sell(1, "MACD bearish")

    # 5. ADX + direction.
    adx = float(row["adx"])
    if adx >= 25:
        if row["di_plus"] > row["di_minus"]:
            add_buy(3, f"ADX {adx:.1f} + DI+ сильнее DI-")
        else:
            add_sell(3, f"ADX {adx:.1f} + DI- сильнее DI+")
    elif adx >= 18:
        if row["di_plus"] > row["di_minus"]:
            add_buy(1, f"ADX {adx:.1f} — тренд формируется")
        elif row["di_minus"] > row["di_plus"]:
            add_sell(1, f"ADX {adx:.1f} — тренд формируется")

    # 6. Volume.
    vr = float(row["volume_ratio"])
    if vr >= 1.5:
        if row["close"] > row["open"]:
            add_buy(2, f"Объём {vr:.1f}x среднего + bullish свеча")
        elif row["close"] < row["open"]:
            add_sell(2, f"Объём {vr:.1f}x среднего + bearish свеча")
    elif vr >= 1.2:
        if row["close"] > row["open"]:
            add_buy(1, "Объём выше среднего")
        elif row["close"] < row["open"]:
            add_sell(1, "Объём выше среднего")

    # 7. Money Flow.
    mfi = float(row["mfi"])
    if 50 <= mfi <= 70:
        add_buy(1, f"MFI {mfi:.1f}")
    elif 30 <= mfi < 50:
        add_sell(1, f"MFI {mfi:.1f}")

    # 8. OBV trend.
    if row["obv"] > row["obv_ema20"] and row["obv"] > prev["obv"]:
        add_buy(1, "OBV растёт")
    elif row["obv"] < row["obv_ema20"] and row["obv"] < prev["obv"]:
        add_sell(1, "OBV снижается")

    # 9. CCI.
    cci = float(row["cci"])
    if cci > 100:
        add_buy(1, f"CCI {cci:.0f}")
    elif cci < -100:
        add_sell(1, f"CCI {cci:.0f}")

    # 10. Stoch RSI — учитываем только направление пересечения, а не экстремум сам по себе.
    if row["stoch_k"] > row["stoch_d"] and row["stoch_k"] > prev["stoch_k"]:
        add_buy(1, "Stoch RSI bullish")
    elif row["stoch_k"] < row["stoch_d"] and row["stoch_k"] < prev["stoch_k"]:
        add_sell(1, "Stoch RSI bearish")

    # 11. ROC.
    roc = float(row["roc"])
    if roc > 0:
        add_buy(1, f"ROC {roc:.2f}%")
    elif roc < 0:
        add_sell(1, f"ROC {roc:.2f}%")

    # 12. Bollinger — фильтр качества, а не самостоятельный сигнал.
    bb_pos = float(row["bb_position"])
    bb_width = float(row["bb_width"])
    if 0.55 <= bb_pos <= 0.90 and bb_width > 0.03 and row["close"] > row["bb_mid"]:
        add_buy(1, "Цена в верхней части Bollinger + нормальная волатильность")
    elif 0.10 <= bb_pos <= 0.45 and bb_width > 0.03 and row["close"] < row["bb_mid"]:
        add_sell(1, "Цена в нижней части Bollinger + нормальная волатильность")

    total = buy + sell
    if total <= 0:
        return {
            "direction": "WAIT",
            "score": 0,
            "rsi": rsi,
            "adx": adx,
            "atr": float(row["atr"]),
            "price": float(row["close"]),
            "mfi": mfi,
            "bb_width": bb_width,
            "reasons": []
        }

    if buy > sell:
        direction = "BUY"
        raw_score = buy / total * 100
        reasons = reasons_buy
    elif sell > buy:
        direction = "SELL"
        raw_score = sell / total * 100
        reasons = reasons_sell
    else:
        direction = "WAIT"
        raw_score = 0
        reasons = []

    # Penalize indecision: близкий buy/sell счёт не должен получать высокий score.
    balance_penalty = abs(buy - sell) / total
    score = raw_score * (0.70 + 0.30 * balance_penalty)

    return {
        "direction": direction,
        "score": score,
        "rsi": rsi,
        "adx": adx,
        "atr": float(row["atr"]),
        "price": float(row["close"]),
        "mfi": mfi,
        "bb_width": bb_width,
        "reasons": reasons,
        "buy_points": buy,
        "sell_points": sell
    }


# ============================================================
# MARKET REGIME
# ============================================================

def get_market_regime():

    try:

        df = get_candles(
            "BTCUSDT",
            "4h"
        )

        df = add_indicators(
            df
        )

        row = df.iloc[-2]

        if (
            row["close"] > row["ema200"]
            and
            row["ema20"] > row["ema50"]
        ):

            return "RISK ON"

        if (
            row["close"] < row["ema200"]
            and
            row["ema20"] < row["ema50"]
        ):

            return "RISK OFF"

        return "NEUTRAL"

    except Exception:

        return "UNKNOWN"


# ============================================================
# АНАЛИЗ ПАРЫ
# ============================================================

def analyze_pair(symbol, market_regime):

    results = {}

    for timeframe in TIMEFRAMES:

        df = get_candles(symbol, timeframe)

        if len(df) < 220:
            return None

        df = add_indicators(df)
        results[timeframe] = analyze_timeframe(df)

        # Только закрытая свеча используется как идентификатор сигнала.
        if len(df) >= 3:
            results[timeframe]["candle_time"] = int(df.iloc[-2]["close_time"])
        else:
            results[timeframe]["candle_time"] = 0

    weights = {
        "15m": 1,
        "1h": 2,
        "4h": 3,
        "1d": 4
    }

    buy_weight = sum(
        weights[tf] for tf in TIMEFRAMES if results[tf]["direction"] == "BUY"
    )
    sell_weight = sum(
        weights[tf] for tf in TIMEFRAMES if results[tf]["direction"] == "SELL"
    )

    if buy_weight == sell_weight:
        return None

    direction = "BUY" if buy_weight > sell_weight else "SELL"
    mtf_score = max(buy_weight, sell_weight) / 10 * 100

    # Для сильного сигнала 4H должен совпадать с направлением.
    # 1D может быть нейтральным, но не должен жёстко противоречить.
    if results["4h"]["direction"] != direction:
        return None

    if results["1d"]["direction"] not in (direction, "WAIT"):
        return None

    # Тренд подтверждён на 1H/4H/1D.
    trend_matches = sum(
        1 for tf in ("1h", "4h", "1d")
        if results[tf]["direction"] == direction
    )
    trend_score = trend_matches / 3 * 100

    # Средняя сила подтверждений на старших ТФ.
    strategy_score = (
        results["1h"]["score"] * 0.25
        + results["4h"]["score"] * 0.35
        + results["1d"]["score"] * 0.40
    )

    if direction == "BUY":
        if market_regime == "RISK ON":
            market_score = 100
        elif market_regime == "NEUTRAL":
            market_score = 55
        else:
            market_score = 10
    else:
        if market_regime == "RISK OFF":
            market_score = 100
        elif market_regime == "NEUTRAL":
            market_score = 55
        else:
            market_score = 10

    # Дополнительный confirmation score из объёма/денежного потока/импульса.
    one_hour = results["1h"]
    four_hour = results["4h"]
    daily = results["1d"]

    confirmation = 0
    for tf_data in (one_hour, four_hour, daily):
        if direction == "BUY":
            if tf_data["mfi"] >= 50:
                confirmation += 1
        else:
            if tf_data["mfi"] <= 50:
                confirmation += 1

    confirmation_score = confirmation / 3 * 100

    final_score = (
        strategy_score * 0.30
        + mtf_score * 0.25
        + trend_score * 0.20
        + market_score * 0.10
        + confirmation_score * 0.15
    )

    if final_score < MIN_SCORE:
        return None

    # Если 1H сам по себе слабый, не выдаём сигнал только потому,
    # что старшие таймфреймы хорошие.
    if one_hour["score"] < 60:
        return None

    price = one_hour["price"]
    atr = one_hour["atr"]

    if atr <= 0:
        return None

    # Динамические цели на основе ATR.
    if direction == "BUY":
        sl = price - atr * 1.6
        tp1 = price + atr * 2.0
        tp2 = price + atr * 3.0
    else:
        sl = price + atr * 1.6
        tp1 = price - atr * 2.0
        tp2 = price - atr * 3.0

    risk = abs(price - sl)
    reward = abs(tp2 - price)
    rr = reward / risk if risk > 0 else 0

    return {
        "symbol": symbol,
        "direction": direction,
        "score": final_score,
        "price": price,
        "entry": price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": rr,
        "market": market_regime,
        "strategy_score": strategy_score,
        "mtf_score": mtf_score,
        "trend_score": trend_score,
        "confirmation_score": confirmation_score,
        "signal_candle_time": int(one_hour.get("candle_time", 0)),
        "timeframes": results
    }


# ============================================================
# SCAN
# ============================================================

def scan_market():

    print(
        "\nПолучаю список торговых пар..."
    )

    pairs = get_liquid_pairs()

    print(
        f"Выбрано пар: {len(pairs)}"
    )

    market = get_market_regime()

    print(
        f"🌐 MARKET REGIME: {market}"
    )

    results = []


    for index, symbol in enumerate(
        pairs,
        start=1
    ):

        print(
            f"[{index}/{len(pairs)}] "
            f"{symbol}"
        )

        try:

            result = analyze_pair(
                symbol,
                market
            )

            if result:

                results.append(
                    result
                )

        except Exception as error:

            print(
                f"Ошибка {symbol}: {error}"
            )


    results.sort(
        key=lambda x:
        x["score"],
        reverse=True
    )

    return results, market


# ============================================================
# FORMAT PRICE
# ============================================================

def price_format(value):

    if value >= 1000:

        return f"{value:,.2f}"

    if value >= 1:

        return f"{value:.4f}"

    if value >= 0.01:

        return f"{value:.6f}"

    return f"{value:.8f}"


# ============================================================
# QUALITY
# ============================================================

def quality(score):

    if score >= 85:
        return "A+"

    if score >= 78:
        return "A"

    if score >= 72:
        return "B+"

    if score >= 65:
        return "B"

    return "C"


# ============================================================
# LOGO URL
# ============================================================

def get_logo_url(symbol):

    coin = symbol.replace(
        "USDT",
        ""
    ).lower()

    return LOGO_URL.format(
        coin=coin
    )


# ============================================================
# SIGNAL CAPTION
# ============================================================

def signal_caption(signal):

    coin = signal[
        "symbol"
    ].replace(
        "USDT",
        ""
    )


    if signal["direction"] == "BUY":

        action = "ПОКУПКА"

        emoji = "🟢"

    else:

        action = "ПРОДАЖА"

        emoji = "🔴"


    grade = quality(
        signal["score"]
    )


    text = f"""
{emoji} <b>{coin}/USDT — {action}</b>

⭐ <b>Качество: {grade}</b>
📊 <b>Score: {signal['score']:.1f}/100</b>

━━━━━━━━━━━━━━━━━━

💰 <b>Вход</b>
<code>{price_format(signal['entry'])}</code>

🎯 <b>TP1</b>
<code>{price_format(signal['tp1'])}</code>

🎯 <b>TP2</b>
<code>{price_format(signal['tp2'])}</code>

🛑 <b>Stop Loss</b>
<code>{price_format(signal['sl'])}</code>

⚖️ <b>Risk / Reward</b>
1 : {signal['rr']:.2f}

━━━━━━━━━━━━━━━━━━

🌐 <b>Рынок:</b> {signal['market']}

📈 <b>Strategy:</b>
{signal['strategy_score']:.0f}/100

⏱ <b>Multi-Timeframe:</b>
{signal['mtf_score']:.0f}/100

📊 <b>Trend:</b>
{signal['trend_score']:.0f}/100

🧩 <b>Confirmation:</b>
{signal['confirmation_score']:.0f}/100

━━━━━━━━━━━━━━━━━━

<b>TIMEFRAMES</b>
"""


    for timeframe in TIMEFRAMES:

        data = signal[
            "timeframes"
        ][timeframe]


        if data["direction"] == "BUY":

            icon = "🟢"

        elif data["direction"] == "SELL":

            icon = "🔴"

        else:

            icon = "⚪"


        text += (
            f"\n{icon} <b>{timeframe}</b>"
            f" — {data['direction']}"
            f" | RSI {data['rsi']:.1f}"
            f" | ADX {data['adx']:.1f}"
        )


    text += """

━━━━━━━━━━━━━━━━━━

⚖️ <b>Не забывайте о грамотном управлении рисками и соблюдайте правила мани-менеджмента.</b>
"""


    return text


# ============================================================
# MENU
# ============================================================

def menu_keyboard(auto_enabled=True, show_admin=False):

    auto_text = "🔔 Автосигналы: ВКЛ" if auto_enabled else "🔕 Автосигналы: ВЫКЛ"

    rows = [
        [
            InlineKeyboardButton(
                "🔎 Найти лучший сигнал",
                callback_data="best"
            )
        ],
        [
            InlineKeyboardButton(
                "📊 Все setups",
                callback_data="scan"
            )
        ],
        [
            InlineKeyboardButton(
                auto_text,
                callback_data="auto_toggle"
            )
        ],
        [
            InlineKeyboardButton(
                "📜 История сигналов",
                callback_data="history"
            )
        ],
        [
            InlineKeyboardButton(
                "💎 Premium",
                callback_data="premium"
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Меню",
                callback_data="menu"
            )
        ],
        [
            InlineKeyboardButton(
                "🧠 Как работает CRYPTONEX",
                callback_data="info"
            )
        ]
    ]

    if show_admin:
        rows.insert(
            -1,
            [
                InlineKeyboardButton(
                    "🛠 Админ-панель",
                    callback_data="admin"
                )
            ]
        )

    return InlineKeyboardMarkup(rows)


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    # /start показывает клиенту сразу главное меню.
    # Пользователь не должен проходить через дополнительные экраны.
    chat_id = update.effective_chat.id
    ensure_user(chat_id)

    USERS[str(chat_id)]["username"] = (
        update.effective_user.username or ""
    )
    USERS[str(chat_id)]["first_name"] = (
        update.effective_user.first_name or ""
    )

    access, until_timestamp = user_access(chat_id)
    days_left = access_days_left(until_timestamp)

    # На trial/Premium автосигналы включаем автоматически.
    if access in {"trial", "premium"}:
        SUBSCRIBERS.add(chat_id)

    save_state()

    text = """
🚀 <b>CRYPTONEX</b>

Ваш персональный радар крипторынка.
📡 Работает 24/7

📡 Мониторинг рынка: 🟢 РАБОТАЕТ
🔔 Автосигналы: 🟢 ВКЛ

🎁 <b>ВАШ ПРОБНЫЙ ДОСТУП</b>
30 дней Premium бесплатно

━━━━━━━━━━━━━━━━━━

🔎 Найти лучший сигнал
📊 Все setups
🔔 Автосигналы
📜 История сигналов
💎 Premium
🧠 Как работает CRYPTONEX
🏠 Меню
"""

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=menu_keyboard(True)
    )


# ============================================================
# SAFE MESSAGE HELPERS
# ============================================================

async def show_text_message(query, text, keyboard=None):
    """
    Показывает текст независимо от того, что было нажато:
    обычное текстовое сообщение или сообщение с фото/логотипом.
    """
    if query.message and query.message.photo:
        chat_id = query.message.chat_id

        try:
            await query.message.delete()
        except Exception as error:
            print("Delete photo message error:", error)

        return await query.message.get_bot().send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    return await query.edit_message_text(
        text=text,
        parse_mode="HTML",
        reply_markup=keyboard
    )


async def show_text_with_bot(
    query,
    context,
    text,
    keyboard=None,
    parse_mode="HTML",
    reply_markup=None
):
    """
    Показывает текст независимо от типа исходного сообщения.
    Если исходное сообщение содержит фото, удаляем его и отправляем
    новое текстовое сообщение. Если это обычный текст — редактируем его.
    """
    final_keyboard = reply_markup if reply_markup is not None else keyboard

    if query.message and query.message.photo:
        chat_id = query.message.chat_id

        try:
            await query.message.delete()
        except Exception as error:
            print("Delete photo message error:", error)

        return await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=final_keyboard
        )

    return await query.edit_message_text(
        text=text,
        parse_mode=parse_mode,
        reply_markup=final_keyboard
    )


# ============================================================
# BEST SIGNAL (НОВАЯ ВЕРСИЯ С МГНОВЕННОЙ ОБРАТНОЙ СВЯЗЬЮ)
# ============================================================

async def best_signal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """
    Находит и показывает лучший сигнал с мгновенной обратной связью.
    """
    query = update.callback_query
    chat_id = query.message.chat_id

    # ============================================================
    # ШАГ 1: МГНОВЕННАЯ ОБРАТНАЯ СВЯЗЬ (показываем, что процесс начался)
    # ============================================================
    
    # Отвечаем Telegram, чтобы кнопка перестала быть "загруженной"
    await query.answer("🔍 Начинаю сканирование рынка...")
    
    # Отправляем новое сообщение с индикатором загрузки
    loading_message = await context.bot.send_message(
        chat_id=chat_id,
        text="""
🔍 <b>СКАНИРОВАНИЕ РЫНКА</b>

Анализирую ликвидные пары Binance...

⏳ <b>Этапы анализа:</b>
• Загрузка списка пар (до 40) 
• Сбор исторических данных
• Расчёт индикаторов (EMA, RSI, MACD, ADX, ATR...)
• Анализ 4 таймфреймов (15m, 1H, 4H, 1D)
• Оценка качества сигналов

⏱ Обычно занимает около 1 минуты, подождите пожалуйста.
""",
        parse_mode="HTML"
    )

    # Пытаемся удалить исходное сообщение с кнопками (если оно есть)
    try:
        await query.message.delete()
    except Exception as delete_error:
        # Если не удалось удалить — не страшно, продолжим
        print(f"Delete old message error: {delete_error}")

    # ============================================================
    # ШАГ 2: ЗАПУСКАЕМ АНАЛИЗ (тяжёлая операция)
    # ============================================================
    
    try:
        # Запускаем сканирование в отдельном потоке (чтобы не блокировать бота)
        results, market = await asyncio.to_thread(
            scan_market
        )

        # ============================================================
        # ШАГ 3: УДАЛЯЕМ СООБЩЕНИЕ О ЗАГРУЗКЕ
        # ============================================================
        
        try:
            await loading_message.delete()
        except Exception as delete_error:
            print(f"Delete loading message error: {delete_error}")

        # ============================================================
        # ШАГ 4: ПОКАЗЫВАЕМ РЕЗУЛЬТАТ
        # ============================================================
        
        if not results:
            # Сигналов не найдено
            text = f"""
⚪ <b>СИЛЬНОГО СИГНАЛА НЕТ</b>

Проверены ликвидные пары Binance.

🌐 Market Regime: <b>{market}</b>

Ни один setup не прошёл фильтр качества.

Лучшее решение сейчас — ждать подтверждения.
"""

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔄 Проверить снова",
                        callback_data="best"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Меню",
                        callback_data="menu"
                    )
                ]
            ])

            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
            return

        # Нашли лучший сигнал
        best = results[0]
        caption = signal_caption(best)
        logo_url = get_logo_url(best["symbol"])

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔄 Новый анализ",
                    callback_data="best"
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 Все setups",
                    callback_data="scan"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Меню",
                    callback_data="menu"
                )
            ]
        ])

        # Пытаемся отправить с логотипом
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=logo_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        except Exception as logo_error:
            print(f"Logo send error: {logo_error}")
            # Если логотип не загрузился — отправляем текстом
            await context.bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode="HTML",
                reply_markup=keyboard
            )

    except Exception as error:
        # ============================================================
        # ШАГ 5: ОБРАБОТКА ОШИБОК
        # ============================================================
        
        print(f"ERROR in best_signal: {error}")

        try:
            await loading_message.delete()
        except Exception:
            pass

        error_text = f"""
⚠️ <b>Ошибка анализа</b>

Что-то пошло не так во время сканирования.

<code>{str(error)[:300]}</code>

Попробуйте ещё раз через минуту.
"""

        await context.bot.send_message(
            chat_id=chat_id,
            text=error_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔄 Попробовать снова",
                        callback_data="best"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Меню",
                        callback_data="menu"
                    )
                ]
            ])
        )


# ============================================================
# ALL SETUPS (НОВАЯ ВЕРСИЯ С КНОПКАМИ "ПОДРОБНЕЕ")
# ============================================================

async def scan_signals(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """
    Показывает список топ-5 сигналов с кнопками "Подробнее" для каждого.
    """
    query = update.callback_query

    # Показываем загрузку
    loading_text = """
📊 <b>СКАНИРОВАНИЕ РЫНКА</b>

Проверяю ликвидные пары Binance...

⏳ Анализирую рынок.
"""
    await show_text_with_bot(query, context, loading_text)

    try:
        results, market = await asyncio.to_thread(scan_market)

        # Сохраняем результаты в context.user_data для последующего просмотра деталей
        context.user_data["scan_results"] = results
        context.user_data["scan_market"] = market

        if not results:
            text = """
⚪ <b>СИЛЬНЫХ SETUPS НЕ НАЙДЕНО</b>

Сейчас рынок не показывает
достаточно качественных возможностей.
"""
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Обновить", callback_data="scan")],
                [InlineKeyboardButton("🔙 Меню", callback_data="menu")]
            ])
            await show_text_with_bot(query, context, text, keyboard)
            return

        # Формируем текст списка
        text = f"""
📊 <b>TOP SETUPS</b>

🌐 Market: <b>{market}</b>

━━━━━━━━━━━━━━━━━━
"""
        # Собираем кнопки для каждого сигнала
        buttons = []
        for index, signal in enumerate(results[:5]):
            if signal["direction"] == "BUY":
                icon = "🟢"
            else:
                icon = "🔴"
            coin = signal["symbol"].replace("USDT", "")
            text += (
                f"\n{index+1}. {icon} <b>{coin}/USDT</b>"
                f" — {signal['direction']}\n"
                f"⭐ Score: <b>{signal['score']:.1f}</b>"
                f" | {quality(signal['score'])}\n"
            )
            # Кнопка "Подробнее" для каждого сигнала
            buttons.append([
                InlineKeyboardButton(
                    f"📋 Подробнее {coin}",
                    callback_data=f"scan_detail:{index}"
                )
            ])

        # Кнопки управления
        buttons.append([InlineKeyboardButton("🔄 Обновить", callback_data="scan")])
        buttons.append([InlineKeyboardButton("🔙 Меню", callback_data="menu")])

        keyboard = InlineKeyboardMarkup(buttons)

        await show_text_with_bot(query, context, text, keyboard)

    except Exception as error:
        print("ERROR in scan_signals:", error)
        await show_text_with_bot(
            query,
            context,
            f"""
⚠️ <b>Ошибка сканирования</b>

<code>{str(error)[:300]}</code>
""",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Попробовать снова", callback_data="scan")],
                [InlineKeyboardButton("🔙 Меню", callback_data="menu")]
            ])
        )


# ============================================================
# ПОКАЗ СПИСКА БЕЗ ПОВТОРНОГО СКАНИРОВАНИЯ
# ============================================================

async def show_scan_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """
    Показывает сохранённый список сигналов без повторного сканирования.
    """
    query = update.callback_query
    results = context.user_data.get("scan_results")
    market = context.user_data.get("scan_market")

    if not results:
        # Если данных нет — перезапускаем сканирование
        await scan_signals(update, context)
        return

    # Формируем такой же список, как в scan_signals
    text = f"""
📊 <b>TOP SETUPS</b>

🌐 Market: <b>{market}</b>

━━━━━━━━━━━━━━━━━━
"""
    buttons = []
    for index, signal in enumerate(results[:5]):
        if signal["direction"] == "BUY":
            icon = "🟢"
        else:
            icon = "🔴"
        coin = signal["symbol"].replace("USDT", "")
        text += (
            f"\n{index+1}. {icon} <b>{coin}/USDT</b>"
            f" — {signal['direction']}\n"
            f"⭐ Score: <b>{signal['score']:.1f}</b>"
            f" | {quality(signal['score'])}\n"
        )
        buttons.append([
            InlineKeyboardButton(
                f"📋 Подробнее {coin}",
                callback_data=f"scan_detail:{index}"
            )
        ])

    buttons.append([InlineKeyboardButton("🔄 Обновить", callback_data="scan")])
    buttons.append([InlineKeyboardButton("🔙 Меню", callback_data="menu")])

    keyboard = InlineKeyboardMarkup(buttons)

    await show_text_with_bot(query, context, text, keyboard)


# ============================================================
# ДЕТАЛЬНЫЙ ПРОСМОТР СИГНАЛА (без кнопки "Новый анализ")
# ============================================================

async def scan_detail(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """
    Показывает детальный сигнал для выбранной пары из списка.
    """
    query = update.callback_query
    chat_id = query.message.chat_id

    # Извлекаем индекс из callback_data
    try:
        index = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer("Ошибка данных", show_alert=True)
        return

    results = context.user_data.get("scan_results")
    if not results or index >= len(results):
        await query.answer("Данные устарели, выполните обновление", show_alert=True)
        return

    signal = results[index]
    caption = signal_caption(signal)
    logo_url = get_logo_url(signal["symbol"])

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔙 Назад к списку",
                callback_data="scan_list"
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Меню",
                callback_data="menu"
            )
        ]
    ])

    # Пытаемся отправить с логотипом
    try:
        # Удаляем старое сообщение (список) перед отправкой деталей
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=logo_url,
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except Exception as logo_error:
        print(f"Logo send error in detail: {logo_error}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=caption,
            parse_mode="HTML",
            reply_markup=keyboard
        )


# ============================================================
# INFO
# ============================================================

async def info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    text = """
🧠 <b>КАК РАБОТАЕТ CRYPTONEX</b>

CRYPTONEX создан для того, чтобы видеть рынок <b>шире одного графика и одного показателя</b>.

Система анализирует текущее состояние рынка и сопоставляет между собой:

📈 <b>Тренд и структуру движения</b>
⚡ <b>Импульс и силу движения</b>
💰 <b>Объём и денежный поток</b>
🌊 <b>Волатильность и динамику цены</b>
🎯 <b>Ключевые зоны и потенциальные точки входа</b>

⏱ <b>Анализ проходит сразу на четырёх уровнях:</b>

<b>15m → 1H → 4H → 1D</b>

Это позволяет увидеть не только краткосрочное движение, но и понять, <b>поддерживается ли оно более крупным трендом</b>.

🔎 CRYPTONEX сопоставляет полученные данные, оценивает их согласованность и отбрасывает ситуации, в которых рынок не даёт достаточно подтверждений.

⭐ <b>Каждый найденный setup получает собственную оценку качества.</b>

🎯 На выходе система формирует готовый сценарий:

<b>Entry → TP1 → TP2 → Stop Loss</b>

⚖️ <b>Не забывайте о грамотном управлении рисками и соблюдайте правила мани-менеджмента.</b>
"""


    await show_text_with_bot(
        query,
        context,
        text,

        parse_mode="HTML",

        reply_markup=InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "🔙 Назад",
                    callback_data="menu"
                )
            ]

        ])
    )


# ============================================================
# MENU
# ============================================================

async def menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    chat_id = query.message.chat_id
    enabled = chat_id in SUBSCRIBERS

    access, until_timestamp = user_access(chat_id)
    days_left = access_days_left(until_timestamp)

    if access == "premium":
        access_text = f"👑 Premium активен: <b>{days_left} дн.</b>"
    elif access == "trial":
        access_text = f"🎁 Trial: <b>{days_left} дн. бесплатно</b>"
    else:
        access_text = "🔒 Trial завершён — Premium доступен по подписке"

    await show_text_with_bot(
        query,
        context,
        f"""
🚀 <b>CRYPTONEX</b>

Ваш персональный радар крипторынка.
📡 Работает 24/7

📡 Мониторинг рынка: 🟢 РАБОТАЕТ
🔔 Автосигналы: {"🟢 ВКЛ" if enabled else "🔕 ВЫКЛ"}

🎁 <b>ВАШ ПРОБНЫЙ ДОСТУП</b>
30 дней Premium бесплатно

━━━━━━━━━━━━━━━━━━

🔎 Найти лучший сигнал
📊 Все setups
🔔 Автосигналы
📜 История сигналов
💎 Premium
🧠 Как работает CRYPTONEX
🏠 Меню
""",
        parse_mode="HTML",
        reply_markup=menu_keyboard(enabled, is_admin(chat_id))
    )



async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    enabled = chat_id in SUBSCRIBERS
    access, until_timestamp = user_access(chat_id)
    days_left = access_days_left(until_timestamp)

    await update.message.reply_text(
        f"""
🚀 <b>CRYPTONEX STATUS</b>

📡 Мониторинг: 🟢 РАБОТАЕТ
🔔 Автосигналы: {"ВКЛ" if enabled else "ВЫКЛ"}
🎁 Доступ: <b>{access.upper()}</b>
⏳ Осталось: <b>{days_left} дн.</b>
⏱ Интервал: {AUTO_SCAN_MINUTES} минут
📊 Ликвидных пар: до {MAX_PAIRS}
📈 Таймфреймы: 15m • 1H • 4H • 1D
⭐ Минимальный Score: {MIN_SCORE}/100

📜 История: {len([x for x in SIGNAL_HISTORY if x.get("chat_id") == chat_id])} сигналов
""",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Меню", callback_data="menu")]
        ])
    )




# ============================================================
# ACCESS / PAYWALL
# ============================================================

async def access_required(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """
    Проверяет trial/Premium перед действиями, связанными с сигналами.
    Возвращает True, если доступ закрыт.
    """
    query = update.callback_query
    chat_id = query.message.chat_id

    access, until_timestamp = user_access(chat_id)

    if access in {"trial", "premium"}:
        return False

    text = """
🔒 <b>PREMIUM ДОСТУП ЗАВЕРШЁН</b>

Ваши 30 дней бесплатного доступа закончились.

CRYPTONEX больше не отправляет новые сигналы,
пока Premium не будет продлён.

💎 <b>Premium — 499 ₽ / 30 дней</b>

Нажмите кнопку ниже, чтобы перейти к оплате.
"""

    await show_text_with_bot(
        query,
        context,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💎 Купить Premium — 499 ₽",
                    callback_data="premium_buy"
                )
            ],
            [
                InlineKeyboardButton(
                    "🧠 Как работает CRYPTONEX",
                    callback_data="info"
                )
            ],
            [
                InlineKeyboardButton(
                    "🏠 Меню",
                    callback_data="menu"
                )
            ]
        ])
    )

    return True


# ============================================================
# PREMIUM (изменён текст)
# ============================================================

async def premium(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    chat_id = query.message.chat_id

    access, until_timestamp = user_access(chat_id)
    days_left = access_days_left(until_timestamp)

    if access == "premium":
        status_text = f"👑 <b>Premium активен</b> — осталось {days_left} дн."
    elif access == "trial":
        status_text = f"🎁 <b>Пробный Premium активен</b> — осталось {days_left} дн."
    else:
        status_text = "🔒 <b>Пробный период завершён</b>"

    text = f"""
💎 <b>CRYPTONEX PREMIUM</b>

{status_text}

🎁 Первый доступ — <b>30 дней бесплатно</b>.

После пробного периода:
⭐ <b>{PREMIUM_PRICE_STARS} ₽ / 30 дней</b>

<b>В Premium:</b>

🔔 Автоматические сигналы
📊 Полный анализ рынка
🎯 Entry • TP1 • TP2 • Stop Loss
⏱ Multi-Timeframe 15m • 1H • 4H • 1D
⭐ Signal Score
📜 История сигналов
📈 Backtest

⚡ CRYPTONEX отслеживает рынок, пока вы заняты своими делами.

━━━━━━━━━━━━━━━━━━

⚖️ Не забывайте о грамотном управлении рисками и соблюдайте правила мани-менеджмента.
"""

    buttons = []

    if access != "premium":
        buttons.append([
            InlineKeyboardButton(
                f"💎 ОПЛАТИТЬ — {PREMIUM_PRICE_STARS} ₽ / 30 дней",
                callback_data="premium_buy"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🏠 Меню",
            callback_data="menu"
        )
    ])

    await show_text_with_bot(
        query,
        context,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ============================================================
# PREMIUM BUY (ИНСТРУКЦИЯ + ССЫЛКА НА АДМИНА)
# ============================================================

async def premium_buy(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """
    Обработка нажатия на кнопку "Оплатить".
    Показывает инструкцию по оплате и кнопку для связи с администратором.
    """
    query = update.callback_query
    await query.answer()

    # Отправляем сообщение с инструкцией по оплате и кнопкой для связи
    await query.edit_message_text(
        text="💎 <b>ОФОРМЛЕНИЕ PREMIUM</b>\n\n"
             "Стоимость доступа:\n"
             "499 ₽ / 30 дней\n\n"
             "Для оплаты переведите 499 ₽ на банковскую карту:\n\n"
             "💳 Номер карты:\n"
             "<code>4400 4303 8484 2230</code>\n\n"
             "👤 Получатель:\n"
             "YEVGENIY CHAGAY\n"
             "━━━━━━━━━━━━━━━━━━\n\n"
             "После оплаты:\n\n"
             "1️⃣ Отправьте чек или скриншот платежа администратору CRYPTONEX.\n"
             "2️⃣ Администратор проверит оплату.\n"
             "3️⃣ После подтверждения Premium будет активирован на 30 дней.\n\n"
             "Для отправки чека нажмите кнопку ниже:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📩 Написать админу", url="https://t.me/YuJin2504?text=ПРЕМИУМ")],
            [InlineKeyboardButton("🏠 Меню", callback_data="menu")]
        ])
    )


# ============================================================
# PAYMENTS / TELEGRAM STARS (оставлены, но не используются)
# ============================================================

async def precheckout_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.pre_checkout_query
    # Этот обработчик больше не используется, но оставлен для совместимости
    await query.answer(ok=False, error_message="Оплата через Stars отключена. Свяжитесь с администратором.")


async def successful_payment_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    # Этот обработчик больше не используется
    pass


async def paysupport_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        """
💳 <b>ПОДДЕРЖКА ПО ОПЛАТЕ</b>

Если возникла проблема с оплатой CRYPTONEX Premium,
напишите в поддержку и приложите:

• ваш Telegram username / ID
• описание проблемы

Мы свяжемся с вами.
""",
        parse_mode="HTML"
    )

# ============================================================
# BUTTON HANDLER
# ============================================================

async def auto_toggle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    chat_id = query.message.chat_id

    if chat_id in SUBSCRIBERS:
        SUBSCRIBERS.remove(chat_id)
        enabled = False
    else:
        SUBSCRIBERS.add(chat_id)
        enabled = True

    save_state()

    if enabled:
        text = (
            "🔔 <b>Автосигналы ВКЛЮЧЕНЫ</b>\n\n"
            "Я буду самостоятельно проверять рынок и присылать новые сильные setups."
        )
    else:
        text = (
            "🔕 <b>Автосигналы ВЫКЛЮЧЕНЫ</b>\n\n"
            "Автоматическая рассылка остановлена. Ручной анализ остаётся доступен."
        )

    await show_text_with_bot(
        query,
        context,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 В меню", callback_data="menu")]
        ])
    )


async def history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    chat_id = query.message.chat_id
    rows = [item for item in SIGNAL_HISTORY if item.get("chat_id") == chat_id]

    if not rows:
        text = """
📜 <b>ИСТОРИЯ СИГНАЛОВ</b>

Пока автоматических сигналов нет.

Когда бот найдёт новый setup, он появится здесь.
"""
    else:
        text = "📜 <b>ИСТОРИЯ СИГНАЛОВ</b>\n\n"
        for item in rows[-10:][::-1]:
            icon = "🟢" if item["direction"] == "BUY" else "🔴"
            coin = item["symbol"].replace("USDT", "")
            text += (
                f"{icon} <b>{coin}/USDT</b> — {item['direction']}\n"
                f"⭐ Score: <b>{item['score']}</b> | {item['time']}\n"
                f"💰 Entry: <code>{price_format(item['entry'])}</code>\n"
                f"🎯 TP1: <code>{price_format(item['tp1'])}</code> | "
                f"🛑 SL: <code>{price_format(item['sl'])}</code>\n"
                f"📌 Статус: <b>{item['status']}</b>\n\n"
            )

        text += "⚠️ ACTIVE означает, что сигнал был отправлен; результат сделки пока не оценивается автоматически."

    await show_text_with_bot(
        query,
        context,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 В меню", callback_data="menu")]
        ])
    )


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    # Отвечаем Telegram сразу, чтобы кнопка визуально сработала
    # даже если анализ Binance занимает время.
    try:
        await query.answer()
    except Exception as error:
        print("Callback answer error:", error)

    print(f"BUTTON: {query.data}")

    if query.data == "best":

        if await access_required(update, context):
            return

        await best_signal(
            update,
            context
        )

    elif query.data == "scan":

        if await access_required(update, context):
            return

        await scan_signals(
            update,
            context
        )

    elif query.data == "scan_list":
        await show_scan_list(update, context)

    elif query.data.startswith("scan_detail:"):
        await scan_detail(update, context)

    elif query.data == "info":

        await info(
            update,
            context
        )

    elif query.data == "menu":

        await menu(
            update,
            context
        )

    elif query.data == "auto_toggle":

        if await access_required(update, context):
            return

        await auto_toggle(
            update,
            context
        )

    elif query.data == "history":

        await history(
            update,
            context
        )

    elif query.data == "premium":

        await premium(
            update,
            context
        )

    elif query.data == "premium_buy":

        await premium_buy(
            update,
            context
        )

    elif query.data == "admin":

        if not is_admin(query.message.chat_id):
            await query.answer("Доступ запрещён.", show_alert=True)
            return

        await send_admin_panel_message(
            update,
            context
        )

    elif query.data == "admin_search":

        await admin_search_start(
            update,
            context
        )

    elif query.data == "admin_users":

        await admin_users(
            update,
            context
        )

    elif query.data == "admin_stats":

        await admin_stats(
            update,
            context
        )

    elif query.data == "admin_actions":

        await admin_actions(
            update,
            context
        )

    elif query.data.startswith("admin_user:"):

        target_id = int(
            query.data.split(":", 1)[1]
        )

        await admin_user_card(
            update,
            context,
            target_id
        )

    elif query.data.startswith("admin_grant:"):

        _, target_id, days = query.data.split(":")

        await admin_grant(
            update,
            context,
            int(target_id),
            int(days)
        )

    elif query.data.startswith("admin_revoke:"):

        target_id = int(
            query.data.split(":", 1)[1]
        )

        await admin_revoke(
            update,
            context,
            target_id
        )

    elif query.data.startswith("admin_history:"):

        target_id = int(
            query.data.split(":", 1)[1]
        )

        await admin_history(
            update,
            context,
            target_id
        )



async def notify_expired_users(application):
    """
    Один раз уведомляет пользователя после окончания trial/Premium
    и удаляет его из списка автоматической рассылки.
    """
    changed = False

    for chat_id in list(SUBSCRIBERS):
        access, _ = user_access(chat_id)

        if access != "expired":
            continue

        user_data = USERS.get(str(chat_id), {})

        if user_data.get("expiry_notice_sent"):
            SUBSCRIBERS.discard(chat_id)
            continue

        try:
            await application.bot.send_message(
                chat_id=chat_id,
                text="""
🔒 <b>ВАШ ДОСТУП CRYPTONEX ЗАВЕРШЁН</b>

30 дней бесплатного Premium закончились.

Автоматические сигналы и доступ к анализу
приостановлены.

💎 <b>Продолжить пользоваться CRYPTONEX</b>

499 ₽ / 30 дней
""",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "💎 Купить Premium — 499 ₽",
                            callback_data="premium_buy"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🏠 Меню",
                            callback_data="menu"
                        )
                    ]
                ])
            )

            user_data["expiry_notice_sent"] = True
            SUBSCRIBERS.discard(chat_id)
            changed = True

        except Exception as error:
            print(f"Expiry notice error {chat_id}: {error}")

    if changed:
        save_state()


# ============================================================
# АВТОМАТИЧЕСКИЕ СИГНАЛЫ (без кнопки "Открыть сигнал")
# ============================================================

async def send_auto_signal(bot, signal):

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Все setups",
                callback_data="scan"
            )
        ],
        [
            InlineKeyboardButton(
                "🔕 Выключить автосигналы",
                callback_data="auto_toggle"
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Меню",
                callback_data="menu"
            )
        ],
    ])

    caption = "🚨 <b>НОВЫЙ СИГНАЛ</b>\n\n" + signal_caption(signal)
    logo_url = get_logo_url(signal["symbol"])

    dead_chats = set()
    signal_key_base = (
        signal["symbol"],
        signal["direction"],
        signal.get("signal_candle_time", 0)
    )

    for chat_id in list(SUBSCRIBERS):

        if not has_active_access(chat_id):
            continue

        key = (chat_id,) + signal_key_base

        if key in SENT_SIGNALS:
            continue

        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=logo_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard
            )

        except Exception as logo_error:
            print(f"Logo send error for {chat_id}: {logo_error}")

            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=caption,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
            except Exception as send_error:
                print(f"Signal send error for {chat_id}: {send_error}")
                dead_chats.add(chat_id)
                continue

        SENT_SIGNALS.add(key)

        SIGNAL_HISTORY.append({
            "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "chat_id": chat_id,
            "symbol": signal["symbol"],
            "direction": signal["direction"],
            "score": round(float(signal["score"]), 1),
            "entry": float(signal["entry"]),
            "tp1": float(signal["tp1"]),
            "tp2": float(signal["tp2"]),
            "sl": float(signal["sl"]),
            "status": "ACTIVE",
            "signal_candle_time": int(signal.get("signal_candle_time", 0))
        })

    SUBSCRIBERS.difference_update(dead_chats)
    save_state()


async def automatic_scan_loop(application):
    """Постоянно проверяет рынок и отправляет новые сильные сигналы."""

    print(f"🤖 Автосигналы включены: каждые {AUTO_SCAN_MINUTES} минут")

    # Небольшая пауза после запуска, чтобы бот успел подключиться.
    await asyncio.sleep(10)

    while True:
        try:
            await notify_expired_users(application)

            active_subscribers = [
                chat_id
                for chat_id in SUBSCRIBERS
                if has_active_access(chat_id)
            ]

            if active_subscribers:
                print("\n🤖 АВТОСКАН: проверяю рынок...")

                results, market = await asyncio.to_thread(scan_market)

                # Отправляем все сильные setups, начиная с самых сильных.
                for signal in results[:5]:
                    await send_auto_signal(
                        application.bot,
                        signal
                    )

                print(
                    f"🤖 АВТОСКАН завершён: {len(results)} setups, "
                    f"market={market}, активных пользователей={len(active_subscribers)}"
                )
            else:
                print("🤖 АВТОСКАН пропущен: нет подписчиков")

        except asyncio.CancelledError:
            print("🤖 Автоскан остановлен")
            raise

        except Exception as error:
            print(f"🤖 Ошибка автоскана: {error}")

        await asyncio.sleep(AUTO_SCAN_MINUTES * 60)


async def post_init(application):
    # JobQueue здесь не нужен — запускаем обычную фоновую asyncio-задачу.
    application.bot_data["auto_task"] = asyncio.create_task(
        automatic_scan_loop(application)
    )


async def post_shutdown(application):
    task = application.bot_data.get("auto_task")

    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass



def validate_telegram_version():
    parts = tuple(
        int(part)
        for part in telegram.__version__.split(".")[:2]
        if part.isdigit()
    )

    if parts < (21, 8):
        raise RuntimeError(
            "Нужна python-telegram-bot версии 21.8 или новее. "
            "Установите: python -m pip install -U python-telegram-bot"
        )



# ============================================================
# ADMIN PANEL
# ============================================================

def admin_panel_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👥 Пользователи",
                callback_data="admin_users"
            )
        ],
        [
            InlineKeyboardButton(
                "🔎 Найти пользователя",
                callback_data="admin_search"
            )
        ],
        [
            InlineKeyboardButton(
                "📊 Статистика",
                callback_data="admin_stats"
            )
        ],
        [
            InlineKeyboardButton(
                "📨 Управление доступом",
                callback_data="admin_actions"
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Меню",
                callback_data="menu"
            )
        ]
    ])


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🆔 Ваш Telegram ID:\n<code>{update.effective_chat.id}</code>",
        parse_mode="HTML"
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return
    await send_admin_panel_message(update, context)


async def send_admin_panel_message(update, context):
    premium_count = 0
    trial_count = 0
    expired_count = 0

    for user_id in USERS:
        status, _ = user_access(int(user_id))
        if status == "premium":
            premium_count += 1
        elif status == "trial":
            trial_count += 1
        else:
            expired_count += 1

    text = f"""
🛠 <b>CRYPTONEX ADMIN</b>

👥 Пользователей: <b>{len(USERS)}</b>
👑 Active Premium: <b>{premium_count}</b>
🎁 Trial: <b>{trial_count}</b>
🔒 Доступ завершён: <b>{expired_count}</b>

━━━━━━━━━━━━━━━━━━

Выберите действие:
"""

    if update.callback_query:
        await show_text_with_bot(
            update.callback_query,
            context,
            text,
            parse_mode="HTML",
            reply_markup=admin_panel_keyboard()
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=admin_panel_keyboard()
        )



async def admin_search_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    admin_id = query.message.chat_id

    if not is_admin(admin_id):
        await query.answer("Доступ запрещён.", show_alert=True)
        return

    context.user_data["admin_search_mode"] = True

    text = """
🔎 <b>ПОИСК ПОЛЬЗОВАТЕЛЯ</b>

Введите <b>Telegram ID</b> или <b>@username</b>.

Например:
<code>123456789</code>
или
<code>@trader123</code>

Можно также вставить ссылку вида:
<code>t.me/trader123</code>
"""

    await show_text_with_bot(
        query,
        context,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="admin"
                )
            ]
        ])
    )


def _normalize_admin_search(value):
    value = value.strip()
    value = value.replace("https://t.me/", "")
    value = value.replace("http://t.me/", "")
    value = value.replace("t.me/", "")
    value = value.lstrip("@").strip()
    return value


async def admin_search_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    admin_id = update.effective_chat.id

    if not is_admin(admin_id):
        return

    if not context.user_data.get("admin_search_mode"):
        return

    context.user_data["admin_search_mode"] = False

    raw_query = update.message.text or ""
    search = _normalize_admin_search(raw_query)

    if not search:
        await update.message.reply_text(
            "⚠️ Введите Telegram ID или @username.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🛠 Админ-панель",
                        callback_data="admin"
                    )
                ]
            ])
        )
        return

    matches = []

    # Exact Telegram ID search.
    if search.isdigit():
        user_id = int(search)
        if str(user_id) in USERS:
            matches.append(user_id)

    # Username / first-name / last-known identifier search.
    if not matches:
        needle = search.casefold()
        for user_id, data in USERS.items():
            username = str(data.get("username", "")).lstrip("@").casefold()
            first_name = str(data.get("first_name", "")).casefold()

            if needle == username or needle == first_name:
                matches.insert(0, int(user_id))
            elif needle in username or needle in first_name:
                matches.append(int(user_id))

    matches = list(dict.fromkeys(matches))[:10]

    if not matches:
        await update.message.reply_text(
            f"❌ Пользователь <b>{raw_query}</b> не найден.\n\n"
            "Поиск работает среди пользователей, которые уже запускали бота.\n"
            "Попробуйте Telegram ID или @username ещё раз.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔎 Искать снова",
                        callback_data="admin_search"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🛠 Админ-панель",
                        callback_data="admin"
                    )
                ]
            ])
        )
        return

    rows = []
    for user_id in matches:
        rows.append([
            InlineKeyboardButton(
                f"{admin_user_label(user_id)} — {admin_status_label(user_id)}",
                callback_data=f"admin_user:{user_id}"
            )
        ])

    if len(matches) == 1:
        title = "✅ <b>ПОЛЬЗОВАТЕЛЬ НАЙДЕН</b>"
        subtitle = "Нажмите на него, чтобы открыть карточку."
    else:
        title = "🔎 <b>НАЙДЕНО НЕСКОЛЬКО ПОЛЬЗОВАТЕЛЕЙ</b>"
        subtitle = "Выберите нужного пользователя:"

    rows.append([
        InlineKeyboardButton(
            "🔎 Новый поиск",
            callback_data="admin_search"
        )
    ])
    rows.append([
        InlineKeyboardButton(
            "🛠 Админ-панель",
            callback_data="admin"
        )
    ])

    await update.message.reply_text(
        f"{title}\n\n{subtitle}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows)
    )


async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.message.chat_id):
        await query.answer("Доступ запрещён.", show_alert=True)
        return

    rows = []

    for user_id in list(USERS.keys())[-20:][::-1]:
        uid = int(user_id)
        rows.append([
            InlineKeyboardButton(
                f"{admin_user_label(uid)} — {admin_status_label(uid)}",
                callback_data=f"admin_user:{uid}"
            )
        ])

    if not rows:
        rows.append([
            InlineKeyboardButton(
                "Пока пользователей нет",
                callback_data="admin"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "🛠 Админ-панель",
            callback_data="admin"
        )
    ])

    text = """
👥 <b>ПОЛЬЗОВАТЕЛИ</b>

Последние 20 пользователей.
Выберите пользователя для управления доступом.
"""

    await show_text_with_bot(
        query,
        context,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows)
    )


async def admin_user_card(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_id: int
):
    query = update.callback_query
    if not is_admin(query.message.chat_id):
        await query.answer("Доступ запрещён.", show_alert=True)
        return

    ensure_user(target_id)
    data = USERS[str(target_id)]
    access, until_timestamp = user_access(target_id)
    days = access_days_left(until_timestamp)

    username = data.get("username", "")
    first_name = data.get("first_name", "")

    if access == "premium":
        status_text = f"👑 Premium — осталось {days} дн."
    elif access == "trial":
        status_text = f"🎁 Trial — осталось {days} дн."
    else:
        status_text = "🔒 Доступ завершён"

    def fmt_date(value):
        if not value:
            return "—"
        return datetime.fromtimestamp(
            float(value),
            tz=timezone.utc
        ).strftime("%d.%m.%Y %H:%M UTC")

    text = f"""
👤 <b>ПОЛЬЗОВАТЕЛЬ</b>

ID: <code>{target_id}</code>
Username: <b>{('@' + username) if username else '—'}</b>
Имя: <b>{first_name or '—'}</b>

Статус: <b>{status_text}</b>

🎁 Trial до:
<b>{fmt_date(data.get("trial_until"))}</b>

👑 Premium до:
<b>{fmt_date(data.get("premium_until"))}</b>
"""

    await show_text_with_bot(
        query,
        context,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔓 Дать Premium 30 дней",
                    callback_data=f"admin_grant:{target_id}:30"
                )
            ],
            [
                InlineKeyboardButton(
                    "➕ Добавить 7 дней",
                    callback_data=f"admin_grant:{target_id}:7"
                )
            ],
            [
                InlineKeyboardButton(
                    "➖ Забрать Premium",
                    callback_data=f"admin_revoke:{target_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 История сигналов",
                    callback_data=f"admin_history:{target_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "👥 Пользователи",
                    callback_data="admin_users"
                )
            ],
            [
                InlineKeyboardButton(
                    "🛠 Админ-панель",
                    callback_data="admin"
                )
            ]
        ])
    )


async def admin_grant(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_id: int,
    days: int
):
    query = update.callback_query
    admin_id = query.message.chat_id

    if not is_admin(admin_id):
        await query.answer("Доступ запрещён.", show_alert=True)
        return

    ensure_user(target_id)

    now = datetime.now(timezone.utc).timestamp()
    current_until = float(
        USERS[str(target_id)].get("premium_until", 0) or 0
    )

    new_until = max(now, current_until) + days * 86400

    USERS[str(target_id)]["premium"] = True
    USERS[str(target_id)]["premium_until"] = new_until
    USERS[str(target_id)]["expiry_notice_sent"] = False

    SUBSCRIBERS.add(target_id)
    save_state()

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"""
👑 <b>CRYPTONEX PREMIUM АКТИВИРОВАН</b>

Администратор предоставил вам Premium.

✅ Доступ: <b>{days} дней</b>
📅 Действует до:
<b>{datetime.fromtimestamp(new_until, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")}</b>

🔔 Автосигналы: 🟢 ВКЛ
📊 Полный анализ: 🟢 ВКЛ
""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🏠 Меню",
                        callback_data="menu"
                    )
                ]
            ])
        )
    except Exception as error:
        print(f"Admin grant notification error: {error}")

    await admin_user_card(update, context, target_id)


async def admin_revoke(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_id: int
):
    query = update.callback_query
    admin_id = query.message.chat_id

    if not is_admin(admin_id):
        await query.answer("Доступ запрещён.", show_alert=True)
        return

    ensure_user(target_id)

    USERS[str(target_id)]["premium"] = False
    USERS[str(target_id)]["premium_until"] = 0
    USERS[str(target_id)]["expiry_notice_sent"] = False

    SUBSCRIBERS.discard(target_id)
    save_state()

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="""
🔒 <b>CRYPTONEX PREMIUM ОТКЛЮЧЁН</b>

Ваш Premium-доступ был отключён администратором.
""",
            parse_mode="HTML"
        )
    except Exception as error:
        print(f"Admin revoke notification error: {error}")

    await admin_user_card(update, context, target_id)


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.message.chat_id):
        await query.answer("Доступ запрещён.", show_alert=True)
        return

    premium_count = 0
    trial_count = 0
    expired_count = 0

    for user_id in USERS:
        status, _ = user_access(int(user_id))
        if status == "premium":
            premium_count += 1
        elif status == "trial":
            trial_count += 1
        else:
            expired_count += 1

    text = f"""
📊 <b>СТАТИСТИКА CRYPTONEX</b>

👥 Всего пользователей: <b>{len(USERS)}</b>
👑 Premium: <b>{premium_count}</b>
🎁 Trial: <b>{trial_count}</b>
🔒 Доступ завершён: <b>{expired_count}</b>
📨 Сигналов в истории: <b>{len(SIGNAL_HISTORY)}</b>

⭐ Premium:
<b>{PREMIUM_PRICE_STARS} ₽ / 30 дней</b>
"""

    await show_text_with_bot(
        query,
        context,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🛠 Админ-панель",
                    callback_data="admin"
                )
            ]
        ])
    )


async def admin_history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_id: int
):
    query = update.callback_query
    if not is_admin(query.message.chat_id):
        await query.answer("Доступ запрещён.", show_alert=True)
        return

    rows = [
        item
        for item in SIGNAL_HISTORY
        if int(item.get("chat_id", 0)) == target_id
    ]

    if not rows:
        text = (
            "📊 <b>ИСТОРИЯ СИГНАЛОВ</b>\n\n"
            f"Пользователь: <code>{target_id}</code>\n\n"
            "Сигналов пока нет."
        )
    else:
        text = (
            "📊 <b>ИСТОРИЯ СИГНАЛОВ</b>\n\n"
            f"Пользователь: <code>{target_id}</code>\n\n"
        )
        for item in rows[-10:][::-1]:
            icon = "🟢" if item["direction"] == "BUY" else "🔴"
            coin = item["symbol"].replace("USDT", "")
            text += (
                f"{icon} <b>{coin}/USDT</b> — {item['direction']}\n"
                f"⭐ Score: <b>{item['score']}</b> | {item['time']}\n"
                f"📌 {item['status']}\n\n"
            )

    await show_text_with_bot(
        query,
        context,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "👤 Пользователь",
                    callback_data=f"admin_user:{target_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🛠 Админ-панель",
                    callback_data="admin"
                )
            ]
        ])
    )


async def admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.message.chat_id):
        await query.answer("Доступ запрещён.", show_alert=True)
        return

    text = f"""
📨 <b>УПРАВЛЕНИЕ ДОСТУПОМ</b>

👥 Всего пользователей: <b>{len(USERS)}</b>

Для выдачи или отмены Premium:
👥 Пользователи → выберите пользователя.
"""

    await show_text_with_bot(
        query,
        context,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "👥 Пользователи",
                    callback_data="admin_users"
                )
            ],
            [
                InlineKeyboardButton(
                    "🛠 Админ-панель",
                    callback_data="admin"
                )
            ]
        ])
    )


# ============================================================
# MAIN
# ============================================================

def main():

    validate_telegram_version()

    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN. "
            "Вставьте новый токен от @BotFather в строку BOT_TOKEN."
        )

    print()
    print(
        "========================================"
    )

    print(
        "        CRYPTONEX BOT"
    )

    print(
        "========================================"
    )

    print(
        "Запускаю CRYPTONEX..."
    )
    print(
        "Если бот не запускается, проверьте BOT_TOKEN."
    )


    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )


    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "admin",
            admin_command
        )
    )

    application.add_handler(
        CommandHandler(
            "myid",
            myid_command
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            admin_search_message
        )
    )

    application.add_handler(
        CommandHandler(
            "backtest",
            backtest_command
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status_command
        )
    )

    application.add_handler(
        CommandHandler(
            "paysupport",
            paysupport_command
        )
    )

    # Обработчики платежей оставлены для совместимости, но не используются
    application.add_handler(
        PreCheckoutQueryHandler(
            precheckout_callback
        )
    )

    application.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment_callback
        )
    )


    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )


    print(
        "✅ Бот запущен!"
    )

    print(
        "Открой Telegram и нажми /start"
    )


    application.run_polling()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
