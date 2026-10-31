"""keys.py: the caption chain's stop contract and its one door to optional service keys. Stdlib only.

  EngineStop      the exception every script raises for a problem a person can fix; its message is the one plain line they read
  stop(msg)       print "STOP: <msg>" and exit 2: how a script that is not wrapped in cli() gives up
  cli(fn, usage)  run one script's main body under the stop contract: every failure is ONE "STOP: ..." line and exit 2, never a traceback.
                  JSONDecodeError is a ValueError, so the bad-input clause has to come first or a corrupt json file would be called a usage error
  get(name)       the value of NAME in ~/.cutroom/keys.env (CUTROOM_HOME moves the folder); EngineStop when it is missing or empty
  has(name)       True when get(name) would answer
  path()          the keys file

A key's value is never printed, logged or put in an error message: only its name and the file are.
"""
import json
import os
import sys


class EngineStop(Exception):
    """A required input, tool or service key is missing or unusable. The take stops and the message is shown to the person."""


def path():
    return os.path.join(os.path.expanduser(os.environ.get("CUTROOM_HOME") or "~/.cutroom"), "keys.env")


def get(name):
    p = path()
    if not os.path.exists(p):
        raise EngineStop(f"{name} is not set: there is no keys file at {p}")
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith(name + "="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
                break
    raise EngineStop(f"{name} is empty or missing in {p}")


def has(name):
    try:
        get(name)
    except EngineStop:
        return False
    return True


def stop(msg):
    """One plain `STOP: ...` line on stderr and exit 2."""
    print(f"STOP: {str(msg).strip()}", file=sys.stderr)
    sys.exit(2)


def cli(fn, usage):
    try:
        fn()
    except EngineStop as e:
        stop(e)
    except (OSError, json.JSONDecodeError) as e:
        stop(f"bad input: {type(e).__name__}: {e}")
    except (IndexError, ValueError):
        stop(f"usage: {usage}")
