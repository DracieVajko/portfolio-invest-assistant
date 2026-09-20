"""V4 hardening acceptance tests. Offline only: no network, no real secrets."""
import json
import logging
import shutil
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import v4_hardening as V4


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def minimal_config(**over):
    cfg = {
        "settings": {
            "buy_probability_threshold": 45,
            "sell_probability_threshold": 55,
            "ai_chain_alpha": ["alpha"],
            "ai_chain_summary": ["summary"],
        },
        "assets": [
            {"broker_symbol": "AAPL", "yahoo_symbol": "AAPL", "name": "Apple",
             "group": "TECH", "aliases": ["Apple"]},
            {"broker_symbol": "NVDA", "yahoo_symbol": "NVDA", "name": "Nvidia",
             "group": "TECH", "aliases": []},
        ],
        "ai_backends": {
            "alpha": {"provider": "ollama", "base_url": "http://127.0.0.1:11434",
                      "model": "m", "timeout": 10, "num_ctx": 1024, "temperature": 0.1,
                      "enabled": True},
            "summary": {"provider": "ollama", "base_url": "http://127.0.0.1:11434",
                        "model": "m", "timeout": 10, "num_ctx": 1024, "temperature": 0.1,
                        "enabled": True},
        },
    }
    cfg.update(over)
    return cfg


def fake_pos(raw="AAPL_US_EQ", clean="AAPL", qty=1.0, avg=100.0, cur=105.0,
             pnl=5.0, ccy="USD", value=105.0):
    return {"broker": "Trading212", "broker_symbol": raw,
            "broker_symbol_clean": clean, "quantity": qty,
            "average_price": avg, "current_price": cur, "market_value": value,
            "pnl": pnl, "pnl_pct": 5.0, "currency": ccy,
            "currencyCode": ccy}


# --------------------------------------------------------------------------
# 1. config validation
# --------------------------------------------------------------------------

class TestConfigValidation:
    def test_valid_config(self):
        errors, warnings = V4.validate_config_full(minimal_config(), env={})
        assert errors == []

    def test_duplicate_asset_id(self):
        cfg = minimal_config()
        cfg["assets"].append({"broker_symbol": "aapl", "yahoo_symbol": "AAPL", "name": "Dup"})
        errors, _ = V4.validate_config_full(cfg, env={})
        assert any("Duplicate" in e for e in errors)

    def test_malformed_asset(self):
        cfg = minimal_config()
        cfg["assets"][0] = {"broker_symbol": "X"}
        errors, _ = V4.validate_config_full(cfg, env={})
        assert any("yahoo_symbol" in e for e in errors)
        assert any("name" in e for e in errors)

    def test_missing_settings_and_assets(self):
        errors, _ = V4.validate_config_full({"foo": 1}, env={})
        assert any("settings" in e for e in errors)
        assert any("assets" in e for e in errors)

    def test_invalid_thresholds_and_timeouts(self):
        cfg = minimal_config()
        cfg["settings"]["buy_probability_threshold"] = 150
        cfg["settings"]["some_timeout"] = -3
        errors, _ = V4.validate_config_full(cfg, env={})
        assert any("buy_probability_threshold" in e for e in errors)
        assert any("some_timeout" in e for e in errors)

    def test_ambiguous_alias(self):
        cfg = minimal_config()
        cfg["assets"][1]["aliases"] = ["Apple"]
        errors, _ = V4.validate_config_full(cfg, env={})
        assert any("Ambiguous alias" in e for e in errors)

    def test_broken_chain_reference(self):
        cfg = minimal_config()
        cfg["settings"]["ai_chain_alpha"] = ["nope"]
        errors, _ = V4.validate_config_full(cfg, env={})
        assert any("nope" in e for e in errors)

    def test_missing_env_for_cloud_backend(self):
        cfg = minimal_config()
        cfg["ai_backends"]["openrouter"] = {
            "provider": "openrouter", "base_url": "https://openrouter.ai/api/v1",
            "model": "openrouter/free", "api_key": "${OPENROUTER_API_KEY}",
            "timeout": 10, "enabled": True}
        cfg["settings"]["ai_chain_alpha"] = ["openrouter"]
        errors, _ = V4.validate_config_full(cfg, env={})
        assert any("OPENROUTER_API_KEY" in e for e in errors)
        errors2, _ = V4.validate_config_full(cfg, env={"OPENROUTER_API_KEY": "x"})
        assert not any("OPENROUTER_API_KEY" in e for e in errors2)


# --------------------------------------------------------------------------
# 2. legacy migration
# --------------------------------------------------------------------------

class TestMigration:
    def test_holdings_list(self):
        import portfolio_ai_assistant as pa
        out = pa.migrate_legacy_config({"settings": {}, "holdings": ["AAPL", "MSFT"]})
        assert [a["broker_symbol"] for a in out["assets"]] == ["AAPL", "MSFT"]
        assert all(a["yahoo_symbol"] == a["broker_symbol"] for a in out["assets"])

    def test_holdings_dict_preserves_quantity(self):
        import portfolio_ai_assistant as pa
        out = pa.migrate_legacy_config({"settings": {}, "holdings": {"AAPL": {"quantity": 3}}})
        assert out["assets"][0]["quantity"] == 3

    def test_noop_when_assets_present(self):
        import portfolio_ai_assistant as pa
        cfg = minimal_config()
        assert pa.migrate_legacy_config(cfg) is cfg

    def test_run_migrate_backup_and_output(self, tmp_path):
        import portfolio_ai_assistant as pa
        src = tmp_path / "portfolio_config.json"
        src.write_text(json.dumps({"settings": {}, "holdings": ["AAPL"]}), encoding="utf-8")
        migrated = pa.migrate_legacy_config(json.loads(src.read_text(encoding="utf-8")))
        rc = pa._run_migrate(SimpleNamespace(config=str(src)),
                             src.read_text(encoding="utf-8"), migrated, "test-run")
        assert rc == 0
        assert list((tmp_path / "config_backups").glob("*.bak.json"))
        assert (tmp_path / "portfolio.migrated.json").exists()

    def test_run_migrate_invalid_output_fails(self, tmp_path):
        import portfolio_ai_assistant as pa
        src = tmp_path / "portfolio_config.json"
        src.write_text(json.dumps({"settings": {}, "holdings": 5}), encoding="utf-8")
        migrated = pa.migrate_legacy_config(json.loads(src.read_text(encoding="utf-8")))
        rc = pa._run_migrate(SimpleNamespace(config=str(src)),
                             src.read_text(encoding="utf-8"), migrated, "test-run")
        assert rc == int(V4.RunExit.CONFIG_ERROR)


# --------------------------------------------------------------------------
# 3. broker mapping
# --------------------------------------------------------------------------

class TestMapping:
    def _merge(self, assets, positions):
        import portfolio_ai_assistant as pa
        return pa.merge_broker_data(assets, {"trading212": positions, "revolut": []},
                                    symbol_aliases={})

    def test_exact_match(self):
        (merged,) = self._merge(
            [{"broker_symbol": "AAPL", "yahoo_symbol": "AAPL", "name": "Apple"}],
            [fake_pos()])
        m = merged["mapping"]
        assert m["mapping_method"] == V4.METHOD_EXACT_SYMBOL
        assert m["mapping_confidence"] == V4.CONF_HIGH
        assert m["mapping_status"] == V4.STATUS_VALID
        assert m["is_actionable"] is True

    def test_explicit_alias(self):
        import portfolio_ai_assistant as pa
        merged = pa.merge_broker_data(
            [{"broker_symbol": "VWSBD", "yahoo_symbol": "VWS.CO", "name": "Vestas"}],
            {"trading212": [fake_pos(raw="VWSBD_EQ", clean="VWSBD")], "revolut": []},
            symbol_aliases={"VWSBD": "VWS.CO"})
        assert merged[0]["mapping"]["mapping_method"] == V4.METHOD_ALIAS

    def test_prefix_match_is_weak_and_blocked(self):
        (merged,) = self._merge(
            [{"broker_symbol": "ABC", "yahoo_symbol": "ABC", "name": "Abc"}],
            [fake_pos(raw="ABC_X_EQ", clean="ABCX")])
        m = merged["mapping"]
        assert m["mapping_method"] == V4.METHOD_PREFIX
        assert m["mapping_status"] == V4.STATUS_SUSPECT
        assert m["is_actionable"] is False

    def test_ambiguous_mapping_blocked(self):
        (merged,) = self._merge(
            [{"broker_symbol": "ABC", "yahoo_symbol": "ABC", "name": "Abc"}],
            [fake_pos(raw="ABC_US_EQ", clean="ABC"),
             fake_pos(raw="ABC_DE_EQ", clean="ABC")])
        m = merged["mapping"]
        assert m["mapping_status"] == V4.STATUS_SUSPECT
        assert m["is_actionable"] is False
        assert "Ambiguous" in m["mapping_reason"]

    def test_auto_discovered_is_non_actionable(self):
        import portfolio_ai_assistant as pa
        assets = pa.auto_discover_unmatched_positions(
            [], {"trading212": [fake_pos(raw="ZZZ_US_EQ", clean="ZZZ")], "revolut": []},
            {"auto_discover_t212_positions": True}, {})
        assert len(assets) == 1
        m = assets[0]["mapping"]
        assert m["mapping_method"] == V4.METHOD_AUTO
        assert m["mapping_status"] == V4.STATUS_UNRESOLVED
        assert m["is_actionable"] is False

    def test_no_position_watchlist(self):
        (merged,) = self._merge(
            [{"broker_symbol": "NVDA", "yahoo_symbol": "NVDA", "name": "Nvidia"}], [])
        m = merged["mapping"]
        assert m["mapping_status"] == V4.STATUS_NO_POSITION
        assert m["is_actionable"] is True  # market data decides


# --------------------------------------------------------------------------
# 4. price sanity gate
# --------------------------------------------------------------------------

class TestPriceGate:
    def _audit(self):
        a = V4.new_audit("S", "S", "S", "S", "S")
        a.mapping_method = V4.METHOD_EXACT_SYMBOL
        a.mapping_confidence = V4.CONF_HIGH
        a.mapping_status = V4.STATUS_VALID
        a.mapping_reason = "Exact symbol match."
        a.is_actionable = True
        return a

    def test_matching_prices_pass(self):
        a = V4.apply_price_gate(self._audit(), 100.0, "EUR", 102.0, "EUR")
        assert a.mapping_status == V4.STATUS_VALID
        assert a.is_actionable is True
        assert a.price_comparison_status == V4.PRICE_OK

    def test_moderate_difference_warns(self):
        a = V4.apply_price_gate(self._audit(), 100.0, "EUR", 118.0, "EUR")
        assert a.price_comparison_status == V4.PRICE_WARNING
        assert a.is_actionable is True

    def test_synl_case_blocked(self):
        a = V4.apply_price_gate(self._audit(), 0.01, "EUR", 89.98, "EUR")
        assert a.mapping_status == V4.STATUS_SUSPECT
        assert a.is_actionable is False
        assert a.price_difference_pct and a.price_difference_pct > 100

    @pytest.mark.parametrize("bad", [None, 0, -5.0, float("nan")])
    def test_missing_or_bad_price_blocks(self, bad):
        a = V4.apply_price_gate(self._audit(), bad, "EUR", 10.0, "EUR")
        assert a.is_actionable is False
        b = V4.apply_price_gate(self._audit(), 10.0, "EUR", bad, "EUR")
        assert b.is_actionable is False

    def test_currency_mismatch_blocks_without_fx(self):
        a = V4.apply_price_gate(self._audit(), 100.0, "EUR", 101.0, "USD")
        assert a.mapping_status == V4.STATUS_CURRENCY
        assert a.is_actionable is False

    def test_explicit_fx_conversion_compares(self):
        a = V4.apply_price_gate(self._audit(), 100.0, "EUR", 108.0, "USD",
                                fx_rates={("EUR", "USD"): 1.08})
        assert a.currency_comparison_status == "CONVERTED"
        assert a.is_actionable is True


# --------------------------------------------------------------------------
# 5. recommendation gates
# --------------------------------------------------------------------------

BASE = dict(has_price=True, price=100.0, mapping_status="VALID",
            mapping_reason="", dq_status="OK")


class TestActionGates:
    def test_no_price_never_adds(self):
        r = V4.finalize_action(buy_prob=90, sell_prob=10, tech_score=90, rsi=40.0,
                               has_price=False, price=None,
                               mapping_status="VALID", mapping_reason="",
                               dq_status="NO_PRICE_DATA")
        assert r["action"] == V4.ACTION_NO_DATA

    def test_suspect_mapping_no_levels_action(self):
        r = V4.finalize_action(buy_prob=90, sell_prob=10, tech_score=90, rsi=40.0,
                               has_price=True, price=1.0,
                               mapping_status="MAPPING_SUSPECT",
                               mapping_reason="price deviation",
                               dq_status="OK")
        assert r["action"] == V4.ACTION_MAPPING

    def test_overbought_blocks_add(self):
        r = V4.finalize_action(buy_prob=80, sell_prob=50, tech_score=69, rsi=75.0, **BASE)
        assert r["action"] != V4.ACTION_ADD
        assert r["action"] == V4.ACTION_WAIT

    def test_sell_dominance_blocks_add(self):
        r = V4.finalize_action(buy_prob=60, sell_prob=72, tech_score=50, rsi=50.0, **BASE)
        assert r["action"] != V4.ACTION_ADD

    def test_weak_tech_blocks_default_buy(self):
        r = V4.finalize_action(buy_prob=66, sell_prob=58, tech_score=30, rsi=50.0, **BASE)
        assert r["action"] != V4.ACTION_ADD

    def test_neutral_is_hold(self):
        r = V4.finalize_action(buy_prob=50, sell_prob=50, tech_score=50, rsi=None, **BASE)
        assert r["action"] == V4.ACTION_HOLD

    def test_overbought_wait_reachable(self):
        r = V4.finalize_action(buy_prob=55, sell_prob=52, tech_score=55, rsi=72.0, **BASE)
        assert r["action"] == V4.ACTION_WAIT

    def test_valid_bearish_reduces(self):
        r = V4.finalize_action(buy_prob=55, sell_prob=75, tech_score=60, rsi=50.0, **BASE)
        assert r["action"] == V4.ACTION_REDUCE

    def test_mild_bearish_reviews(self):
        r = V4.finalize_action(buy_prob=40, sell_prob=52, tech_score=50, rsi=55.0, **BASE)
        assert r["action"] == V4.ACTION_REVIEW


# --------------------------------------------------------------------------
# 6. levels gating
# --------------------------------------------------------------------------

class TestLevels:
    def test_long_run_dca_has_no_levels(self):
        ok, reason = V4.levels_allowed(group="LONG_RUN_DCA", mapping_status="VALID",
                                       dq_status="OK", price=100.0, currency="EUR")
        assert ok is False

    def test_short_term_gets_levels(self):
        ok, _ = V4.levels_allowed(group="SHORT_TERM_TRADING", mapping_status="VALID",
                                  dq_status="OK", price=100.0, currency="EUR")
        assert ok is True

    def test_blocked_mapping_no_levels(self):
        ok, _ = V4.levels_allowed(group="SHORT_TERM_TRADING",
                                  mapping_status="MAPPING_SUSPECT",
                                  dq_status="OK", price=100.0, currency="EUR")
        assert ok is False

    def test_missing_price_or_currency_no_levels(self):
        assert V4.levels_allowed(group="SHORT_TERM_TRADING", mapping_status="VALID",
                                 dq_status="OK", price=None, currency="EUR")[0] is False
        assert V4.levels_allowed(group="SHORT_TERM_TRADING", mapping_status="VALID",
                                 dq_status="OK", price=10.0, currency=None)[0] is False


# --------------------------------------------------------------------------
# 7. output: serialization, atomic writes, report order
# --------------------------------------------------------------------------

class TestOutput:
    def test_json_safe_numpy(self):
        import numpy as np
        obj = {"a": np.float64(1.5), "b": np.int64(3),
               "c": datetime(2026, 1, 1), "d": {1, 2}}
        assert json.dumps(V4.json_safe(obj))

    def test_atomic_write(self, tmp_path):
        target = tmp_path / "r" / "report.md"
        V4.atomic_write_text(target, "hello")
        assert target.read_text(encoding="utf-8") == "hello"
        assert list((tmp_path / "r").glob("*.part")) == []
        V4.atomic_write_text(target, "world")
        assert target.read_text(encoding="utf-8") == "world"

    def _mini_result(self):
        d_map = {"broker_symbol": "XXX", "mapping_status": "MAPPING_SUSPECT",
                 "mapping_method": "WEAK_PREFIX", "mapping_confidence": "LOW",
                 "mapping_reason": "weak", "is_actionable": False,
                 "broker_currency": "EUR", "market_currency": "EUR",
                 "currency_comparison_status": "MATCH",
                 "price_comparison_status": "BLOCKED", "price_difference_pct": 199.0}
        blocked = {"broker_symbol": "XXX", "name": "X", "group": "G",
                   "data_quality": 10, "dq_status": "MAPPING_SUSPECT",
                   "action": "REVIEW_MAPPING", "action_reasons": ["weak"],
                   "blockers": ["mapping:MAPPING_SUSPECT"], "summary": {},
                   "price": {}, "trading212": {"market_value": 5.0}, "mapping": d_map}
        good = {"broker_symbol": "AAA", "name": "A", "group": "G",
                "data_quality": 80, "dq_status": "OK", "action": "ADD_CANDIDATE",
                "action_reasons": ["strong"], "blockers": [],
                "summary": {"buy_probability": 80, "sell_probability": 40},
                "price": {"price": 10.0}, "trading212": {}, "mapping": dict(d_map, is_actionable=True,
                mapping_status="VALID")}
        js = {"run_id": "r1", "timestamp": "2026-01-01T00:00:00", "run_status": "PARTIAL_SUCCESS",
              "summary": {"broker_positions_loaded": 1, "assets_analyzed": 1,
                          "broker_only_assets": 1,
                          "actions": {"ADD_CANDIDATE": 1, "REVIEW_MAPPING": 1}},
              "mapping_summary": {"actionable": 1, "blocked": 1,
                                  "by_method": {"WEAK_PREFIX": 1, "EXACT_CONFIGURED_SYMBOL": 1},
                                  "by_status": {"MAPPING_SUSPECT": 1, "VALID": 1}},
              "broker_sync_status": {"ok": True, "endpoint_used": "e", "positions_count": 1,
                                     "account_total": 100.0, "account_pnl": 1.0, "cash_free": 2.0,
                                     "fetched_at": "2026-01-01T00:00:00"},
              "providers": {"trading212": {"ok": True, "endpoint": "e", "positions": 1, "error": None},
                            "yfinance": {"ok": 1, "failed": 1},
                            "ddgs_news": {"per_ticker": 0, "global": 0, "discovery": 0,
                                          "skipped_global": False, "skipped_discovery": False},
                            "ai": {"mode": "DISABLED"}},
              "execution": {"filtered": False, "group": None, "asset": None},
              "warnings": ["w1"], "timings": {}}
        return {"run_id": "r1", "json": js, "tradable": [good], "blocked": [blocked],
                "analysis_text": "t", "ai_status": {}, "timings": {},
                "counts": {}, "news": {}, "broker_cash_ok": True, "stale_days": 7}

    def test_mapping_warnings_before_signals(self):
        import portfolio_ai_assistant as pa
        md = pa.build_main_report(self._mini_result())
        assert md.index("REVIEW MAPPING") < md.index("add candidates")
        assert "XXX" in md and "AAA" in md

    def test_publish_layout(self, tmp_path):
        import portfolio_ai_assistant as pa
        result = self._mini_result()
        manifest = {"run_id": "r1"}
        out = pa.publish_run(result, "# main", "# dq", manifest,
                             output_folder=str(tmp_path / "reports"), json_only=False)
        latest = tmp_path / "reports" / "latest"
        assert (latest / "portfolio_report.md").exists()
        assert (latest / "portfolio_report.json").exists()
        assert (latest / "data_quality.md").exists()
        assert (latest / "run_manifest.json").exists()
        assert (tmp_path / "reports" / "portfolio_analysis.md").exists()
        assert (tmp_path / "reports" / "portfolio_analysis.json").exists()
        assert len(list((tmp_path / "reports" / "archive").rglob("portfolio_report.json"))) == 1
        assert out

    def test_publish_json_only_skips_markdown(self, tmp_path):
        import portfolio_ai_assistant as pa
        result = self._mini_result()
        pa.publish_run(result, "# main", "# dq", {"run_id": "r1"},
                       output_folder=str(tmp_path / "reports"), json_only=True)
        latest = tmp_path / "reports" / "latest"
        assert (latest / "portfolio_report.json").exists()
        assert not (latest / "portfolio_report.md").exists()
        assert not (tmp_path / "reports" / "portfolio_analysis.md").exists()


# --------------------------------------------------------------------------
# 8. CLI + main() behavior (network fully mocked)
# --------------------------------------------------------------------------

def _write_project_copy(tmp_path):
    root = tmp_path
    shutil.copy("portfolio_config.example.json", root / "portfolio_config.json")
    (root / "api.env").write_text(
        "TRADING212_ENABLED=false\n"
        "OPENROUTER_API_KEY=test\n"
        "GEMINI_API_KEY=test\n"
        "MISTRAL_API_KEY=test\n", encoding="utf-8")
    return root


def _mock_network(monkeypatch):
    import portfolio_ai_assistant as pa

    def _boom(*a, **k):
        raise AssertionError("network/AI must not be called")

    monkeypatch.setattr(pa, "fetch_trading212_with_retry",
                        lambda **kw: {"positions": [],
                                      "status": {"ok": True, "positions_count": 0,
                                                 "endpoint_used": "mock", "error": None}})
    monkeypatch.setattr(pa, "fetch_trading212_cash",
                        lambda **kw: {"ok": True, "total": 1000.0, "result": 10.0,
                                      "free": 5.0, "error": None})
    monkeypatch.setattr(
        pa, "collect_asset_data",
        lambda asset, settings=None: {
            "price": {"price": 10.0, "change_1d_pct": 0.5, "change_5d_pct": 1.0,
                      "currency": "EUR", "trend_hint": "UP", "ma20": 9.5,
                      "ma50": 9.0, "ma200": None, "rsi": 55.0,
                      "history_bars": 60,
                      "last_bar": datetime.now(timezone.utc).isoformat()},
            "market_cap": None, "volume": 100, "info": {}, "technicals": {},
            "news": []})
    monkeypatch.setattr(pa, "collect_global_news", lambda settings: [])
    monkeypatch.setattr(pa, "collect_discovery_news", lambda settings: [])
    monkeypatch.setattr(pa, "run_ai_pipeline", _boom)
    monkeypatch.setattr(pa, "start_ai_warmup", _boom)
    return pa


class TestCLI:
    def test_parse_flags(self):
        import portfolio_ai_assistant as pa
        args = pa.parse_args(["--json-only", "--scheduled", "--debug"])
        assert args.json_only and args.scheduled and args.debug

    def test_unknown_group_fails(self, tmp_path, monkeypatch, capsys):
        _write_project_copy(tmp_path)
        monkeypatch.chdir(tmp_path)
        pa = _mock_network(monkeypatch)
        rc = pa.main(["--dry-run", "--no-ai", "--group", "NOPE-NOT-A-GROUP"])
        assert rc == int(V4.RunExit.CONFIG_ERROR)
        assert "Unknown group" in capsys.readouterr().out

    def test_unknown_asset_fails(self, tmp_path, monkeypatch):
        _write_project_copy(tmp_path)
        monkeypatch.chdir(tmp_path)
        pa = _mock_network(monkeypatch)
        assert pa.main(["--dry-run", "--no-ai", "--asset", "NOPE"]) == int(V4.RunExit.CONFIG_ERROR)

    def test_dry_run_no_ai_writes_nothing(self, tmp_path, monkeypatch, capsys):
        _write_project_copy(tmp_path)
        monkeypatch.chdir(tmp_path)
        pa = _mock_network(monkeypatch)
        (tmp_path / "reports").mkdir()
        before = sorted(p.name for p in (tmp_path / "reports").iterdir())
        rc = pa.main(["--dry-run", "--no-ai"])
        after = sorted(p.name for p in (tmp_path / "reports").iterdir())
        out = capsys.readouterr().out
        assert before == after
        assert "DRY RUN: no report files published" in out
        assert rc == int(V4.RunExit.PARTIAL)  # news warning present by design

    def test_no_ai_never_calls_ai(self, tmp_path, monkeypatch):
        # run_ai_pipeline/start_ai_warmup raise via _mock_network; success proves skip
        _write_project_copy(tmp_path)
        monkeypatch.chdir(tmp_path)
        pa = _mock_network(monkeypatch)
        rc = pa.main(["--dry-run", "--no-ai"])
        assert rc in (int(V4.RunExit.SUCCESS), int(V4.RunExit.PARTIAL))

    def test_validate_only_is_offline(self, tmp_path, monkeypatch):
        _write_project_copy(tmp_path)
        monkeypatch.chdir(tmp_path)
        import portfolio_ai_assistant as pa

        def _boom(*a, **k):
            raise AssertionError("validate-only must not touch network")
        monkeypatch.setattr(pa, "_probe_backend", _boom)
        assert pa.main(["--validate-only"]) == int(V4.RunExit.SUCCESS)

    def test_validate_only_broken_config(self, tmp_path, monkeypatch):
        import portfolio_ai_assistant as pa
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"settings": {}, "assets": [{"broker_symbol": "X"}]}),
                       encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        assert pa.main(["--validate-only", "--config", str(bad)]) == int(V4.RunExit.CONFIG_ERROR)

    def test_lock_conflict_exit_3(self, tmp_path, monkeypatch):
        _write_project_copy(tmp_path)
        monkeypatch.chdir(tmp_path)
        import portfolio_ai_assistant as pa
        lock = V4.RunLock(runtime_dir="runtime")
        held, _ = lock.acquire("holder")
        assert held
        try:
            assert pa.main(["--scheduled"]) == int(V4.RunExit.ALREADY_RUNNING)
        finally:
            lock.release()

    def test_debug_sets_level(self, tmp_path, monkeypatch):
        _write_project_copy(tmp_path)
        monkeypatch.chdir(tmp_path)
        import portfolio_ai_assistant as pa
        logger = logging.getLogger("PortfolioAI")
        old = logger.level
        try:
            assert pa.main(["--validate-only", "--debug"]) == int(V4.RunExit.SUCCESS)
            assert logger.level == logging.DEBUG
        finally:
            logger.setLevel(old)
