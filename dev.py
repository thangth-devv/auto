"""Restart the desktop app when a development Python file changes."""

import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
WATCHED_FILES = (
    PROJECT_ROOT / "app.py",
    PROJECT_ROOT / "auto_buy.py",
    PROJECT_ROOT / "player_insert.py",
)
POLL_INTERVAL_SECONDS = 0.5
RESTART_DELAY_SECONDS = 0.2


def file_mtimes():
    return {
        path: path.stat().st_mtime_ns
        for path in WATCHED_FILES
        if path.exists()
    }


def start_app():
    return subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "app.py")],
        cwd=PROJECT_ROOT,
    )


def stop_app(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main():
    print("Dev hot reload is running.")
    print("Watching: app.py, auto_buy.py, player_insert.py")
    print("Press Ctrl+C to stop.")

    process = start_app()
    known_mtimes = file_mtimes()
    try:
        while True:
            time.sleep(POLL_INTERVAL_SECONDS)
            current_mtimes = file_mtimes()
            if current_mtimes == known_mtimes:
                if process.poll() is not None:
                    process = start_app()
                continue

            known_mtimes = current_mtimes
            print("Code changed; restarting the app...")
            stop_app(process)
            time.sleep(RESTART_DELAY_SECONDS)
            process = start_app()
    except KeyboardInterrupt:
        print("\nStopping dev hot reload...")
    finally:
        stop_app(process)


if __name__ == "__main__":
    main()
