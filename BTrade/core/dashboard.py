import os
import sys
import time

from core.reporter import STRATEGY_LABELS, fmt_price

if os.name == "nt":
    try:
        import ctypes

        _kernel32 = ctypes.windll.kernel32
        _handle = _kernel32.GetStdHandle(-11)
        _mode = ctypes.c_uint32()
        if _kernel32.GetConsoleMode(_handle, ctypes.byref(_mode)):
            _kernel32.SetConsoleMode(_handle, _mode.value | 0x0004)
    except Exception:
        pass

C_RESET = "\x1b[0m"
C_GREEN = "\x1b[32m"
C_RED = "\x1b[31m"
C_CYAN = "\x1b[36m"
C_YELLOW = "\x1b[33m"
C_BOLD = "\x1b[1m"


def _color(value, positive_is_green=True):
    if value > 0:
        return f"{C_GREEN}{value:+.2f}{C_RESET}" if positive_is_green else f"{C_RED}{value:+.2f}{C_RESET}"
    if value < 0:
        return f"{C_RED}{value:+.2f}{C_RESET}" if positive_is_green else f"{C_GREEN}{value:+.2f}{C_RESET}"
    return f"{value:+.2f}"


SLEEP_ART = [
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⣀⡀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠠⠤⠤⠤⣤⠀⠀⠀⠀⡼⠁⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢤⡄⠀⢀⡤⠔⠊⠁⠀⠀⠀⣰⠃⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⣤⣤⣤⣤⣤⣤⣤⣤⣀⣀⣀⠀⠀⠀⠚⠂⠀⠉⠒⠢⠤⠤⠄⠀⡰⠃⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣤⣶⠿⠟⠛⠛⠋⠉⠉⠉⠉⠉⠉⠛⠛⠛⠷⢷⣦⣤⣀⡀⠀⠀⠀⠀⠀⠀⠙⠛⠓⠓⠒⠒⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⣠⣤⣴⣶⣶⣾⠟⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠙⠻⣿⣿⣶⣶⣶⣤⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⣴⣿⠟⠉⠀⠀⠙⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠟⠀⠀⠀⠉⠙⢿⣦⠀⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⣠⣿⡟⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢦⣽⣿⡄⠀⠀⠀⠀⠀",
    "⠀⠀⠀⣰⣿⠏⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⣿⣷⠀⠀⠀⠀⠀",
    "⠀⠀⢰⣿⡏⣤⠀⠀⠀⠀⠀⢀⡼⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣸⡀⠀⠀⢤⢠⣼⡇⠀⠀⠀⠀⠀",
    "⠀⠀⠀⢿⣿⠁⠀⠀⠀⠀⣴⡾⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⣇⠀⠀⠈⣇⣿⣿⠀⠀⠀⠀⠀",
    "⠀⠀⠀⢸⣿⠀⡀⣀⠀⢠⣿⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢻⠀⢠⣠⣿⣿⠇⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠈⢿⣷⣇⣽⠀⢈⡏⠀⠀⠀⠀⣀⣤⣤⣤⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣤⣴⣦⣤⠀⠀⠀⠀⠀⣿⣿⣧⣾⣿⠇⠀⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠈⠛⠿⣿⣧⣾⣿⡄⠀⠀⠀⠙⠿⠿⠿⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠛⠛⠛⠋⠀⠀⠀⠀⠀⢸⣿⡿⠋⠁⠀⠀⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⣿⡇⣴⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣤⣤⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠐⢶⣼⣿⣀⣠⣤⣤⣤⣀⠀⠀⠀⠀⠀",
    "⠀⠀⣠⣶⣾⠿⠛⠛⠻⢷⣿⣿⠁⠀⠀⠀⠀⠀⠀⠀⠀⣼⣿⣿⣿⣿⡆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⣿⣿⡿⠋⠉⠉⠉⠛⢿⣦⡀⠀⠀",
    "⢀⣾⡿⠋⠀⠀⠀⠀⠀⠀⠙⣿⡆⢀⠀⠀⠀⠀⠀⠀⠀⠘⢿⣿⣿⠟⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣤⣿⡟⠀⠀⠀⠀⠀⠀⠀⠀⠹⣿⡇⠀",
    "⣼⡿⠁⠀⠀⠀⠀⠀⠀⠀⠀⣸⣷⣿⣷⣧⠀⢀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣄⠀⢠⡾⣠⣇⣠⣿⣿⣿⡇⠀⢀⠀⠀⠀⢀⠀⠀⢹⣷⠀",
    "⣿⣷⡀⠀⣷⠀⠀⠀⣼⣦⣴⣿⠏⠙⠻⠿⣷⡿⠷⣶⣶⡾⠿⠿⠷⢶⣶⣦⣤⣾⣿⣷⣿⣿⠿⠿⠛⠛⠙⠻⣿⣤⣾⣇⠀⢀⣸⡇⠀⠀⠀⠀",
    "⠘⢿⣿⣾⣿⣷⣴⣾⡿⠟⠋⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠛⠛⠛⠛⠋⠀",
]


class Dashboard:
    def __init__(self, enabled: bool = None):
        if enabled is None:
            enabled = sys.stdout.isatty()
        self.enabled = enabled

    def finish(self):
        if not self.enabled:
            return
        sys.stdout.write("\x1b[r\x1b[0J\x1b[H")
        sys.stdout.flush()

    def sleep_splash(self, wake_dt, cfg):
        """Статичная «заставка сна»: большая мордочка + расписание на 3 языках."""
        if not self.enabled:
            return
        if hasattr(cfg, "schedule_weekdays_only") and cfg.schedule_weekdays_only:
            wake_label = f"{wake_dt:%d.%m} {wake_dt.strftime('%A')}"
            uk_suffix = " | закриття позицій на сон СБ та НД"
            sk_suffix = " | zatváranie pozícií na spánok SO a NE"
            en_suffix = " | closing positions for sleep Sat & Sun"
        else:
            wake_label = f"{wake_dt:%d.%m} {wake_dt:%H:%M}"
            uk_suffix = sk_suffix = en_suffix = ""
        art = [line.rstrip() for line in SLEEP_ART]
        try:
            term_w = os.get_terminal_size().columns
        except Exception:
            term_w = 120
        if term_w >= max(len(line) for line in art) * 2 + 12:
            art = ["".join(ch * 2 for ch in line) for line in art]
            art = [line for line in art for _ in (0, 1)]
        art_w = max(len(line) for line in art)
        pad = max((term_w - art_w - 4) // 2, 4)
        lines = [f"{C_BOLD}{'═' * 44}{C_RESET}",
                 f"{C_BOLD}   БОТ СПИТ · проснётся {wake_label}{C_RESET}",
                 f"{C_BOLD}{'═' * 44}{C_RESET}"]
        lines.extend(" " * pad + line for line in art)
        lines += ["",
                  f"{C_CYAN}РОЗКЛАД РОБОТИ: {cfg.schedule_wake} – {cfg.schedule_sleep} "
                  f"({cfg.schedule_zone}){uk_suffix}{C_RESET}",
                  f"{C_CYAN}PRACOVNÝ ČAS: {cfg.schedule_wake} – {cfg.schedule_sleep} "
                  f"({cfg.schedule_zone}){sk_suffix}{C_RESET}",
                  f"{C_CYAN}WORK SCHEDULE: {cfg.schedule_wake} – {cfg.schedule_sleep} "
                  f"({cfg.schedule_zone}){en_suffix}{C_RESET}",
                  f" {C_YELLOW}новые входы не ищутся до пробуждения{C_RESET}"]
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()

    def render(self, scan_no: int, risk, engine):
        if not self.enabled:
            return
        lines = []
        sep = "=" * 64
        lines.append(f"{C_CYAN}{sep}{C_RESET}")
        lines.append(f"{C_BOLD} OMNI TRADING BOT — ПАНЕЛЬ ПОЗИЦИЙ · скан №{scan_no} · {time.strftime('%H:%M:%S')}{C_RESET}")
        lines.append(f"{C_CYAN}{sep}{C_RESET}")

        open_pos = [p for p in engine.positions.values() if p.status == "open"]
        unreal = sum(engine.unrealized_pnl(p)[0] for p in open_pos)
        total = risk.equity + unreal
        killed = f" {C_RED}ДНЕВНОЙ ЛИМИТ УБЫТКА СРАБОТАЛ — новые входы заблокированы{C_RESET}" if risk.is_killed else ""
        lines.append(f" Капитал: {C_BOLD}{total:,.2f}{C_RESET} USDT "
                     f"(реализовано {risk.equity:,.2f} + открытые {_color(unreal)}) | день {_color(risk.day_pnl)}{killed}")

        if open_pos:
            lines.append(f" Открытые позиции ({len(open_pos)}):")
            for pos in open_pos:
                pnl, pct, sl_d, tp_d = engine.unrealized_pnl(pos)
                side = f"{C_GREEN}LONG{C_RESET}" if pos.side == "long" else f"{C_RED}SHORT{C_RESET}"
                strat = STRATEGY_LABELS.get(pos.strategy, pos.strategy)
                reason = (pos.features or {}).get("open_reason", "")
                last_trail = (pos.features or {}).get("last_trail", "")
                lines.append(f"   {pos.symbol:<14} {side}  {pos.qty:g} @ {fmt_price(pos.entry):>12}"
                             f"  [{strat}]  PnL {_color(pnl)} USDT ({pct:+.2f}%)"
                             f" | до SL {sl_d:.1f}% | выход трейлинг/стоп-лосс")
                if last_trail:
                    lines.append(f"      последний трейлинг: {last_trail}")
                if reason:
                    lines.append(f"      причина: {reason[:90]}")
        else:
            lines.append(f" {C_YELLOW}Открытых позиций нет{C_RESET}")

        lines.append(f"{C_CYAN}{sep}{C_RESET}")

        try:
            term_lines = os.get_terminal_size().lines
        except Exception:
            term_lines = 50
        region_top = len(lines) + 1
        if region_top >= term_lines:
            sys.stdout.write("\x1b[2J\x1b[H" + "\n".join(lines) + "\n")
            sys.stdout.flush()
            return
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.write(f"\x1b[{region_top};{term_lines}r\x1b[{region_top};1H")
        sys.stdout.flush()
