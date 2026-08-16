"""Отдельный процесс-обучалка (вариант B): нейросеть смотрит на стакан
(Indicator 1 VAP/DOM), учится предсказывать, куда пойдёт цена за HORIZON,
и печатает бумажную оценку точности. Реальные ордера не шлёт.

Запуск:  python scripts/run_predictor.py
Останов: Ctrl+C

Настройки через env:
  PREDICTOR_V2=1             — V2-вектор (20 фич: DOM + момент-фичи + SMC), новая БД snapshots_v2 и модель predictor_net_v2.npz
  PREDICTOR_HORIZON_MIN=5    — горизонт прогноза, минут
  PREDICTOR_LABEL_MOVE_PCT=0.12 — минимальное движение для метки, %
  PREDICTOR_RETRAIN_MIN=15   — переобучение, минут
  PREDICTOR_MIN_LABELS=200   — первый запуск обучения при N размеченных
  PREDICTOR_SCAN_INTERVAL=20 — цикл сбора, сек
  PREDICTOR_TOP_SYMBOLS=60   — сколько самых волатильных монет сканируем
  PREDICTOR_MAX_BATCH=600    — макс снипсетов за один проход
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "predictor.log"
LOCK_FILE = Path(__file__).resolve().parent.parent / "logs" / "predictor.pid"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def _acquire_lock() -> bool:
    """Один процесс-обучалка на машину: если PID в lock-файле жив — выходим."""
    import ctypes

    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
        except Exception:
            old_pid = 0
        if old_pid > 0:
            try:
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, old_pid)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    print(f"[predictor] уже запущен (PID {old_pid}), выходим")
                    return False
            except Exception:
                pass
    LOCK_FILE.write_text(str(os.getpid()))
    return True


class _Tee:
    """Дублирует stdout в консоль и в logs/predictor.log (append)."""

    def __init__(self):
        self.console = sys.stdout
        self.file = open(LOG_FILE, "a", encoding="utf-8", buffering=1)

    def write(self, text):
        try:
            self.console.write(text)
        except Exception:
            pass
        self.file.write(text)

    def flush(self):
        try:
            self.console.flush()
        except Exception:
            pass
        self.file.flush()


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import Config
from data.binance import BinanceClient
from features.order_book import indicator1_snapshot
from ml.predictor import (
    DOM_FEATURES,
    DOM_FEATURES_V2,
    MODEL_PATH,
    MODEL_PATH_V2,
    DomPredictor,
    PredictorStore,
    dom_feature_vector,
    dom_feature_vector_v2,
)

USE_V2 = _env_bool("PREDICTOR_V2", True)
HORIZON_SEC = _env_int("PREDICTOR_HORIZON_MIN", 5) * 60
LABEL_MOVE = _env_float("PREDICTOR_LABEL_MOVE_PCT", 0.12) / 100.0
SCAN_INTERVAL = _env_int("PREDICTOR_SCAN_INTERVAL", 20)
RETRAIN_EVERY = _env_int("PREDICTOR_RETRAIN_MIN", 15) * 60
MIN_LABELS_TO_TRAIN = _env_int("PREDICTOR_MIN_LABELS", 200)
TOP_SYMBOLS = _env_int("PREDICTOR_TOP_SYMBOLS", 60)
MAX_BATCH = _env_int("PREDICTOR_MAX_BATCH", 600)

TABLE = "snapshots_v2" if USE_V2 else "snapshots"
MODEL_FILE = MODEL_PATH_V2 if USE_V2 else MODEL_PATH
FEATURE_DIM = len(DOM_FEATURES_V2) if USE_V2 else len(DOM_FEATURES)


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


async def run():
    cfg = Config()
    client = BinanceClient(cfg.binance_api_key, cfg.binance_secret, futures=True)
    client.load_markets()
    store = PredictorStore(table=TABLE)
    predictor = DomPredictor(input_dim=FEATURE_DIM, model_path=MODEL_FILE)
    ever_trained = predictor.load()

    symbols = list(cfg.trade_symbols)
    if cfg.scan_all_symbols or len(symbols) > TOP_SYMBOLS:
        try:
            tickers = client.ex.fetch_tickers()
            change = {
                sym: abs(float(t.get("percentage") or 0.0))
                for sym, t in (tickers or {}).items()
                if sym in cfg.trade_symbols
            }
            symbols = [sym for sym, _ in sorted(change.items(), key=lambda kv: kv[1], reverse=True)][:TOP_SYMBOLS]
        except Exception as exc:
            print(f"[{stamp()}] [predictor] WARN не удалось отсортировать по волатильности: {exc}")
        symbols = symbols[:TOP_SYMBOLS]

    last_train = time.time()
    scan_no = 0
    baseline = store.stats()
    total_labeled_before = baseline["labeled"]
    total_inserted_before = baseline["total"]
    label_errors = 0
    print(f"[{stamp()}] [predictor] старт: {len(symbols)} монет, горизонт {HORIZON_SEC // 60} мин, "
          f"метка {LABEL_MOVE * 100:.2f}%, версия {'V2 (20 фич + SMC)' if USE_V2 else 'V1 (12 фич)'}, "
          f"таблица {TABLE}, модель {'загружена' if ever_trained else 'не обучена'}, "
          f"обучение при >= {MIN_LABELS_TO_TRAIN} размеченных, далее каждые {RETRAIN_EVERY // 60} мин")
    while True:
        t0 = time.monotonic()
        now = time.time()
        scan_no += 1

        labeled_rows = store.due_for_label(now, limit=MAX_BATCH)
        new_labels = 0
        for row_id, symbol, snap_price in labeled_rows:
            try:
                ticker = client.fetch_ticker(symbol)
                future_price = float(ticker.get("last") or 0.0)
                if future_price <= 0:
                    continue
                move = (future_price - snap_price) / snap_price if snap_price else 0.0
                label = 1 if move >= LABEL_MOVE else 0 if move <= -LABEL_MOVE else None
                if label is not None:
                    store.set_label(row_id, label, future_price)
                    new_labels += 1
            except Exception:
                label_errors += 1

        if len(symbols) > 0:
            try:
                loop = asyncio.get_running_loop()
                rows = await asyncio.gather(
                    *(loop.run_in_executor(None, snapshot_symbol, client, symbol) for symbol in symbols),
                    return_exceptions=True,
                )
            except Exception as exc:
                rows = [exc]
            for symbol, result in zip(symbols, rows):
                if isinstance(result, Exception) or result is None:
                    continue
                price, orderbook, klines = result
                if not orderbook.get("ready"):
                    continue
                if USE_V2:
                    features = dom_feature_vector_v2(orderbook, price, klines)
                else:
                    features = dom_feature_vector(orderbook, price)
                store.insert(symbol, now, price, features, now + HORIZON_SEC)

        stats = store.stats()
        new_total = stats["total"] - total_inserted_before
        total_inserted_before = stats["total"]
        new_lab = stats["labeled"] - total_labeled_before
        total_labeled_before = stats["labeled"]

        train_result = None
        first_due = stats["labeled"] >= MIN_LABELS_TO_TRAIN and not ever_trained
        retrain_due = ever_trained and time.time() - last_train >= RETRAIN_EVERY
        if first_due or retrain_due:
            rows, _ = store.labeled(min_samples=50)
            if rows:
                train_result = predictor.train(rows)
                if train_result.get("status") == "ok":
                    ever_trained = True
                    last_train = time.time()
                    train_result["paper"] = _paper_digest(predictor, store)
            else:
                train_result = {"status": "skipped", "reason": "нет размеченных строк"}

        duration = time.monotonic() - t0
        train_info = ""
        if train_result:
            if train_result.get("status") == "ok":
                paper = train_result.get("paper")
                paper_s = f", бумага {paper['accuracy']:.0%} на {paper['count']} примерах" if paper else ""
                train_info = (f" | ОБУЧЕНИЕ OK: {train_result['samples']} примеров, "
                              f"валь.точность {train_result.get('val_acc', 0):.0%}{paper_s}")
            else:
                train_info = f" | обучение пропущено: {train_result.get('reason', '?')}"
        elif not ever_trained:
            need = MIN_LABELS_TO_TRAIN - stats["labeled"]
            train_info = f" | до 1-го обучения: {max(need, 0)} меток"
        else:
            left = max(0.0, last_train + RETRAIN_EVERY - time.time())
            train_info = f" | переобучение через {left / 60:.1f} мин"
        warn = f" (ошибок разметки: {label_errors})" if label_errors else ""
        print(f"[{stamp()}] [predictor] скан {scan_no}: всего {stats['total']} (+{new_total}), "
              f"размечено {stats['labeled']} (+{new_lab}), цикл {duration:.1f}с{warn}{train_info}")
        if scan_no % 15 == 0 and ever_trained:
            _paper_digest(predictor, store)
        await asyncio.sleep(max(1, SCAN_INTERVAL - duration))


def snapshot_symbol(client, symbol: str):
    klines = client.fetch_klines(symbol, "1m", limit=300)
    if not klines:
        return None
    orderbook = indicator1_snapshot(klines, source_timeframe="1m")
    price = float(klines[-1][4])
    return price, orderbook, klines


def _paper_digest(predictor, store) -> dict:
    """Бумажная оценка: сколько последних размеченных снипсетов модель угадала бы."""
    recent, total = store.labeled(min_samples=30, cap=300)
    if total < 30:
        return None
    correct = 0
    count = 0
    up = 0
    for _symbol, _price, features_json, label in recent:
        pred = predictor.predict(json.loads(features_json))
        guess = 1 if pred > 0.5 else 0
        correct += int(guess == int(label))
        count += 1
        up += int(label)
    accuracy = correct / max(count, 1)
    baseline = up / max(count, 1)
    print(f"[{stamp()}] [predictor] бумага на последних {count} примерах: точность {accuracy:.0%} "
          f"(угадано {correct} из {count}, baseline «всегда вверх» {baseline:.0%})")
    return {"accuracy": accuracy, "count": count, "baseline": baseline}


if __name__ == "__main__":
    sys.stdout = _Tee()
    sys.stderr = sys.stdout
    if not _acquire_lock():
        raise SystemExit(0)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("[predictor] остановлен")
