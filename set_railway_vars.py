#!/usr/bin/env python3
"""Set/update Railway service variables (secrets) via GraphQL v2.

Reads key values from a local dotenv file and upserts them on the
web-frontend service, NEVER printing values to the console.

Usage:
  python set_railway_vars.py --token TOKEN --env BTrade/.env \
      --keys BINANCE_API_KEY BINANCE_SECRET [--plain BOT_DOTENV=.env.binance-real BOT_SCHEDULE=1]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

API_URL = "https://backboard.railway.com/graphql/v2"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

PROJECT = "futuristic-ar-portfolio"
SERVICE = "web-frontend"


def gql(token: str, query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": UA, "Origin": "https://railway.com",
                 "Referer": "https://railway.com/",
                 "Authorization": f"Bearer {token}"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        raw = resp.read().decode()
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
    data = json.loads(raw)
    if data.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "?") for e in data["errors"]))
    return data.get("data") or {}


def find_ids(token: str):
    me = gql(token, "{ me { workspaces { id } } }")["me"]
    wid = me["workspaces"][0]["id"]
    d = gql(token, """query($wid: String!) { projects(workspaceId: $wid) { edges { node { id name } } } }""",
            {"wid": wid})
    pid = next(e["node"]["id"] for e in d["projects"]["edges"] if e["node"]["name"] == PROJECT)
    d = gql(token, """query($pid: String!) { project(id: $pid) { services { edges { node { id name } } } } }""",
            {"pid": pid})
    sid = next(e["node"]["id"] for e in d["project"]["services"]["edges"] if e["node"]["name"] == SERVICE)
    d = gql(token, """query($pid: String!) { project(id: $pid) { environments { edges { node { id name } } } } }""",
            {"pid": pid})
    eid = next(e["node"]["id"] for e in d["project"]["environments"]["edges"] if e["node"]["name"] == "production")
    return pid, sid, eid


def load_env(path: str) -> dict:
    out = {}
    for line in open(path, encoding="utf-8").read().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def upsert(token: str, pid: str, sid: str, eid: str, key: str, value: str) -> None:
    q = """mutation($i: VariableUpsertInput!) {
      variableUpsert(input: $i) }"""
    inp = {"projectId": pid, "environmentId": eid, "serviceId": sid,
           "name": key, "value": value, "skipDeploys": True}
    try:
        gql(token, q, {"i": inp})
        print(f"  upserted {key}")
    except RuntimeError as e:
        raise SystemExit(f"{RED}variable upsert failed for {key}: {e}{RESET}")


RED = "\033[91m"; GREEN = "\033[92m"; CYAN = "\033[96m"; RESET = "\033[0m"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.environ.get("RAILWAY_TOKEN"))
    ap.add_argument("--env", help="local dotenv file to read secrets from")
    ap.add_argument("--keys", nargs="*", default=[], help="keys to copy from --env")
    ap.add_argument("--plain", nargs="*", default=[], help="literal KEY=VALUE pairs (non-secret)")
    args = ap.parse_args()

    token = (args.token or "").strip()
    if not token:
        raise SystemExit("No --token")
    envs = load_env(args.env) if args.env else {}
    pairs = {k: envs[k] for k in args.keys if k in envs and envs[k]}
    missing = [k for k in args.keys if k not in pairs]
    if missing:
        raise SystemExit(f"missing keys in {args.env}: {missing}")
    for pv in args.plain:
        k, _, v = pv.partition("=")
        pairs[k] = v

    print("resolving service ids…")
    pid, sid, eid = find_ids(token)
    print(f"  project {pid} service {sid} env {eid}")
    for k, v in pairs.items():
        upsert(token, pid, sid, eid, k, v)
    print(f"{GREEN}done: {len(pairs)} variables set (secrets not printed){RESET}")


if __name__ == "__main__":
    main()