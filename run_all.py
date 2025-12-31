from __future__ import annotations

import signal
import subprocess
import sys
import time


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    python = sys.executable
    api_proc = subprocess.Popen([python, "run_local.py"])
    bot_proc = subprocess.Popen([python, "run_telegram_bot.py"])

    try:
        while True:
            api_code = api_proc.poll()
            bot_code = bot_proc.poll()
            if api_code is not None:
                _terminate(bot_proc)
                return api_code
            if bot_code is not None:
                _terminate(api_proc)
                return bot_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        _terminate(api_proc)
        _terminate(bot_proc)
        return 0


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.default_int_handler)
    raise SystemExit(main())
