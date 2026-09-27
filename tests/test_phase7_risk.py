"""Phase 7 risk: policy, eligibility gates, sizing, advisory plans, no-execution.

Offline only. No live API.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _row(display="AAPL", signal="BUY", weight=5.0, mapping="VERIFIED"):
    return {"display_symbol": display, "ticker": display, "signal": signal,
            "weight": weight, "pnl_pct": 3.0, "external_mapping_status": mapping,
            "quantity": 10.0, "market_value": 500.0}


def _policy(**over):
    base = {"policy_version": "v1-preliminary", "risk_per_trade_pct": 2.0,
            "adverse_move_assumption": 0.25,
            "concentration": {"default_max_position_pct": 20.0},
            "cash": {"mandatory_floor_pct": 0.0, "zero_cash_blocks_buys": True},
            "earnings_blackout": {"before_bdays": 5, "after_bdays": 2},
            "stops": {"scope": "tactical-only"}, "core_trims": True}
    base.update(over)
    return base


def test_policy_template_loads_and_missing_is_not_set(tmp_path):
    from investment_engine.risk import policy as pol

    ok = pol.load_policy(str(ROOT / "investment_policy.example.json"))
    assert ok["status"] == "OK" and ok["version"] == "v1-preliminary"
    missing = pol.load_policy(str(tmp_path / "nope.json"))
    assert missing["status"] == "NOT SET"
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    assert pol.load_policy(str(bad))["status"] == "INVALID"
    assert pol.is_deferred("REQUIRES_USER_VALUE")
    assert not pol.is_deferred(2.0)


def test_eligibility_matrix():
    from investment_engine.risk import eligibility as e

    assert not e.gate_recon("FAIL")["pass"] and e.gate_recon("PASS")["pass"]
    assert not e.gate_cash("BUY", 0.0)["pass"] and e.gate_cash("BUY", 1.0)["pass"]
    assert e.gate_cash("TRIM", 0.0)["pass"]
    assert not e.gate_concentration(25.0, 20.0)["pass"] and e.gate_concentration(5.0, 20.0)["pass"]
    earn = {"AAPL": ("2026-09-24", "confirmed")}
    assert not e.gate_blackout("AAPL", earn, "2026-09-24")["pass"]  # T+0 inside
    assert not e.gate_blackout("AAPL", earn, "2026-09-17")["pass"]  # 5bd before: inside
    assert e.gate_blackout("AAPL", earn, "2026-09-16")["pass"]  # 6bd before: clear
    assert e.gate_blackout("AAPL", earn, "2026-09-10")["pass"]  # well before
    assert not e.gate_blackout("AAPL", earn, "2026-09-28")["pass"]  # 2bd after: inside
    assert e.gate_blackout("AAPL", earn, "2026-09-29")["pass"]  # 3bd after: clear
    assert e.gate_blackout("MSFT", earn, "2026-09-24")["pass"]  # no date
    assert not e.gate_mapping("UNRESOLVED", True)["pass"]
    assert e.gate_mapping("UNRESOLVED", False)["pass"] and e.gate_mapping("VERIFIED", True)["pass"]
    ev = [{"ticker": "AAPL"}]
    assert e.gate_evidence("BUY", "AAPL", ev, "NEUTRAL", "NORMAL")["pass"]
    assert e.gate_evidence("BUY", "AAPL", [], "MUST_BUY", "NORMAL")["pass"]
    assert e.gate_evidence("BUY", "AAPL", [], "POTENTIAL_ACCUMULATION_ZONE", "CAUTIOUS")["pass"]
    assert not e.gate_evidence("BUY", "AAPL", [], "NEUTRAL", "NORMAL")["pass"]
    assert not e.gate_evidence("BUY", "AAPL", [{"ticker": "AAPL", "state": "SOCIAL"}],
                               "NEUTRAL", "NORMAL")["pass"]
    assert e.gate_evidence("TRIM", "AAPL", [], "NEUTRAL", "NORMAL")["pass"]
    assert not e.gate_sleeve_known(None)["pass"] and e.gate_sleeve_known("tactical")["pass"]


def test_sizing_math():
    from investment_engine.risk import sizing as s

    assert s.risk_cap_eur(10000.0, 2.0, 0.25) == 800.0
    out = s.indicative_notional_eur(weight_pct=5.0, cap_pct=20.0, total_equity=10000.0,
                                    risk_pct=2.0, adverse_move=0.25,
                                    sector_headroom_eur=5000.0, deployable_cash_eur=300.0)
    assert out["notional_eur"] == 300.0 and out["binding_constraint"] == "cash"
    out2 = s.indicative_notional_eur(weight_pct=19.0, cap_pct=20.0, total_equity=10000.0,
                                     risk_pct=2.0, adverse_move=0.25,
                                     sector_headroom_eur=5000.0, deployable_cash_eur=9000.0)
    assert out2["notional_eur"] == 100.0 and out2["binding_constraint"] == "position-cap"


def _decisions():
    return [{"ticker": "AAPL", "action": "BUY", "basis": ["canonical-signal"]}]


def test_build_plans_end_to_end_and_guards():
    from investment_engine.risk import order_plan as op

    rows = [_row()]
    bundle = op.build_plans(
        committee_decisions=_decisions(), portfolio_rows=rows, recon_status="PASS",
        free_cash=1000.0, total_equity=10000.0, earnings_status={},
        report_day="2026-09-24", news_events=[{"ticker": "AAPL"}], regime="NEUTRAL",
        risk_posture="NORMAL", policy=_policy(), sleeves={"AAPL": "tactical"})
    assert len(bundle["plans"]) == 1
    plan = bundle["plans"][0]
    assert plan["human_confirmation_required"] is True
    assert plan["direction"] == "BUY" and plan["indicative_notional_eur"] > 0
    assert all(g["pass"] or g["rule"] == "mapping-verified" for g in plan["policy_checks"])
    assert "Advisory only" in bundle["advisory_guard"]
    assert "not executed" in op.render(bundle)

    fail = op.build_plans(
        committee_decisions=_decisions(), portfolio_rows=rows, recon_status="FAIL",
        free_cash=1000.0, total_equity=10000.0, earnings_status={},
        report_day="2026-09-24", news_events=[{"ticker": "AAPL"}], regime="NEUTRAL",
        risk_posture="DEFENSIVE", policy=_policy(), sleeves={"AAPL": "tactical"})
    assert fail["plans"] == [] and len(fail["blocked"]) == 1

    core = op.build_plans(
        committee_decisions=[{"ticker": "AAPL", "action": "TRIM", "basis": []}],
        portfolio_rows=[_row(signal="SELL")], recon_status="PASS",
        free_cash=1000.0, total_equity=10000.0, earnings_status={},
        report_day="2026-09-24", news_events=[], regime="NEUTRAL",
        risk_posture="NORMAL", policy=_policy(core_trims="REQUIRES_USER_VALUE"),
        sleeves={"AAPL": "long_term"})
    assert core["plans"] == [] and "confirmation" in core["blocked"][0]["reason"]

    unknown = op.build_plans(
        committee_decisions=_decisions(), portfolio_rows=rows, recon_status="PASS",
        free_cash=1000.0, total_equity=10000.0, earnings_status={},
        report_day="2026-09-24", news_events=[{"ticker": "AAPL"}], regime="NEUTRAL",
        risk_posture="NORMAL", policy=_policy(), sleeves={"AAPL": "unknown"})
    assert unknown["plans"] == []


def test_no_execution_capability_in_risk_package():
    import re

    for path in (ROOT / "investment_engine" / "risk").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for pat in (r"\bimport requests\b", r"\bimport socket\b", r"\bsubprocess\b",
                    r"os\.system", r"place_order", r"submit_order", r"execute_order",
                    r"webbrowser", r"Trade212Client"):
            assert not re.search(pat, src), f"{path}:{pat}"


def test_manifest_policy_version(tmp_path):
    import json

    import portfolio_ai_assistant as entry

    result = {"brief_markdown": "", "snapshot_markdown": "",
              "reconciliation": {"status": "UNKNOWN"}, "failed_tickers": [],
              "monitoring_items": [], "decision_news": [], "brief_metadata": {},
              "investment_policy": {"status": "OK", "version": "v1-preliminary", "problems": []}}
    entry.write_reports(result, tmp_path / "o", tmp_path / "a", "p7")
    manifest = json.loads((tmp_path / "o" / "current" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["policy_version"] == "v1-preliminary"


def test_sleeve_mapping():
    from investment_engine.risk.order_plan import sleeve_for_group

    assert sleeve_for_group("TECH_PIE") == "thematic"
    assert sleeve_for_group("SHORT_TERM_TRADING") == "tactical"
    assert sleeve_for_group("LONG_RUN_DCA") == "long_term"
    assert sleeve_for_group("CRYPTO") == "crypto"
    assert sleeve_for_group("???") == "unknown"
