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

    fri = datetime(2026, 8, 14)   # пятница
    sat = datetime(2026, 8, 15)   # суббота
    sun = datetime(2026, 8, 16)   # воскресенье
    mon = datetime(2026, 8, 17)   # понедельник

    check("пятница 14:00 активен", schedule_state(fri.replace(hour=14))[0] is True)
    check("пятница 09:00 ровно активен", schedule_state(fri.replace(hour=9))[0] is True)
    check("пятница 19:59 активен", schedule_state(fri.replace(hour=19, minute=59))[0] is True)

    st = schedule_state(fri.replace(hour=8, minute=59, second=59))
    check("пятница 08:59 неактивен", st[0] is False)
    check("проснётся сегодня в 09:00", st[1] == fri.replace(hour=9), str(st[1]))

    st = schedule_state(fri.replace(hour=20, minute=30))
    check("пятница 20:30 неактивен", st[0] is False)
    check("выходные: сон до ПОНЕДЕЛЬНИКА 09:00", st[1] == mon.replace(hour=9), str(st[1]))

    st = schedule_state(fri.replace(hour=23, minute=59))
    check("пятница 23:59: сон до понедельника", st[0] is False and st[1] == mon.replace(hour=9), str(st))

    st = schedule_state(sat.replace(hour=10))
    check("суббота 10:00: спит", st[0] is False)
    check("суббота: до понедельника 09:00", st[1] == mon.replace(hour=9), str(st[1]))

    st = schedule_state(sun.replace(hour=14))
    check("воскресенье 14:00: спит", st[0] is False and st[1] == mon.replace(hour=9), str(st))

    st = schedule_state(sun.replace(hour=23, minute=59))
    check("воскресенье 23:59: спит до утра понедельника", st[0] is False and st[1] == mon.replace(hour=9), str(st))

    st = schedule_state(mon.replace(hour=0, minute=1))
    check("понедельник после полуночи: до 09:00", st[0] is False and st[1] == mon.replace(hour=9), str(st))
    check("понедельник 12:00 активен", schedule_state(mon.replace(hour=12))[0] is True)

    st = schedule_state(fri.replace(hour=20, minute=30), weekdays_only=False)
    check("без выходных: пятница 20:30 -> суббота 09:00", st[1] == sat.replace(hour=9), str(st[1]))
    check("без выходных: суббота 10:00 активен",
          schedule_state(sat.replace(hour=10), weekdays_only=False)[0] is True)

    check("кастомное окно 10:00-15:00: активен",
          schedule_state(fri.replace(hour=12), "10:00", "15:00")[0] is True)
    check("кастомное окно: неактивен после",
          schedule_state(fri.replace(hour=16), "10:00", "15:00")[0] is False)


def test_all():
    print("== расписание работы ==")
    test_schedule()
    print(f"\n{PASSED} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed:", FAILED)
        sys.exit(1)


if __name__ == "__main__":
    test_all()