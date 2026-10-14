"""Tier-2 runner: real local model against offline fixture stubs.

- Stub tools return ONLY fixture data (no network except the model call).
- Model endpoint allowlist: loopback by default + EVAL_ALLOW_HOSTS.
  Anything else raises before any HTTP (T13 for the harness path).
- ReAct loop: model emits ```tool <name> {json}``` fences and finishes
  with ```record {Appendix-C JSON}```. Transcripts + tool calls + record
  land in tests/eval/runs/<scenario>/<utc>/ (gitignored).
- CI never runs this (needs EVAL_LIVE=1). On demand + pre-release.

Usage:
    EVAL_LIVE=1 py tests/eval/harness.py --scenario b09 --runs 1
    EVAL_MODEL=google/gemma-4-26b-a4b-qat EVAL_LIVE=1 py tests/eval/harness.py --all
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent.parent))

from tests.eval import checks as checks  # noqa: E402

TOOL_RE = re.compile(r"```tool\s+([a-z0-9_]+)\s+(.*?)```", re.DOTALL | re.IGNORECASE)
RECORD_RE = re.compile(r"```record\s+(.*?)```", re.DOTALL | re.IGNORECASE)


def base_url() -> str:
    return os.getenv("EVAL_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")


def allow_hosts() -> set[str]:
    extra = {h.strip().lower() for h in os.getenv("EVAL_ALLOW_HOSTS", "").split(",") if h.strip()}
    return {"127.0.0.1", "localhost", "::1"} | extra


def assert_allowed(url: str) -> str:
    """T13: only POST <endpoint>/chat/completions may be called. Raises."""
    from urllib.parse import urlparse

    base = base_url()
    if not url.startswith(base + "/"):
        raise RuntimeError(f"harness: URL outside model endpoint: {url}")
    want = urlparse(base).path.rstrip("/") + "/chat/completions"
    if urlparse(url).path.rstrip("/") != want:
        raise RuntimeError(f"harness: only {want} may be called")
    host = (urlparse(url).hostname or "").lower()
    if host not in allow_hosts():
        raise RuntimeError(f"harness: host not allowlisted: {host}")
    return url


def chat(messages: list[dict], model: str, timeout: int) -> str:
    import requests

    url = assert_allowed(base_url() + "/chat/completions")
    resp = requests.post(url, json={"model": model, "messages": messages,
                                    "temperature": 0.2, "max_tokens": 800,
                                    "stream": False},
                         timeout=(10, timeout))
    resp.raise_for_status()
    data = resp.json()
    return str((data.get("choices") or [{}])[0].get("message", {}).get("content", ""))


def default_model() -> str:
    if os.getenv("EVAL_MODEL"):
        return str(os.getenv("EVAL_MODEL"))
    import requests

    base = base_url().rstrip("/")
    from urllib.parse import urlparse
    if (urlparse(base).hostname or "").lower() not in allow_hosts():
        raise RuntimeError("harness: model endpoint not allowlisted")
    resp = requests.get(base + "/models", timeout=10)
    resp.raise_for_status()
    ids = [m.get("id", "") for m in resp.json().get("data", []) if m.get("id")]
    if not ids:
        raise RuntimeError("harness: no models served")
    return ids[0]


def system_prompt(tools: dict, capabilities: dict) -> str:
    lines = [
        "You are a portfolio decision-support assistant. Answer the user's",
        "question, then close with a fenced Appendix-C decision record:",
        "```record {...json...}``` with keys: id, mode, asset{...},",
        "portfolio_status, verdict (one of BUY HOLD ADD TRIM SELL WATCH AVOID",
        "INSUFFICIENT DATA), confidence, horizon, data_asof{price,...}.",
        "Rules: every number needs a tool source; never invent data;",
        "never claim trades, alerts or monitoring;",
        f"capabilities: {json.dumps(capabilities, ensure_ascii=False)}.",
    ]
    if tools:
        lines.append("Tools (call at most a few times, fenced ```tool <name> {json}```):")
        for name, spec in tools.items():
            lines.append(f"- {name}: {spec.get('description', '')}")
    return "\n".join(lines)


def run_once(scenario: dict, model: str, timeout: int, max_turns: int) -> dict:
    """One ReAct episode. Returns {transcript, tool_calls, record, raw_record}."""
    tools = scenario.get("tools", {})
    capabilities = scenario.get("capabilities", {"image_capable": False})
    messages = [{"role": "system",
                 "content": system_prompt(tools, capabilities)},
                {"role": "user", "content": scenario["prompt"]}]
    transcript = [f"### system\n{ messages[0]['content']}\n",
                  f"### user\n{scenario['prompt']}\n"]
    tool_calls: list[dict] = []
    record: dict = {}
    for _ in range(max(1, max_turns)):
        reply = None
        last_exc: str = ""
        for attempt in (1, 2):  # one retry with backoff (§1.5)
            try:
                reply = chat(messages, model, timeout)
                break
            except Exception as exc:
                last_exc = f"{type(exc).__name__}: {exc}"
                if attempt == 1:
                    import time as _time

                    _time.sleep(10)
        if reply is None:
            transcript.append(f"### transport-error\n{last_exc}\n")
            break
        transcript.append(f"### assistant\n{reply}\n")
        messages.append({"role": "assistant", "content": reply})
        for name, blob in TOOL_RE.findall(reply):
            spec = tools.get(name)
            if spec is None:
                feedback = {"error": f"unknown tool {name}"}
            else:
                try:
                    args = json.loads(blob or "{}")
                except ValueError:
                    args = {}
                try:
                    feedback = spec["handler"](args)
                except Exception as exc:
                    feedback = {"error": f"{type(exc).__name__}"}
            tool_calls.append({"tool": name, "args": blob[:500], "result": feedback})
            messages.append({"role": "user",
                             "content": f"tool {name} result: {json.dumps(feedback, ensure_ascii=False)[:2000]}"})
        found = RECORD_RE.findall(reply)
        if found:
            try:
                record = json.loads(found[-1])
            except ValueError:
                record = {"_parse_error": found[-1][:500]}
            break
    return {"transcript": "\n".join(transcript), "tool_calls": tool_calls,
            "record": record if isinstance(record, dict) else {}}


def evaluate(scenario: dict, outcome: dict) -> dict:
    """Run the scenario's mechanical checks. Returns {name: {ok, details}}."""
    answer = "\n".join(line for line in outcome["transcript"].splitlines()
                       if not line.startswith("### "))
    record = outcome["record"]
    calls = outcome["tool_calls"]
    results = {}
    for check in scenario.get("checks", []):
        name = check.get("check", "")
        try:
            if name == "number_provenance":
                results[name] = checks.number_provenance(
                    answer, check.get("tool_outputs", []),
                    check.get("calc_outputs", []),
                    check.get("prompt_numbers", []))
            elif name == "outbound_leak":
                results[name] = checks.outbound_leak(calls, check.get("secrets", []))
            elif name == "no_forbidden_verdict":
                results[name] = checks.no_forbidden_verdict(record, check.get("held", False))
            elif name == "no_execution_claims":
                results[name] = checks.no_execution_claims(answer)
            elif name == "verdict_token":
                results[name] = checks.verdict_token(record)
            elif name == "as_of_present":
                results[name] = checks.as_of_present(answer, record)
            elif name == "chart_claim":
                results[name] = checks.chart_claim(
                    answer, check.get("image_capable", False))
            elif name == "verdict_equals":
                results[name] = checks.verdict_equals(record, check.get("expected", ""))
            elif name == "slovak_format":
                results[name] = checks.slovak_format(answer)
            else:
                results[name] = {"ok": False, "details": {"unknown": name}}
        except Exception as exc:
            results[name] = {"ok": False, "details": {"error": type(exc).__name__}}
    return results


def run_scenario(scenario: dict, runs: int, model: str, timeout: int,
                 max_turns: int) -> dict:
    """Run N episodes, persist transcripts, return the summary row."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = HERE / "runs" / scenario["id"] / stamp
    dest.mkdir(parents=True, exist_ok=True)
    episodes = []
    for i in range(max(1, runs)):
        outcome = run_once(scenario, model, timeout, max_turns)
        checks_out = evaluate(scenario, outcome)
        (dest / f"run{i + 1}_transcript.md").write_text(
            outcome["transcript"][:60000], encoding="utf-8")
        (dest / f"run{i + 1}_checks.json").write_text(
            json.dumps({"record": outcome["record"], "tool_calls": outcome["tool_calls"],
                        "checks": checks_out}, indent=2, ensure_ascii=False),
            encoding="utf-8")
        episodes.append({"ok": all(v.get("ok", False) for v in checks_out.values()),
                         "checks": checks_out})
    passed = sum(1 for e in episodes if e["ok"])
    return {"id": scenario["id"], "critical": bool(scenario.get("critical")),
            "runs": len(episodes), "passed": passed,
            "pass_rate": passed / len(episodes), "path": str(dest),
            "model": model}


def write_report(rows: list[dict], model: str) -> Path:
    dest = HERE / "REPORT.md"
    lines = ["# Tier-2 eval report", "",
             f"Model: `{model}` | generated (UTC): "
             f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} | "
             f"runs carry transcripts under `tests/eval/runs/`.", "",
             "| Scenario | Critical | Runs | Passed | Rate | Transcript |",
             "|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['id']} | {row['critical']} | {row['runs']} | "
                     f"{row['passed']} | {row['pass_rate']:.0%} | `{row['path']}` |")
    lines += ["",
              "Policy: a critical failure blocks release; below 4/5 needs a "
              "prompt fix or a documented waiver."]
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def main(argv: list[str] | None = None) -> int:
    import os as _os

    if _os.getenv("EVAL_LIVE") != "1":
        print("Tier-2 needs EVAL_LIVE=1 (local model, on demand only).")
        return 2
    from tests.eval.scenarios_b import SCENARIOS

    ap = argparse.ArgumentParser(description="Tier-2 eval runner.")
    ap.add_argument("--scenario", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--model", default="")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--max-turns", type=int, default=6)
    args = ap.parse_args(argv)

    if args.list:
        for sid, spec in sorted(SCENARIOS.items()):
            print(f"{sid} critical={spec['critical']}")
        return 0
    wanted = sorted(SCENARIOS) if args.all else [args.scenario]
    try:
        model = args.model or default_model()
    except Exception as exc:
        print(f"ERROR: no model available: {type(exc).__name__}: {exc}")
        return 1
    print(f"Tier-2 model: {model} @ {base_url()}")
    rows = []
    for sid in wanted:
        spec = SCENARIOS.get(sid)
        if spec is None:
            print(f"ERROR: unknown scenario {sid!r}")
            return 1
        rows.append(run_scenario(spec, args.runs, model, args.timeout,
                                 args.max_turns))
        row = rows[-1]
        print(f"{sid}: {row['passed']}/{row['runs']} passed")
    print(f"report: {write_report(rows, model)}")
    if any(r["critical"] and r["passed"] < r["runs"] for r in rows):
        print("BLOCKED: critical scenario failure")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
