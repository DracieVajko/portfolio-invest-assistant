#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V4 hardening core for the portfolio reporting pipeline.

Stdlib-only module (fast to import, unit-testable without network or secrets):
- run exit codes / run statuses
- single-run lock (OS-level, stale-safe by design)
- atomic file publishing
- secret redaction for logs/reports/exceptions
- phase timing/logging helper
- strict config validation (errors vs warnings)
- broker mapping audit + price/currency sanity gates
- data-quality states + canonical action finalizer (new action vocabulary)
- stop-loss/take-profit gating
- transient-only HTTP retry helper

The scheduled entry point remains portfolio_ai_assistant.py, which imports this module.
"""

from __future__ import annotations

import enum
import hashlib
import json
import os
import random
import re
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

SCHEMA_VERSION = "v4.1"
APP_VERSION = "v4.1.0-hardening"


# ============================================================================
# Exit codes / run statuses
# ============================================================================

class RunExit(enum.IntEnum):
    """Dedicated exit codes. Use names, never bare numeric literals."""
    SUCCESS = 0          # full success
    FATAL = 1            # fatal runtime failure, no usable final report
    CONFIG_ERROR = 2     # invalid configuration / env / CLI selection
    ALREADY_RUNNING = 3  # skipped because another run holds the lock
    PARTIAL = 4          # usable report generated with provider/data warnings
    BROKER_FAILED = 5    # broker sync failed; report states data unavailable/stale


RUN_STATUS_SUCCESS = "SUCCESS"
RUN_STATUS_SUCCESS_WITH_WARNINGS = "SUCCESS_WITH_WARNINGS"
RUN_STATUS_PARTIAL = "PARTIAL_SUCCESS"
RUN_STATUS_FAILED = "FAILED"
RUN_STATUS_SKIPPED = "SKIPPED_ALREADY_RUNNING"


def run_status_for_exit(code: int, has_warnings: bool = False) -> str:
    if code == RunExit.SUCCESS:
        return RUN_STATUS_SUCCESS_WITH_WARNINGS if has_warnings else RUN_STATUS_SUCCESS
    if code == RunExit.PARTIAL:
        return RUN_STATUS_PARTIAL
    if code == RunExit.ALREADY_RUNNING:
        return RUN_STATUS_SKIPPED
    return RUN_STATUS_FAILED


# ============================================================================
# Secret redaction (logs, exceptions, reports, manifests)
# ============================================================================

_SECRET_PATTERNS = [
    re.compile(r"sk-or-v1-[A-Za-z0-9\-_]+"),
    re.compile(r"AIza[0-9A-Za-z\-_]{20,}"),
    re.compile(r"(?i)(bearer\s+)[^\s\"']+"),
    re.compile(r"(?i)(api[_-]?key[\"'\s:=]+)([^\s\"',}]+)"),
    re.compile(r"(?i)(^|[\s?&]key=)[^&\s\"']+"),
    re.compile(r"(?i)(authorization[\"'\s:]+(?:basic|bearer)\s+)[^\s\"']+"),
]


def redact_secrets(text: Any) -> str:
    """Mask API keys, bearer tokens and key= URL parameters. Never raises."""
    try:
        out = str(text)
    except Exception:
        return "***unprintable***"
    for pat in _SECRET_PATTERNS:
        # Keep the label (group 1) when present, mask the value.
        if pat.groups >= 1:
            out = pat.sub(lambda m: (m.group(1) + "***") if m.lastindex else "***", out)
        else:
            out = pat.sub("***", out)
    return out


# ============================================================================
# Single-run lock (stale-safe: enforced by the OS, released on process death)
# ============================================================================

_HELD_LOCKS: set = set()  # in-process guard (same-process re-entry is also a conflict)


class RunLock:
    """Cross-platform exclusive run lock.

    Enforcement is done by the OS (msvcrt.locking on Windows, flock on POSIX),
    so a crashed run can never leave a stale lock behind: the OS releases it.
    The file content is diagnostic metadata only and is never used for locking.
    We never delete a lock file; we only fail to acquire while it is held.
    """

    def __init__(self, runtime_dir: str = "runtime", name: str = "run.lock"):
        self.path = Path(runtime_dir) / name
        self._fh = None

    def acquire(self, run_id: str) -> Tuple[bool, Dict[str, Any]]:
        key = os.path.abspath(str(self.path))
        if key in _HELD_LOCKS:
            return False, {"reason": "lock already held by this process"}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.path, "a+b")
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, IOError):
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
                return False, {"reason": "lock held by another live process"}
            _HELD_LOCKS.add(key)
            try:
                self._fh.seek(0)
                self._fh.truncate(0)
                meta = {"run_id": run_id, "pid": os.getpid(),
                        "started_at": datetime.now(timezone.utc).isoformat()}
                self._fh.write(json.dumps(meta).encode("utf-8"))
                self._fh.flush()
            except Exception:
                pass
            return True, {"run_id": run_id, "pid": os.getpid()}
        except Exception as exc:
            try:
                if self._fh:
                    self._fh.close()
            except Exception:
                pass
            self._fh = None
            return False, {"reason": f"lock error: {exc}"}

    def release(self) -> None:
        key = os.path.abspath(str(self.path))
        try:
            if self._fh is not None:
                try:
                    if os.name == "nt":
                        import msvcrt
                        try:
                            msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
                    else:
                        import fcntl
                        try:
                            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                        except OSError:
                            pass
                finally:
                    try:
                        self._fh.close()
                    except Exception:
                        pass
        finally:
            self._fh = None
            _HELD_LOCKS.discard(key)

    def __enter__(self) -> "RunLock":
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()


# ============================================================================
# Atomic publishing (never leave a partially written latest report)
# ============================================================================

def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8") -> Path:
    """Write fully to a temp file on the same filesystem, then atomically replace."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=".tmp_", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp, dest)
    except BaseException:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return dest


def atomic_write_json(path: str | Path, obj: Any) -> Path:
    return atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False))


# ============================================================================
# Phase timing / structured logging
# ============================================================================

def _fmt_fields(fields: Mapping[str, Any]) -> str:
    parts = []
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)


@contextmanager
def phase(logger, run_id: str, name: str, **fields):
    """Log phase start/end with duration. Yields a stats dict the body may fill."""
    start = time.perf_counter()
    logger.info("run_id=%s phase=%s status=START %s", run_id, name, _fmt_fields(fields))
    stat: Dict[str, Any] = {"status": "OK"}
    try:
        yield stat
    except Exception:
        stat["status"] = "FAIL"
        raise
    finally:
        duration = time.perf_counter() - start
        extra = {k: v for k, v in stat.items() if k != "status"}
        logger.info("run_id=%s phase=%s status=%s duration_s=%.2f %s",
                    run_id, name, stat.get("status", "OK"), duration, _fmt_fields(extra))


# ============================================================================
# Strict config validation (errors stop the run, warnings do not)
# ============================================================================

KNOWN_PROVIDERS = {"ollama", "lmstudio", "openrouter", "gemini", "mistral", "openai_compat"}
CLOUD_PROVIDERS = {"openrouter", "gemini", "mistral", "openai_compat"}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_config_full(config: Any, env: Mapping[str, str] | None = None) -> Tuple[List[str], List[str]]:
    """Validate the portfolio config. Returns (errors, warnings).

    `env` supplies environment values for ${VAR} checks (names only are reported,
    values are never included in messages).
    """
    errors: List[str] = []
    warnings: List[str] = []
    env = env or {}

    if not isinstance(config, dict):
        return ["config root must be a JSON object"], []

    settings = config.get("settings")
    if not isinstance(settings, dict):
        errors.append("Missing 'settings' section (must be an object)")
        settings = {}

    assets = config.get("assets")
    if not isinstance(assets, list):
        errors.append("Missing 'assets' section (must be an array)")
        assets = []
    elif not assets:
        errors.append("No assets configured ('assets' is empty)")

    # ---- assets ----
    seen_ids: Dict[str, int] = {}
    alias_owner: Dict[str, str] = {}
    for idx, asset in enumerate(assets):
        where = f"assets[{idx}]"
        if not isinstance(asset, dict):
            errors.append(f"{where} must be an object")
            continue
        bid = asset.get("broker_symbol")
        if not isinstance(bid, str) or not bid.strip():
            errors.append(f"{where} missing non-empty 'broker_symbol'")
            continue
        key = bid.strip().upper()
        if key in seen_ids:
            errors.append(f"Duplicate asset broker_symbol: '{bid}' (assets[{seen_ids[key]}] and {where})")
        else:
            seen_ids[key] = idx
        if not asset.get("yahoo_symbol"):
            errors.append(f"{where} ('{bid}') missing 'yahoo_symbol'")
        if not asset.get("name"):
            errors.append(f"{where} ('{bid}') missing 'name'")
        aliases = asset.get("aliases", [])
        if aliases is not None and not isinstance(aliases, list):
            errors.append(f"{where} ('{bid}') 'aliases' must be an array")
        elif isinstance(aliases, list):
            for alias in aliases:
                if not isinstance(alias, str) or not alias.strip():
                    errors.append(f"{where} ('{bid}') has an empty alias")
                    continue
                akey = alias.strip().upper()
                if akey in alias_owner and alias_owner[akey] != key:
                    errors.append(
                        f"Ambiguous alias '{alias}': claimed by '{alias_owner[akey]}' and '{bid}'")
                else:
                    alias_owner.setdefault(akey, key)

    # ---- symbol_aliases map ----
    aliases_map = config.get("symbol_aliases", {})
    if aliases_map is not None:
        if not isinstance(aliases_map, dict):
            errors.append("'symbol_aliases' must be an object")
        else:
            value_owner: Dict[str, str] = {}
            for akey, aval in aliases_map.items():
                if not isinstance(akey, str) or not akey.strip():
                    errors.append("'symbol_aliases' has an empty key")
                if not isinstance(aval, str) or not aval.strip():
                    errors.append(f"'symbol_aliases['{akey}']' must be a non-empty string")
                    continue
                vkey = aval.strip().upper()
                if vkey in value_owner and value_owner[vkey] != akey:
                    warnings.append(
                        f"'symbol_aliases' value '{aval}' mapped from both "
                        f"'{value_owner[vkey]}' and '{akey}'")
                else:
                    value_owner.setdefault(vkey, akey)

    # ---- thresholds ----
    for tkey in ("buy_probability_threshold", "sell_probability_threshold"):
        val = settings.get(tkey)
        if val is None:
            continue
        if not _is_number(val) or not (0 <= float(val) <= 100):
            errors.append(f"settings.{tkey} must be a number in 0..100")

    # ---- timeouts ----
    for tkey, tval in settings.items():
        low = tkey.lower()
        if "timeout" in low or low.endswith("_seconds"):
            if not _is_number(tval) or float(tval) <= 0:
                errors.append(f"settings.{tkey} must be a positive number of seconds")

    # ---- ai backends / chains ----
    backends = config.get("ai_backends", {})
    if backends is not None:
        if not isinstance(backends, dict):
            errors.append("'ai_backends' must be an object")
            backends = {}
        for bkey, bcfg in backends.items():
            if not isinstance(bcfg, dict):
                errors.append(f"ai_backends.{bkey} must be an object")
                continue
            prov = bcfg.get("provider", "ollama")
            if prov not in KNOWN_PROVIDERS:
                errors.append(f"ai_backends.{bkey} has unknown provider '{prov}'")
            if not bcfg.get("base_url"):
                errors.append(f"ai_backends.{bkey} missing 'base_url'")
            if not bcfg.get("model") and prov != "ollama":
                errors.append(f"ai_backends.{bkey} missing 'model' (auto-pick allowed only for ollama)")
            tmo = bcfg.get("timeout")
            if tmo is not None and (not _is_number(tmo) or float(tmo) <= 0):
                errors.append(f"ai_backends.{bkey}.timeout must be a positive number")
            nctx = bcfg.get("num_ctx")
            if nctx is not None and (not _is_number(nctx) or int(nctx) <= 0):
                errors.append(f"ai_backends.{bkey}.num_ctx must be a positive number")
            tmp = bcfg.get("temperature")
            if tmp is not None and (not _is_number(tmp) or not (0 <= float(tmp) <= 2)):
                errors.append(f"ai_backends.{bkey}.temperature must be a number in 0..2")
            akey = bcfg.get("api_key")
            if prov in CLOUD_PROVIDERS and bcfg.get("enabled", True):
                if not akey:
                    errors.append(f"ai_backends.{bkey} ({prov}) is enabled but has no 'api_key'")
                elif isinstance(akey, str) and akey.startswith("${") and akey.endswith("}"):
                    var = akey[2:-1]
                    if not env.get(var):
                        errors.append(
                            f"ai_backends.{bkey} requires environment variable '{var}' (name only, value not shown)")
                elif isinstance(akey, str) and akey and not akey.startswith("${"):
                    warnings.append(
                        f"ai_backends.{bkey} api_key should reference '${{ENV_VAR}}' instead of an inline value")
    for chain_key in ("ai_chain_alpha", "ai_chain_summary"):
        chain = settings.get(chain_key)
        if chain is None:
            continue
        if not isinstance(chain, list) or not chain:
            errors.append(f"settings.{chain_key} must be a non-empty array of backend names")
            continue
        for entry in chain:
            if entry not in backends:
                errors.append(f"settings.{chain_key} references unknown backend '{entry}'")

    # ---- rules / presets / legacy ----
    rules = config.get("portfolio_rules")
    if rules is not None:
        if not isinstance(rules, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                              for k, v in rules.items()):
            warnings.append("'portfolio_rules' should be an object of string -> string")
    if "holdings" in config:
        warnings.append("Legacy 'holdings' field present; 'assets' takes precedence")
    presets = config.get("model_presets", {})
    if isinstance(presets, dict):
        for pkey, pcfg in presets.items():
            if not isinstance(pcfg, dict) or not pcfg.get("model"):
                warnings.append(f"model_presets.{pkey} has no model (dead preset)")

    return errors, warnings


# ============================================================================
# Broker mapping audit
# ============================================================================

METHOD_EXACT_SYMBOL = "EXACT_CONFIGURED_SYMBOL"
METHOD_NORMALIZED = "EXACT_NORMALIZED_SYMBOL"
METHOD_ALIAS = "EXPLICIT_ALIAS"
METHOD_AUTO = "AUTO_DISCOVERED"
METHOD_PREFIX = "WEAK_PREFIX"
METHOD_UNMATCHED = "UNMATCHED"
METHOD_NO_POSITION = "NO_BROKER_POSITION"

CONF_HIGH = "HIGH"
CONF_MEDIUM = "MEDIUM"
CONF_LOW = "LOW"
CONF_NONE = "NONE"

STATUS_VALID = "VALID"
STATUS_VALID_WARNING = "VALID_WITH_WARNING"
STATUS_SUSPECT = "MAPPING_SUSPECT"
STATUS_UNRESOLVED = "UNRESOLVED"
STATUS_BROKER_ONLY = "BROKER_ONLY"
STATUS_NO_MARKET = "NO_MARKET_DATA"
STATUS_CURRENCY = "CURRENCY_UNRESOLVED"
STATUS_NO_POSITION = "NO_BROKER_POSITION"

MAPPING_BLOCKING = {STATUS_SUSPECT, STATUS_UNRESOLVED, STATUS_CURRENCY}

CCY_MATCH = "MATCH"
CCY_MISMATCH = "MISMATCH"
CCY_UNKNOWN = "UNKNOWN"
CCY_NA = "NOT_APPLICABLE"

PRICE_OK = "OK"
PRICE_WARNING = "WARNING"
PRICE_BLOCKED = "BLOCKED"
PRICE_UNAVAILABLE = "UNAVAILABLE"


@dataclass
class MappingAudit:
    broker_symbol: str = ""
    normalized_broker_symbol: str = ""
    configured_asset_id: str = ""
    configured_ticker: str = ""
    external_market_ticker: str = ""
    mapping_method: str = METHOD_UNMATCHED
    mapping_confidence: str = CONF_NONE
    mapping_status: str = STATUS_UNRESOLVED
    mapping_reason: str = ""
    broker_currency: Optional[str] = None
    market_currency: Optional[str] = None
    currency_comparison_status: str = CCY_NA
    price_comparison_status: str = PRICE_UNAVAILABLE
    price_difference_pct: Optional[float] = None
    is_actionable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def new_audit(broker_symbol: str = "", normalized: str = "",
              asset_id: str = "", asset_ticker: str = "",
              market_ticker: str = "") -> MappingAudit:
    return MappingAudit(
        broker_symbol=broker_symbol or "",
        normalized_broker_symbol=(normalized or broker_symbol or "").upper(),
        configured_asset_id=asset_id or "",
        configured_ticker=asset_ticker or "",
        external_market_ticker=market_ticker or "",
    )


def apply_price_gate(audit: MappingAudit,
                     broker_price: Any, broker_ccy: Any,
                     market_price: Any, market_ccy: Any,
                     warn_pct: float = 15.0, block_pct: float = 35.0,
                     fx_rates: Mapping[tuple, float] | None = None) -> MappingAudit:
    """Compare broker vs external price; downgrade status and actionability.

    Currencies: identical → direct compare; differing with an explicit
    ``fx_rates[(SRC, DST)]`` entry → convert broker price, compare, mark
    CONVERTED; differing without conversion → CURRENCY_UNRESOLVED (blocked).
    Never raises; any incomparable input leaves the audit non-actionable-safe.
    """
    try:
        audit.broker_currency = str(broker_ccy).upper() if broker_ccy else None
        audit.market_currency = str(market_ccy).upper() if market_ccy else None

        try:
            bp = float(broker_price) if broker_price is not None else None
        except (TypeError, ValueError):
            bp = None
        try:
            mp = float(market_price) if market_price is not None else None
        except (TypeError, ValueError):
            mp = None
        if bp is not None and (bp != bp or bp <= 0):  # NaN guard
            bp = None
        if mp is not None and (mp != mp or mp <= 0):
            mp = None

        if bp is None or mp is None:
            audit.price_comparison_status = PRICE_UNAVAILABLE
            audit.price_difference_pct = None
            if audit.mapping_status in (STATUS_VALID, STATUS_VALID_WARNING):
                audit.mapping_status = STATUS_VALID_WARNING
                audit.mapping_reason = (audit.mapping_reason + "; " if audit.mapping_reason else "") + \
                    "price comparison unavailable (missing/non-positive price)"
            audit.is_actionable = False
            return audit

        # ---- currency gate ----
        converted_note = ""
        if audit.broker_currency and audit.market_currency:
            if audit.broker_currency != audit.market_currency:
                rate = (fx_rates or {}).get((audit.broker_currency, audit.market_currency))
                if rate is None or not isinstance(rate, (int, float)) or rate <= 0:
                    audit.currency_comparison_status = CCY_MISMATCH
                    audit.mapping_status = STATUS_CURRENCY
                    audit.mapping_reason = (
                        f"currency mismatch broker={audit.broker_currency} "
                        f"market={audit.market_currency}, no FX conversion available")
                    audit.price_comparison_status = PRICE_BLOCKED
                    audit.is_actionable = False
                    return audit
                bp = bp * float(rate)
                audit.currency_comparison_status = "CONVERTED"
                converted_note = (f" (broker price converted {audit.broker_currency}->"
                                  f"{audit.market_currency} @ {rate})")
            else:
                audit.currency_comparison_status = CCY_MATCH
        else:
            audit.currency_comparison_status = CCY_UNKNOWN

        # ---- price deviation gate (symmetric percentage) ----
        denom = (bp + mp) / 2.0
        diff_pct = abs(bp - mp) / denom * 100.0 if denom > 0 else None
        audit.price_difference_pct = round(diff_pct, 2) if diff_pct is not None else None

        if diff_pct is not None and diff_pct >= block_pct:
            audit.price_comparison_status = PRICE_BLOCKED
            audit.mapping_status = STATUS_SUSPECT
            extra = (f"price deviation {diff_pct:.1f}% >= block {block_pct}% "
                     f"(broker={bp}, market={mp}){converted_note}")
            if audit.currency_comparison_status == CCY_UNKNOWN:
                extra += "; currencies unverified"
            audit.mapping_reason = ((audit.mapping_reason + "; ") if audit.mapping_reason else "") + extra
            audit.is_actionable = False
        elif diff_pct is not None and diff_pct >= warn_pct:
            audit.price_comparison_status = PRICE_WARNING
            if audit.mapping_status == STATUS_VALID:
                audit.mapping_status = STATUS_VALID_WARNING
            extra = f"price deviation {diff_pct:.1f}% >= warn {warn_pct}%"
            audit.mapping_reason = ((audit.mapping_reason + "; ") if audit.mapping_reason else "") + extra
            audit.is_actionable = audit.mapping_status in (STATUS_VALID, STATUS_VALID_WARNING)
        else:
            audit.price_comparison_status = PRICE_OK
            audit.is_actionable = audit.mapping_status in (STATUS_VALID, STATUS_VALID_WARNING)
        return audit
    except Exception as exc:  # never let the gate itself crash the run
        audit.mapping_status = STATUS_SUSPECT
        audit.mapping_reason = f"price gate internal error: {exc}"
        audit.is_actionable = False
        return audit


def summarize_mappings(audits: List[MappingAudit]) -> Dict[str, Any]:
    by_status: Dict[str, int] = {}
    by_method: Dict[str, int] = {}
    for audit in audits:
        by_status[audit.mapping_status] = by_status.get(audit.mapping_status, 0) + 1
        by_method[audit.mapping_method] = by_method.get(audit.mapping_method, 0) + 1
    return {
        "total": len(audits),
        "actionable": sum(1 for a in audits if a.is_actionable),
        "blocked": sum(1 for a in audits if not a.is_actionable),
        "by_status": by_status,
        "by_method": by_method,
    }


# ============================================================================
# Data quality + canonical actions
# ============================================================================

DQ_OK = "OK"
DQ_PARTIAL = "PARTIAL"
DQ_STALE = "STALE_MARKET_DATA"
DQ_NO_PRICE = "NO_PRICE_DATA"
DQ_BROKER_ONLY = "BROKER_ONLY"
DQ_MAPPING_SUSPECT = "MAPPING_SUSPECT"
DQ_UNRESOLVED = "UNRESOLVED_MAPPING"
DQ_CURRENCY = "CURRENCY_UNRESOLVED"
DQ_PROVIDER_FAILED = "PROVIDER_FAILED"

DQ_BLOCKING = {DQ_NO_PRICE, DQ_BROKER_ONLY, DQ_MAPPING_SUSPECT,
               DQ_UNRESOLVED, DQ_CURRENCY, DQ_PROVIDER_FAILED}

ACTION_ADD = "ADD_CANDIDATE"
ACTION_HOLD = "HOLD"
ACTION_WAIT = "WAIT"
ACTION_REVIEW = "REVIEW"
ACTION_REDUCE = "REDUCE_CANDIDATE"
ACTION_NO_DATA = "DATA_UNAVAILABLE"
ACTION_MAPPING = "REVIEW_MAPPING"

LEGACY_FOR_ACTION = {
    ACTION_ADD: "BUY",
    ACTION_REDUCE: "SELL",
    ACTION_HOLD: "HOLD",
    ACTION_WAIT: "WATCH",
    ACTION_REVIEW: "WATCH",
    ACTION_NO_DATA: "WATCH",
    ACTION_MAPPING: "WATCH",
}


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def finalize_action(*, legacy: str = "WATCH",
                    buy_prob: Any = 50.0, sell_prob: Any = 50.0,
                    tech_score: Any = 50, rsi: Any = None,
                    has_price: bool = False, price: Any = None,
                    tech_complete: bool = True,
                    mapping_status: str = STATUS_VALID,
                    mapping_reason: str = "",
                    dq_status: str = DQ_PARTIAL,
                    buy_thr: float = 45.0, sell_thr: float = 55.0,
                    overbought: float = 70.0, oversold: float = 30.0,
                    add_margin: float = 5.0, reduce_margin: float = 5.0,
                    tech_floor: float = 45.0, hold_band: float = 10.0) -> Dict[str, Any]:
    """Decide the canonical action with hard gates. Never raises.

    Gates (in order): mapping block → data block → contradiction guards →
    threshold logic with WAIT/HOLD/REVIEW reachable by construction.
    """
    blockers: List[str] = []
    reasons: List[str] = []

    def _result(action: str) -> Dict[str, Any]:
        return {"action": action,
                "legacy_action": LEGACY_FOR_ACTION.get(action, "WATCH"),
                "blockers": blockers, "reasons": reasons}

    # 1) mapping gate
    if mapping_status in MAPPING_BLOCKING:
        blockers.append(f"mapping:{mapping_status}")
        if mapping_reason:
            reasons.append(mapping_reason)
        else:
            reasons.append(f"Broker mapping blocked ({mapping_status}); position kept as broker-provided only.")
        return _result(ACTION_MAPPING)

    # 2) data gate (None is not 0; missing price can never trade)
    price_num = _num(price)
    if dq_status in DQ_BLOCKING or not has_price or price_num is None or price_num <= 0:
        blockers.append(f"data:{dq_status or 'NO_PRICE'}")
        reasons.append("No valid market price/data; nothing actionable can be derived.")
        return _result(ACTION_NO_DATA)

    buy = _num(buy_prob)
    sell = _num(sell_prob)
    if buy is None or sell is None:
        blockers.append("data:missing-probabilities")
        reasons.append("Buy/sell probabilities unavailable.")
        return _result(ACTION_NO_DATA)

    tech = _num(tech_score)
    rsi_num = _num(rsi)

    incomplete_note = ""
    if not tech_complete:
        incomplete_note = "incomplete technicals (insufficient history)"

    # 3) contradiction guards
    rsi_blocks_add = rsi_num is not None and rsi_num >= overbought
    rsi_blocks_reduce = rsi_num is not None and rsi_num <= oversold
    add_lean = (buy - sell) >= add_margin
    red_lean = (sell - buy) >= reduce_margin
    buy_dominates = (buy - sell) >= reduce_margin
    tech_weak = (tech is None) or (tech < tech_floor)

    add_want = buy >= buy_thr
    red_want = sell >= sell_thr

    add_ok = (add_want and add_lean and not rsi_blocks_add
              and not tech_weak and tech_complete)
    red_ok = (red_want and red_lean and not rsi_blocks_reduce
              and not buy_dominates and tech_complete)

    # 4) threshold logic
    if add_ok and red_ok:
        reasons.append("Strong conflicting signals on both sides; needs review, no auto direction.")
        return _result(ACTION_REVIEW)
    if add_ok:
        if incomplete_note:
            reasons.append(incomplete_note)
        reasons.append(f"Buy {buy:.0f}% >= {buy_thr:.0f} and leads sell by {buy - sell:.0f} pts, "
                       f"RSI {rsi_num if rsi_num is not None else 'n/a'} not overbought, "
                       f"tech {tech if tech is not None else 'n/a'} >= {tech_floor:.0f}.")
        return _result(ACTION_ADD)
    if red_ok:
        if incomplete_note:
            reasons.append(incomplete_note)
        reasons.append(f"Sell {sell:.0f}% >= {sell_thr:.0f} and leads buy by {sell - buy:.0f} pts, "
                       f"RSI {rsi_num if rsi_num is not None else 'n/a'} not oversold.")
        return _result(ACTION_REDUCE)
    if add_want and add_lean and not add_ok:
        why = []
        if rsi_blocks_add:
            why.append(f"RSI {rsi_num:.1f} >= overbought {overbought:.0f}")
        if tech_weak:
            why.append(f"technical score {tech} below floor {tech_floor:.0f}")
        if not tech_complete:
            why.append(incomplete_note)
        reasons.append("Entry unattractive now: " + ("; ".join(why) if why else "unconfirmed"))
        return _result(ACTION_WAIT)
    if red_want and red_lean and not red_ok:
        why = []
        if buy_dominates:
            why.append(f"buy {buy:.0f}% dominates sell {sell:.0f}%")
        if rsi_blocks_reduce:
            why.append(f"RSI {rsi_num:.1f} <= oversold {oversold:.0f}")
        if not tech_complete:
            why.append(incomplete_note)
        reasons.append("Bearish tilt blocked from reduce: " + ("; ".join(why) if why else "unconfirmed"))
        return _result(ACTION_REVIEW)
    if add_want and red_want:
        reasons.append(f"Strong conflicting signals on both sides (buy {buy:.0f}% / sell {sell:.0f}%); "
                       "needs review, no auto direction.")
        return _result(ACTION_REVIEW)
    if rsi_num is not None and rsi_num >= overbought:
        reasons.append(f"Overbought RSI {rsi_num:.1f}; wait for better entry.")
        return _result(ACTION_WAIT)
    if abs(buy - sell) <= hold_band:
        if incomplete_note:
            reasons.append(incomplete_note)
        reasons.append(f"Mixed/stable signals (buy {buy:.0f}% vs sell {sell:.0f}%).")
        return _result(ACTION_HOLD)
    if sell > buy:
        reasons.append(f"Leaning negative (sell {sell:.0f}% vs buy {buy:.0f}%) but below reduce bar; review.")
        return _result(ACTION_REVIEW)
    if incomplete_note:
        reasons.append(incomplete_note)
    reasons.append(f"No urgent condition (buy {buy:.0f}% vs sell {sell:.0f}%).")
    return _result(ACTION_HOLD)


# ============================================================================
# Stop-loss / take-profit gating
# ============================================================================

LEVEL_GROUP_TRADING = "SHORT_TERM_TRADING"
LEVEL_GROUP_DCA = "LONG_RUN_DCA"


def levels_allowed(*, group: Any, mapping_status: str, dq_status: str,
                   price: Any, currency: Any) -> Tuple[bool, str]:
    """Decide whether trading levels may be emitted. Informational only, never orders."""
    if mapping_status in MAPPING_BLOCKING:
        return False, "blocked-mapping"
    if dq_status in DQ_BLOCKING:
        return False, "blocked-data-quality"
    if _num(price) is None or float(price) <= 0:
        return False, "no-reference-price"
    if not currency:
        return False, "unknown-currency"
    if group == LEVEL_GROUP_DCA:
        return False, "dca-thesis-only"
    if group == LEVEL_GROUP_TRADING:
        return True, "short-term-ma-percent"
    return False, "strategy-no-levels"


# ============================================================================
# Transient-only HTTP retry helper
# ============================================================================

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


def should_retry_status(code: Any) -> bool:
    try:
        return int(code) in TRANSIENT_STATUS
    except (TypeError, ValueError):
        return False


def retry_after_seconds(headers: Any, default: float = 5.0) -> float:
    try:
        raw = (headers or {}).get("Retry-After")
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return default


def call_with_retry(func: Callable[[], Any], *, retries: int = 2,
                    base_delay: float = 1.0) -> Any:
    """Call func(); retry transient failures only (never 401/403/404/config errors).

    Retries stdlib (ConnectionError, TimeoutError) and duck-typed HTTP errors
    exposing `.response.status_code`. Honors Retry-After on 429. Adds jitter.
    """
    attempt = 0
    while True:
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - classified below
            status = getattr(getattr(exc, "response", None), "status_code", None)
            transient = isinstance(exc, (ConnectionError, TimeoutError)) or (
                status is not None and should_retry_status(status))
            if not transient or attempt >= retries:
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            if status == 429:
                headers = getattr(getattr(exc, "response", None), "headers", None)
                delay = max(delay, retry_after_seconds(headers, base_delay))
            time.sleep(delay)
            attempt += 1


# ============================================================================
# Run identity helpers
# ============================================================================

def new_run_id(now: Optional[datetime] = None) -> str:
    moment = now or datetime.now()
    return f"{moment.strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex[:6]}"


def local_tzname() -> str:
    try:
        return str(datetime.now().astimezone().tzinfo) or "local"
    except Exception:
        return "local"


def git_commit() -> Optional[str]:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        value = (out.stdout or "").strip()
        return value or None
    except Exception:
        return None


def config_hash(config: Mapping[str, Any]) -> Optional[str]:
    try:
        blob = json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
    except Exception:
        return None


def json_safe(obj: Any) -> Any:
    """Recursively convert numpy scalars/datetimes/sets into JSON-native values."""
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (datetime,)):
        try:
            return obj.isoformat()
        except Exception:
            return str(obj)
    if isinstance(obj, set):
        return sorted((json_safe(v) for v in obj), key=str)
    if isinstance(obj, bool) or obj is None or isinstance(obj, (str, int, float)):
        if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
            return None
        return obj
    for attr in ("item",):
        try:
            candidate = getattr(obj, attr, None)
            if callable(candidate):
                return json_safe(candidate())
        except Exception:
            pass
    try:
        return float(obj)
    except Exception:
        return str(obj)


def sanitize_backends(backends: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Backend inventory for manifests: names/URLs/models only, never secrets."""
    cleaned = []
    for key, cfg in (backends or {}).items():
        if not isinstance(cfg, dict):
            continue
        cleaned.append({
            "key": key,
            "provider": cfg.get("provider"),
            "base_url": cfg.get("base_url"),
            "model": cfg.get("model") or None,
            "enabled": bool(cfg.get("enabled", True)),
            "has_api_key": bool(cfg.get("api_key")),
        })
    return cleaned
