# Tuto funkciu pridaj do portfolio_ai_assistant.py
# hneď za fetch_trading212_positions()

def fetch_trading212_cash(api_key: str, api_base: str = "https://live.trading212.com") -> Dict[str, Any]:
    """
    Fetch cash balance z Trading212 API.
    GET /api/v0/equity/account/cash
    Response: { free, invested, pieCash, result, total }
    """
    if not api_key:
        return {"error": "Missing API key", "free": 0, "total": 0, "result": 0}

    base = (api_base or "https://live.trading212.com").rstrip("/")
    if base.endswith("/api/v0"):
        base = base[:-len("/api/v0")]

    headers = {"Authorization": api_key}
    url = f"{base}/api/v0/equity/account/cash"
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code in (401, 403):
            return {"error": f"HTTP {r.status_code}", "free": 0, "total": 0}
        r.raise_for_status()
        data = r.json()
        return {
            "free":     float(data.get("free",     0)),
            "invested": float(data.get("invested", 0)),
            "pie_cash": float(data.get("pieCash",  0)),
            "result":   float(data.get("result",   0)),  # celkový P&L
            "total":    float(data.get("total",    0)),  # celková hodnota účtu
            "ok":       True,
        }
    except Exception as e:
        return {"error": str(e), "free": 0, "total": 0, "result": 0}
