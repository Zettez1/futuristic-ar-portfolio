"""Тесты расписания работы бота: окно wake/sleep по локальному времени."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import parse_hhmm, schedule_state

PASSED = 0
FAILED = []


def check(name, cond, detail=""):
    global PASSED
    if cond:
        PASSED += 1
        print(f"  ok: {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL: {name} {detail}")


def test_schedule():
    check("parse 09:00", parse_hhmm("09:00") == (9, 0))
    check("parse 20:30", parse_hhmm("20:30") == (20, 30))
    check("parse битый -> 09:00", parse_hhmm("xx") == (9, 0))

    check("активен 14:00", schedule_state(datetime(2026, 8, 14, 14, 0))[0] is True)
    check("активен 09:00 ровно", schedule_state(datetime(2026, 8, 14, 9, 0))[0] is True)
    check("активен 19:59", schedule_state(datetime(2026, 8, 14, 19, 59))[0] is True)

    st = schedule_state(datetime(2026, 8, 14, 8, 59, 59))
    check("неактивен 08:59", st[0] is False)
    check("проснётся сегодня в 09:00", st[1] == datetime(2026, 8, 14, 9, 0), str(st[1]))

    st = schedule_state(datetime(2026, 8, 14, 20, 30))
    check("неактивен 20:30 ровно", st[0] is False)
    check("проснётся завтра в 09:00", st[1] == datetime(2026, 8, 15, 9, 0), str(st[1]))

    st = schedule_state(datetime(2026, 8, 14, 23, 59))
    check("спит ночью", st[0] is False and st[1] == datetime(2026, 8, 15, 9, 0), str(st))

    st = schedule_state(datetime(2026, 8, 15, 0, 1))
    check("после полуночи: сон до 09:00", st[0] is False and st[1] == datetime(2026, 8, 15, 9, 0), str(st))

    check("кастомное окно 10:00-15:00: активен",
          schedule_state(datetime(2026, 8, 14, 12, 0), "10:00", "15:00")[0] is True)
    check("кастомное окно: неактивен после",
          schedule_state(datetime(2026, 8, 14, 16, 0), "10:00", "15:00")[0] is False)


def test_all():
    print("== расписание работы ==")
    test_schedule()
    print(f"\n{PASSED} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed:", FAILED)
        sys.exit(1)


if __name__ == "__main__":
    test_all()