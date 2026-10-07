"""Playwright adapter with retry logic, caching, and mapper utility for web scraping.

This module provides:
1. Robust Playwright-based scraping with exponential backoff retry
2. File-based caching with TTL for scraped data
3. Mapper/recorder utility for debugging and creating resilient selectors
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional, Dict, List
from functools import wraps

logger = logging.getLogger(__name__)


# Cache configuration (created lazily on first write — never at import time,
# so importing this module has no filesystem side effects).
CACHE_DIR = Path("reports/playwright_cache")
DEFAULT_CACHE_TTL_HOURS = 6
MAX_RETRIES = 3
BASE_RETRY_DELAY = 2.0  # seconds


def _cache_path(symbol: str) -> Path:
    """Generate cache file path for a symbol."""
    safe_symbol = symbol.replace("/", "_").replace(":", "_").replace(".", "_")
    return CACHE_DIR / f"{safe_symbol}.json"


def _is_cache_valid(cache_path: Path, ttl_hours: float = DEFAULT_CACHE_TTL_HOURS) -> bool:
    """Check if cache file exists and is within TTL."""
    if not cache_path.exists():
        return False
    try:
        mtime = datetime.fromtimestamp(cache_path.stat().st_mtime, tz=__import__('timezone').timezone.utc)
        return datetime.now(__import__('timezone').timezone.utc) - mtime < timedelta(hours=ttl_hours)
    except Exception:
        return False


def _load_cache(symbol: str) -> Optional[Dict]:
    """Load cached data if valid."""
    cache_path = _cache_path(symbol)
    if _is_cache_valid(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                data["_cached"] = True
                data["_cache_time"] = datetime.fromtimestamp(
                    cache_path.stat().st_mtime, tz=__import__('timezone').timezone.utc
                ).isoformat()
                logger.info("Playwright cache hit for %s (cached at %s)", symbol, data["_cache_time"])
                return data
        except Exception as e:
            logger.debug("Failed to load cache for %s: %s", symbol, e)
    return None


def _save_cache(symbol: str, data: Dict) -> None:
    """Save data to cache."""
    cache_path = _cache_path(symbol)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_data = dict(data)
        cache_data["_cached"] = True
        cache_data["_cache_time"] = datetime.now(__import__('timezone').timezone.utc).isoformat()
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        logger.debug("Saved Playwright cache for %s", symbol)
    except Exception as e:
        logger.warning("Failed to save cache for %s: %s", symbol, e)


def retry_with_backoff(
    max_retries: int = MAX_RETRIES,
    base_delay: float = BASE_RETRY_DELAY,
    exceptions: tuple = (Exception,),
    logger_name: str = __name__,
) -> Callable:
    """Decorator for exponential backoff retry."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            "%s attempt %d/%d failed: %s. Retrying in %.1fs...",
                            func.__name__, attempt + 1, max_retries + 1, exc, delay
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            "%s failed after %d attempts: %s",
                            func.__name__, max_retries + 1, exc
                        )
            raise last_exc
        return wrapper
    return decorator


class PlaywrightMapper:
    """Utility for recording and replaying Playwright interactions.
    
    Used to create resilient selectors by recording user interactions
    and generating robust selector maps.
    """
    
    def __init__(self, output_dir: Path = Path("reports/playwright_maps")):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.recorded_steps: List[Dict] = []
        
    def record_step(
        self,
        action: str,
        selector: str,
        url: str,
        value: Optional[str] = None,
        wait_for: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> None:
        """Record a step in the interaction sequence."""
        step = {
            "action": action,
            "selector": selector,
            "url": url,
            "value": value,
            "wait_for": wait_for,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        self.recorded_steps.append(step)
        logger.debug("Recorded step: %s %s", action, selector)
    
    def save_map(self, name: str) -> Path:
        """Save recorded steps as a selector map."""
        map_data = {
            "name": name,
            "created_at": datetime.now().isoformat(),
            "steps": self.recorded_steps,
        }
        safe_name = name.replace(" ", "_").replace("/", "_")
        output_path = self.output_dir / f"{safe_name}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(map_data, f, ensure_ascii=False, indent=2)
        logger.info("Saved Playwright map to %s", output_path)
        return output_path
    
    @classmethod
    def load_map(cls, path: Path) -> Dict:
        """Load a previously saved selector map."""
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


def fetch_with_playwright(
    symbol: str,
    scraper_func: Callable,
    use_cache: bool = True,
    cache_ttl_hours: float = DEFAULT_CACHE_TTL_HOURS,
    max_retries: int = MAX_RETRIES,
) -> Optional[Dict]:
    """High-level wrapper for Playwright scraping with cache and retry.
    
    Args:
        symbol: The symbol to fetch (e.g., "AAPL", "VWS.CO")
        scraper_func: Function that takes (page, symbol) and returns dict of data
        use_cache: Whether to use file-based caching
        cache_ttl_hours: Cache TTL in hours
        max_retries: Maximum retry attempts
    
    Returns:
        Scraped data dict or None on failure
    """
    # Check cache first
    if use_cache:
        cached = _load_cache(symbol)
        if cached:
            return cached
    
    # Retry wrapper
    @retry_with_backoff(max_retries=max_retries)
    def _scrape():
        from playwright.sync_api import sync_playwright
        from investment_engine.config.settings import EngineSettings
        
        settings = EngineSettings.from_defaults()
        headless = getattr(settings, "playwright_headless", True)
        timeout = getattr(settings, "playwright_timeout_seconds", 30) * 1000
        
        # Map symbol to TradingView format
        tv_symbol = _yahoo_to_tradingview(symbol)
        url = f"https://www.tradingview.com/symbols/{tv_symbol}/technicals/"
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)  # Wait for JS to load
            
            try:
                result = scraper_func(page, symbol)
                if result:
                    result["_source"] = "playwright"
                    result["_url"] = url
                    result["_scraped_at"] = datetime.now().isoformat()
                return result
            finally:
                browser.close()
    
    try:
        result = _scrape()
        if result and use_cache:
            _save_cache(symbol, result)
        return result
    except Exception as e:
        logger.error("Playwright scrape failed for %s after retries: %s", symbol, e)
        # Try to return stale cache if available
        if use_cache:
            cached = _load_cache(symbol)
            if cached:
                logger.warning("Returning stale cache for %s", symbol)
                return cached
        return None


def _yahoo_to_tradingview(symbol: str) -> str:
    """Convert Yahoo Finance symbol to TradingView format."""
    if symbol.endswith(".DE"):
        return symbol.replace(".DE", ":XETR")
    elif symbol.endswith(".L"):
        return symbol.replace(".L", ":LSE")
    elif symbol.endswith(".CO"):
        return symbol.replace(".CO", ":COPENHAGEN")
    elif symbol.endswith(".AS"):
        return symbol.replace(".AS", ":AMSTERDAM")
    elif symbol.endswith(".MI"):
        return symbol.replace(".MI", ":MILAN")
    elif symbol.endswith(".PA"):
        return symbol.replace(".PA", ":PARIS")
    elif symbol.endswith(".SW"):
        return symbol.replace(".SW", ":SWISS")
    elif symbol.endswith(".VI"):
        return symbol.replace(".VI", ":VIENNA")
    elif symbol.endswith(".ST"):
        return symbol.replace(".ST", ":STOCKHOLM")
    elif symbol.endswith(".HE"):
        return symbol.replace(".HE", ":HELSINKI")
    elif symbol.endswith(".OL"):
        return symbol.replace(".OL", ":OSLO")
    elif symbol.endswith(".IC"):
        return symbol.replace(".IC", ":REYKJAVIK")
    elif symbol.endswith(".KS"):
        return symbol.replace(".KS", ":KOREA")
    elif symbol.endswith(".TW"):
        return symbol.replace(".TW", ":TAIPEI")
    elif symbol.endswith(".HK"):
        return symbol.replace(".HK", ":HONGKONG")
    elif symbol.endswith(".TO"):
        return symbol.replace(".TO", ":TSX")
    elif symbol.endswith(".V"):
        return symbol.replace(".V", ":TSXV")
    return symbol


# CLI utility for recording selector maps
def main():
    """CLI entry point for recording selector maps."""
    import argparse
    import asyncio
    
    parser = argparse.ArgumentParser(description="Playwright Mapper - Record selector maps")
    parser.add_argument("url", help="URL to navigate to")
    parser.add_argument("--output", "-o", default="reports/playwright_maps/map.json", help="Output map file")
    parser.add_argument("--headless", action="store_true", default=False, help="Run headless")
    args = parser.parse_args()
    
    async def record():
        from playwright.async_api import async_playwright
        
        mapper = PlaywrightMapper()
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=args.headless)
            page = await browser.new_page()
            
            # Set up event listeners for recording
            async def on_click(locator):
                selector = await locator.get_attribute("data-testid") or await locator.get_attribute("id") or await locator.get_attribute("class")
                mapper.record_step("click", selector or "unknown", page.url)
            
            page.on("click", on_click)
            
            await page.goto(args.url, wait_until="domcontentloaded")
            print(f"Recording started. Interact with the page, then press Ctrl+C to save.")
            try:
                await asyncio.Event().wait()  # Wait forever
            except KeyboardInterrupt:
                pass
            
            await browser.close()
        
        output_path = mapper.save_map(args.output.replace(".json", ""))
        print(f"Map saved to {output_path}")
    
    asyncio.run(record())


if __name__ == "__main__":
    main()