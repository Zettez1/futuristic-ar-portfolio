#!/usr/bin/env python3
"""
FastStart Digital portfolio — autonomous Railway deploy via official GraphQL API v2.

Endpoint: https://backboard.railway.com/graphql/v2
Auth:     Bearer token from $RAILWAY_TOKEN (or --token)

Pipeline:
  1. projectCreate       - create project (idempotent: reuse by name)
  2. environments        - resolve default (production) environment
  3. serviceCreate       - service linked to GitHub repo source
  4. deploymentTriggerCreate - auto-deploy on push to branch (main)
  5. serviceDomainCreate - public *.up.railway.app domain
  6. serviceInstanceDeployV2 - trigger deploy
  7. poll deployment     - wait until SUCCESS / FAILED
  8. print final URL

Optional --push: init local git, create GitHub repo via REST API
($GITHUB_TOKEN or git credential store) and push the code.

Usage:
  set RAILWAY_TOKEN=your_token
  python deploy_railway.py [--repo Zettez1/futuristic-ar-portfolio] [--push] [--wait 600]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

API_URL = "https://backboard.railway.com/graphql/v2"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

DEFAULT_PROJECT = "futuristic-ar-portfolio"
DEFAULT_SERVICE = "web-frontend"
DEFAULT_REPO = os.environ.get("GITHUB_REPO", "Zettez1/futuristic-ar-portfolio")
DEFAULT_BRANCH = "main"

RED = "\033[91m"; GREEN = "\033[92m"; CYAN = "\033[96m"; YELLOW = "\033[93m"; DIM = "\033[2m"; RESET = "\033[0m"

# ------------------------------------------------------------------ utils --


def log(msg: str, color: str = "", end="\n"):
    print(f"{color}{msg}{RESET}", end=end, flush=True)


def gql(token: str, query: str, variables: dict | None = None) -> dict:
    """Execute GraphQL query, raise on errors, return `data`."""
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API_URL, data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,
            "Origin": "https://railway.com",
            "Referer": "https://railway.com/",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        raw = resp.read().decode()
        status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        status = e.code
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"{RED}FATAL: network error: {e}{RESET}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise SystemExit(f"{RED}FATAL: non-JSON response (HTTP {status}): {raw[:400]}{RESET}")

    if data.get("errors"):
        msgs = "; ".join(e.get("message", "?") for e in data["errors"])
        raise SystemExit(f"{RED}GraphQL error: {msgs}{RESET}")
    return data.get("data") or {}


def run_git(args: list[str], stdin: str | None = None, check: bool = True):
    p = subprocess.run(["git", *args], input=stdin, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit(f"{RED}git {' '.join(args)} failed: {p.stderr.strip()[:400]}{RESET}")
    return p


# ------------------------------------------------------------- GitHub part --


def github_token() -> str | None:
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok.strip()
    p = run_git(["credential", "fill"], stdin="protocol=https\nhost=github.com\n\n", check=False)
    if p.returncode == 0:
        for line in p.stdout.splitlines():
            if line.startswith("password="):
                return line[len("password="):].strip()
    return None


def github_api(token: str, method: str, path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        data=json.dumps(payload).encode() if payload else None,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "User-Agent": "faststart-deploy", "Content-Type": "application/json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        raise SystemExit(
            f"{RED}GitHub API {method} {path} failed ({e.code}): "
            f"{e.read().decode()[:300]}{RESET}"
        )


def sync_github_repo(repo: str, token: str, branch: str) -> None:
    owner, name = repo.split("/", 1)

    log(f"\n[CYAN]GitHub: ensuring repository {repo}…{RESET}")
    existing = github_api(token, "GET", f"/repos/{repo}")
    if not existing:
        log(f"{DIM}  creating private repo {name}…{RESET}")
        github_api(token, "POST", "/user/repos",
                   {"name": name, "description": "Futuristic WebAR portfolio (FastStart Digital)", "private": True})
        time.sleep(2)

    if not os.path.isdir(".git"):
        log(f"{DIM}  git init…{RESET}")
        run_git(["init", "-b", branch])
        run_git(["config", "user.email", "deploy@faststart.digital"])
        run_git(["config", "user.name", "FastStart Deploy"])

    log(f"{DIM}  staging & committing…{RESET}")
    run_git(["add", "-A"])
    status = run_git(["status", "--porcelain"], check=False)
    if status.stdout.strip():
        run_git(["commit", "-m", "feat: futuristic AR portfolio site", "--allow-empty"])

    log(f"{DIM}  pushing to github.com/{repo} ({branch})…{RESET}")
    url = f"https://x-access-token:{token}@github.com/{repo}.git"
    run_git(["remote", "remove", "origin"], check=False)
    run_git(["remote", "add", "origin", url])
    run_git(["push", "-u", "origin", branch, "-f", "--quiet"])
    run_git(["remote", "set-url", "origin", f"https://github.com/{repo}.git"])
    log(f"{GREEN}  pushed.{RESET}")

    # grant Railway's GitHub App access to the repo so deployments can read it
    try:
        insts = github_api(token, "GET", "/user/installations")
        for inst in (insts.get("installations") or []):
            if "railway" in (inst.get("app_slug") or "").lower():
                repo_id = github_api(token, "GET", f"/repos/{repo}").get("id")
                if repo_id:
                    github_api(token, "PUT", f"/user/installations/{inst['id']}/repositories/{repo_id}")
                    log(f"{GREEN}  Railway GitHub App granted access to {repo}{RESET}")
                break
    except SystemExit:
        log(f"{YELLOW}  could not auto-grant Railway access; grant it in repo Settings > Access{RESET}")


# ------------------------------------------------------------ Railway part --


def find_project(token: str, name: str, workspace_id: str) -> str | None:
    d = gql(token, """query($wid: String!) {
      projects(workspaceId: $wid) { edges { node { id name } } }
    }""", {"wid": workspace_id})
    for e in (d.get("projects") or {}).get("edges") or []:
        if e["node"]["name"] == name:
            return e["node"]["id"]
    return None


def find_environment(token: str, project_id: str) -> str | None:
    d = gql(token, """query($pid: String!) {
      project(id: $pid) { environments { edges { node { id name } } } }
    }""", {"pid": project_id})
    envs = [e["node"] for e in (((d.get("project") or {}).get("environments")) or {}).get("edges") or []]
    for e in envs:
        if e["name"] == "production":
            return e["id"]
    return envs[0]["id"] if envs else None


def find_service(token: str, project_id: str, name: str) -> str | None:
    d = gql(token, """query($pid: String!) {
      project(id: $pid) { services { edges { node { id name } } } }
    }""", {"pid": project_id})
    for s in [e["node"] for e in (((d.get("project") or {}).get("services")) or {}).get("edges") or []]:
        if s["name"] == name:
            return s["id"]
    return None


def create_service(token: str, project_id: str, name: str, repo: str, branch: str) -> str:
    return gql(token, """mutation($input: ServiceCreateInput!) {
      serviceCreate(input: $input) { id name }
    }""", {"input": {"projectId": project_id, "name": name,
                     "source": {"repo": repo}, "branch": branch}})["serviceCreate"]["id"]


def create_domain(token: str, service_id: str, environment_id: str, target_port: int | None) -> str:
    inp = {"serviceId": service_id, "environmentId": environment_id}
    if target_port:
        inp["targetPort"] = target_port
    return gql(token, """mutation($input: ServiceDomainCreateInput!) {
      serviceDomainCreate(input: $input) { domain }
    }""", {"input": inp})["serviceDomainCreate"]["domain"]


def create_trigger(token: str, project_id: str, service_id: str, environment_id: str,
                   repo: str, branch: str) -> None:
    gql(token, """mutation($input: DeploymentTriggerCreateInput!) {
      deploymentTriggerCreate(input: $input) { id }
    }""", {"input": {"projectId": project_id, "serviceId": service_id,
                     "environmentId": environment_id, "provider": "github",
                     "repository": repo, "branch": branch}})


def trigger_deploy(token: str, service_id: str, environment_id: str) -> str:
    return gql(token, """mutation($sid: String!, $eid: String!) {
      serviceInstanceDeployV2(serviceId: $sid, environmentId: $eid)
    }""", {"sid": service_id, "eid": environment_id})["serviceInstanceDeployV2"]


def wait_deploy(token: str, deployment_id: str, timeout: int) -> dict:
    log(f"\n{YELLOW}Deployment {deployment_id}{RESET} started, polling…")
    start = time.time()
    last = None
    while time.time() - start < timeout:
        d = gql(token, """query($id: String!) {
          deployment(id: $id) { id status url createdAt staticUrl }
        }""", {"id": deployment_id})
        dep = d.get("deployment") or {}
        status = dep.get("status", "WAITING")
        if status != last:
            log(f"  [{GREEN if status == 'SUCCESS' else YELLOW if status in ('BUILDING','DEPLOYING','WAITING','QUEUED','INITIALIZING') else RED}{status}{RESET}] {DIM}{dep.get('url') or ''}{RESET}")
            last = status
        if status in ("SUCCESS", "FAILED", "CRASHED", "REMOVED", "SKIPPED", "NEEDS_APPROVAL"):
            return dep
        time.sleep(6)
    raise SystemExit(f"{RED}Timeout waiting for deployment.{RESET}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Deploy FastStart Digital portfolio to Railway via GraphQL API v2")
    ap.add_argument("--token", default=os.environ.get("RAILWAY_TOKEN"), help="Railway API token (or $RAILWAY_TOKEN)")
    ap.add_argument("--project", default=DEFAULT_PROJECT, help="Railway project name")
    ap.add_argument("--service", default=DEFAULT_SERVICE, help="Railway service name")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo 'owner/name' connected on Railway")
    ap.add_argument("--branch", default=DEFAULT_BRANCH, help="Git branch to deploy")
    ap.add_argument("--port", type=int, default=None, help="target port for generated domain")
    ap.add_argument("--push", action="store_true", help="create GitHub repo & push local code first")
    ap.add_argument("--no-domain", action="store_true", help="skip domain creation")
    ap.add_argument("--no-trigger", action="store_true", help="skip auto-deploy trigger creation")
    ap.add_argument("--wait", type=int, default=600, help="deploy polling timeout (s), 0 = skip")
    ap.add_argument("--json", action="store_true", help="machine-readable final output")
    args = ap.parse_args()

    token = (args.token or "").strip()
    if not token:
        raise SystemExit(f"{RED}No RAILWAY_TOKEN. Set env RAILWAY_TOKEN or pass --token.{RESET}")

    log(f"{CYAN}FastStart Digital Railway deploy script{RESET} · {DIM}GraphQL API v2 · backboard.railway.com{RESET}")
    log(f"1/8 Authenticating…")
    me = gql(token, "{ me { id email username workspaces { id name } } }")["me"]
    log(f"{GREEN}  OK{RESET} · {me.get('email')} {DIM}(@{me.get('username')}){RESET}")

    workspace_id = me["workspaces"][0]["id"]
    log(f"  workspace: {DIM}{me['workspaces'][0]['name']} ({workspace_id}){RESET}")

    if args.push:
        gtok = github_token()
        if not gtok:
            raise SystemExit(f"{RED}--push requires $GITHUB_TOKEN or stored git credentials.{RESET}")
        sync_github_repo(args.repo, gtok, args.branch)

    log(f"2/8 Project '{args.project}'…")
    project_id = find_project(token, args.project, workspace_id)
    if project_id:
        log(f"{GREEN}  reuse{RESET} {project_id}")
    else:
        d = gql(token, """mutation($input: ProjectCreateInput!) {
          projectCreate(input: $input) { id name }
        }""", {"input": {"workspaceId": workspace_id, "name": args.project,
                         "defaultEnvironmentName": "production",
                         "description": "Futuristic WebAR portfolio (FastStart Digital)"}})
        project_id = d["projectCreate"]["id"]
        log(f"{GREEN}  created{RESET} {project_id}")

    log(f"3/8 Environment…")
    environment_id = find_environment(token, project_id)
    if not environment_id:
        raise SystemExit(f"{RED}No environment in project.{RESET}")
    log(f"{GREEN}  OK{RESET} {environment_id}")

    log(f"4/8 Service '{args.service}' <- {args.repo}…")
    service_id = find_service(token, project_id, args.service)
    if service_id:
        log(f"{GREEN}  reuse{RESET} {service_id}")
    else:
        service_id = create_service(token, project_id, args.service, args.repo, args.branch)
        log(f"{GREEN}  created{RESET} {service_id}")

    if not args.no_trigger:
        log(f"5/8 Auto-deploy trigger…")
        try:
            create_trigger(token, project_id, service_id, environment_id, args.repo, args.branch)
            log(f"{GREEN}  OK{RESET} push to {args.branch} auto-deploys")
        except SystemExit:
            log(f"{YELLOW}  skipped (trigger may already exist){RESET}")

    domain = None
    if not args.no_domain:
        log("6/8 Public domain…")
        try:
            domain = create_domain(token, service_id, environment_id, args.port)
            log(f"{GREEN}  OK{RESET} https://{domain}")
        except SystemExit:
            log(f"{YELLOW}  domain already exists, skipping{RESET}")

    log(f"7/8 Deploy…")
    deployment_id = trigger_deploy(token, service_id, environment_id)
    log(f"{GREEN}  deployment {deployment_id}{RESET}")

    dep = {}
    if args.wait:
        dep = wait_deploy(token, deployment_id, args.wait)

    url = dep.get("url")
    if url:
        host = url.replace("https://", "").replace("http://", "").rstrip("/")
        port_str = f":{args.port}" if args.port else ""
        domain = f"{host}{port_str}" if host else domain
    final_url = f"https://{domain}" if domain else url

    log(f"\n{'='*62}")
    log(f"8/8 {GREEN}DEPLOY {'SUCCESS' if dep.get('status') == 'SUCCESS' else 'TRIGGERED'}{RESET}")
    log(f"  URL:     {CYAN}{final_url or '[domain pending]'}{RESET}")
    log(f"  Project: https://railway.com/project/{project_id}")
    log(f"  Service: https://railway.com/project/{project_id}/service/{service_id}")
    log(f"  Deploy:  {deployment_id}  status={dep.get('status', 'in progress')}")
    log(f"{'='*62}")

    if args.json:
        print(json.dumps({"url": final_url, "projectId": project_id, "serviceId": service_id,
                          "environmentId": environment_id, "deploymentId": deployment_id,
                          "status": dep.get("status", "TRIGGERED"), "domain": domain},
                         indent=2))


if __name__ == "__main__":
    main()