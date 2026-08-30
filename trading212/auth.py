"""
Trading212 API Authentication Module

Trading212 API v0 uses a SINGLE API key.
Auth header: Authorization: <api_key>   (no Bearer, no Basic, just the key)

Skutočné T212 response fieldy:
  /equity/account/cash  → { free, invested, pieCash, result, total }
  /equity/account/info  → { currencyCode, id }
  /equity/portfolio     → [ { ticker, quantity, averagePrice, currentPrice,
                               ppl, fxPpl, initialFillDate, maxBuy, maxSell,
                               pieQuantity } ]
  /equity/history/orders?limit=N → { items: [...] }
"""

import os
import requests
import time
import threading
from typing import Dict, Optional, Any, List
from datetime import datetime


class Trading212Auth:
    """Secure API authentication handler for Trading212."""

    def __init__(self, api_key: str, api_secret: str = None, account_id: str = None):
        """
        T212 API v0 (current) uses HTTP Basic Auth: base64(api_key:api_secret).
        Both api_key and api_secret are required.
        """
        self.api_key = api_key
        self.api_secret = api_secret or ""
        self.account_id = account_id
        self.base_url = "https://live.trading212.com"
        self.api_base = f"{self.base_url}/api/v0"
        self.session = requests.Session()
        self._rate_limit_timestamp = 0
        self._request_count = 0

    def generate_auth_header(self) -> Dict[str, str]:
        """
        T212 Basic Auth: Authorization: Basic base64(api_key:api_secret)
        api_key = username, api_secret = password.
        """
        if not self.api_key:
            raise ValueError("API key is required")
        import base64
        creds = base64.b64encode(f"{self.api_key}:{self.api_secret}".encode()).decode()
        return {
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
        }

    def verify_credentials(self) -> bool:
        try:
            headers = self.generate_auth_header()
            response = self.session.get(
                f"{self.api_base}/equity/account/cash",
                headers=headers,
                timeout=10,
            )
            return response.status_code == 200
        except Exception as e:
            print(f"[Trading212Auth] Credential verification error: {e}")
            return False

    def make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        retry_count: int = 3,
    ) -> Dict[str, Any]:
        headers = self.generate_auth_header()
        url = f"{self.api_base}/{endpoint.lstrip('/')}"

        for attempt in range(retry_count):
            try:
                self._handle_rate_limit()

                kwargs = dict(headers=headers, timeout=15)
                if params:
                    kwargs["params"] = params

                if method.upper() == "GET":
                    response = self.session.get(url, **kwargs)
                elif method.upper() == "POST":
                    response = self.session.post(url, json=data, **kwargs)
                elif method.upper() == "DELETE":
                    response = self.session.delete(url, **kwargs)
                else:
                    response = self.session.request(method, url, json=data, **kwargs)

                if response.status_code == 429:
                    wait = int(response.headers.get("Retry-After", 60))
                    print(f"[Trading212Auth] Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                    continue

                if response.status_code == 401:
                    return {"error": "Unauthorized – check TRADING212_API_KEY", "status": "auth_failed"}

                if response.status_code == 403:
                    return {"error": "Forbidden – API key has no permission for this endpoint (read-only key?)", "status": "forbidden"}

                if response.status_code == 404:
                    return {"error": f"404 Not Found: {endpoint}", "status": "not_found"}

                if response.status_code in (200, 201):
                    try:
                        return response.json()
                    except Exception:
                        return {"success": True, "status": "ok"}

                if response.status_code >= 500:
                    if attempt < retry_count - 1:
                        time.sleep(2 ** attempt)
                        continue

                try:
                    return response.json()
                except Exception:
                    return {"error": f"HTTP {response.status_code}", "status": "error"}

            except requests.Timeout:
                if attempt < retry_count - 1:
                    time.sleep(2 ** attempt)
                    continue
            except Exception as e:
                if attempt < retry_count - 1:
                    time.sleep(2 ** attempt)
                    continue
                return {"error": f"{type(e).__name__}: {e}", "status": "error"}

        return {"error": f"Request failed after {retry_count} retries: {endpoint}", "status": "error"}

    # Class-level rate limiter (shared across all instances)
    _class_rate_limit_timestamp = 0
    _class_request_count = 0
    _class_lock = threading.Lock()

    def _handle_rate_limit(self, max_requests: int = 50):
        current_time = time.time()
        with Trading212Auth._class_lock:
            if current_time - Trading212Auth._class_rate_limit_timestamp > 60:
                Trading212Auth._class_rate_limit_timestamp = current_time
                Trading212Auth._class_request_count = 0
            Trading212Auth._class_request_count += 1
            if Trading212Auth._class_request_count > max_requests:
                sleep_time = 60 - (current_time - Trading212Auth._class_rate_limit_timestamp)
                if sleep_time > 0:
                    print(f"[Trading212Auth] Global rate limit – sleeping {sleep_time:.1f}s...")
                    time.sleep(sleep_time)
                    Trading212Auth._class_rate_limit_timestamp = time.time()
                    Trading212Auth._class_request_count = 0


class Trade212Client:
    """Trading212 API Client – read-only operations (GET only)."""

    def __init__(self, api_key: str, api_secret: str = None, account_id: str = None):
        """Both api_key and api_secret required for Basic Auth."""
        self.auth = Trading212Auth(api_key, api_secret, account_id)
        self.account_id = account_id

    # ------------------------------------------------------------------
    # ACCOUNT
    # ------------------------------------------------------------------

    def get_account_cash(self) -> Dict[str, Any]:
        """
        GET /api/v0/equity/account/cash
        Response: { free, invested, pieCash, result, total }
        """
        return self.auth.make_request("GET", "/equity/account/cash")

    def get_account_info(self) -> Dict[str, Any]:
        """
        GET /api/v0/equity/account/info
        Response: { currencyCode, id }
        """
        return self.auth.make_request("GET", "/equity/account/info")

    # ------------------------------------------------------------------
    # PORTFOLIO
    # ------------------------------------------------------------------

    def get_positions(self) -> Any:
        """
        GET /api/v0/equity/portfolio
        Response: list of position objects:
          { ticker, quantity, averagePrice, currentPrice,
            ppl, fxPpl, initialFillDate, maxBuy, maxSell, pieQuantity }
        """
        return self.auth.make_request("GET", "/equity/portfolio")

    # ------------------------------------------------------------------
    # HISTORY
    # ------------------------------------------------------------------

    def get_order_history(self, limit: int = 50, ticker: str = None) -> Dict[str, Any]:
        """GET /api/v0/equity/history/orders"""
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        return self.auth.make_request("GET", "/equity/history/orders", params=params)

    def get_dividends(self, limit: int = 50) -> Dict[str, Any]:
        """GET /api/v0/history/dividends"""
        return self.auth.make_request("GET", "/history/dividends", params={"limit": limit})

    def get_transactions(self, limit: int = 50) -> Dict[str, Any]:
        """GET /api/v0/history/transactions"""
        return self.auth.make_request("GET", "/history/transactions", params={"limit": limit})

    # ------------------------------------------------------------------
    # PIES
    # ------------------------------------------------------------------

    def get_pies(self) -> Any:
        """GET /api/v0/equity/pies"""
        return self.auth.make_request("GET", "/equity/pies")

    def get_pie(self, pie_id: str) -> Dict[str, Any]:
        """GET /api/v0/equity/pies/{id}"""
        return self.auth.make_request("GET", f"/equity/pies/{pie_id}")

    # ------------------------------------------------------------------
    # MARKET DATA
    # ------------------------------------------------------------------

    def search_instrument(self, query: str) -> Dict[str, Any]:
        """GET /api/v0/equity/metadata/instruments?search=<query>"""
        return self.auth.make_request("GET", "/equity/metadata/instruments",
                                      params={"search": query})
