#!/usr/bin/env python3
"""
Minimal QuickBooks Online OAuth + SDK script (single file)
- Opens browser for consent, catches http://localhost:8000/callback
- Exchanges code -> tokens and saves tokens.json
- Example API calls: raw CompanyInfo and SDK query

Install deps:
  pip install intuitlib python-quickbooks requests

Usage:
  python qbo_min.py login   # do OAuth and save tokens.json
  python qbo_min.py whoami  # raw REST CompanyInfo
  python qbo_min.py query   # SDK query example

Set ENV to 'production' when you switch to live keys.
"""
import json, webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests
import os
import keyring
import getpass

from intuitlib.client import AuthClient
from intuitlib.enums import Scopes
from quickbooks import QuickBooks
# (Optional models) from quickbooks.objects.companyinfo import CompanyInfo

SERVICE = "qbo_receipt_processor"  # name used in the OS keychain

def _get_secret(name: str) -> str | None:
    # Prefer env vars for CI; otherwise pull from keychain
    return os.getenv(f"QBO_{name.upper()}") or keyring.get_password(SERVICE, name)

def setkeys():
    """One-time: store Client ID/Secret in the OS keychain."""
    cid = input("Enter QuickBooks CLIENT_ID: ").strip()
    cs  = getpass.getpass("Enter QuickBooks CLIENT_SECRET (hidden): ").strip()
    keyring.set_password(SERVICE, "client_id", cid)
    keyring.set_password(SERVICE, "client_secret", cs)
    print("Saved to OS keychain under service:", SERVICE)

# ---- config (no secret reads at import time) ----
REDIRECT_URI  = os.getenv("QBO_REDIRECT_URI", "http://localhost:8000/callback")
ENV           = os.getenv("QBO_ENV", "production")  # or "sandbox"
# -------------------------------------------------

auth_client = None

def get_auth_client():
    """Create AuthClient on first use, after secrets exist."""
    global auth_client
    if auth_client is None:
        cid = _get_secret("client_id")
        cs  = _get_secret("client_secret")
        if not cid or not cs:
            raise SystemExit(
                "Missing CLIENT_ID/CLIENT_SECRET.\n"
                "Run:  python qbo_min.py setkeys   (stores them in your OS keychain)\n"
                "Or set env vars QBO_CLIENT_ID / QBO_CLIENT_SECRET."
            )
        auth_client = AuthClient(
            client_id=cid,
            client_secret=cs,
            environment=ENV,
            redirect_uri=REDIRECT_URI,
        )
    return auth_client

class CB(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        code = qs.get("code", [None])[0]
        realm_id = qs.get("realmId", [None])[0]
        self.send_response(200); self.end_headers()
        self.wfile.write(b"OK, you can close this tab.")
        # exchange code -> tokens
        ac = get_auth_client()
        ac.get_bearer_token(code, realm_id=realm_id)
        with open("tokens.json", "w") as f:
            json.dump({
                "access_token": ac.access_token,
                "refresh_token": ac.refresh_token,
                "realm_id": realm_id
            }, f, indent=2)

def login():
    ac = get_auth_client()
    url = ac.get_authorization_url([Scopes.ACCOUNTING])
    webbrowser.open(url)
    host, port = "localhost", 8000
    HTTPServer((host, port), CB).handle_request()  # serve single callback

def company_info_raw():
    with open("tokens.json") as f:
        t = json.load(f)
    base = "https://sandbox-quickbooks.api.intuit.com" if ENV=="sandbox" \
           else "https://quickbooks.api.intuit.com"
    url = f"{base}/v3/company/{t['realm_id']}/companyinfo/{t['realm_id']}?minorversion=75"
    r = requests.get(url, headers={"Authorization": f"Bearer {t['access_token']}",
                                   "Accept":"application/json"}, timeout=30)
    r.raise_for_status()
    print(json.dumps(r.json(), indent=2))
    print("DEBUG URL:", url)

def company_info_sdk():
    with open("tokens.json") as f:
        t = json.load(f)
    qb = QuickBooks(auth_client=get_auth_client(),
                    refresh_token=t["refresh_token"],
                    company_id=t["realm_id"])
    # Example SDK call: do a small query
    resp = qb.query("select Id, Name from Account maxresults 5")
    print(json.dumps(resp, indent=2))

def get_transactions(t_start_date, t_end_date):
    with open("tokens.json") as f:
        t = json.load(f)
    base = "https://sandbox-quickbooks.api.intuit.com" if ENV=="sandbox" \
           else "https://quickbooks.api.intuit.com"
    url = f"{base}/v3/company/{t['realm_id']}/reports/TransactionList?start_date={t_start_date}&end_date={t_end_date}&minorversion=75"
    r = requests.get(url, headers={"Authorization": f"Bearer {t['access_token']}",
                                   "Accept":"application/json"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    # Optional: print for CLI runs
    # print(json.dumps(data, indent=2)); print("DEBUG URL:", url)
    return data

def parse_transaction_list(report_json):
    """
    Convert the TransactionList report JSON into a list[dict].
    Keys are the column titles from the report header.
    """
    cols = [c["ColTitle"] for c in report_json.get("Columns", {}).get("Column", [])]
    out = []
    for row in report_json.get("Rows", {}).get("Row", []):
        if row.get("type") == "Data":
            values = [c.get("value") for c in row.get("ColData", [])]
            out.append(dict(zip(cols, values)))
    return out

if __name__ == "__main__":
    import sys
    cmd = (sys.argv[1] if len(sys.argv)>1 else "login").lower()
    if cmd == "login":        login()
    elif cmd == "whoami":     company_info_raw()   # raw REST example
    elif cmd == "query":      company_info_sdk()   # SDK example
    elif cmd == "setkeys":     setkeys()
    elif cmd == "tx":     get_transactions(sys.argv[2], sys.argv[3])
    elif cmd == "parse":     parse_transaction_list(sys.argv[2])
    else: print("Usage: python qbo_min.py [login|whoami|query]")
