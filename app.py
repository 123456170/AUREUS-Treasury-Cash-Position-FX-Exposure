"""
AUREUS — Treasury, Cash Position & FX Exposure Management (local MVP).

Single-file Streamlit application. No API keys, no external services.
Auto-seeds a clearly-labeled SYNTHETIC dataset, starts a live simulation,
and supports the full workflow: import -> validate -> forecast -> scenario
-> review -> approve -> export. Persistence: SQLite (stdlib). Demo video:
animated GIF rendered locally with Pillow from the running simulation.
Theming: 4 switchable themes (sidebar), persisted, applied to CSS, charts
and the demo reel. Default: Porcelain · Private Bank (light).

Run (Windows PowerShell):
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    streamlit run app.py
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import sqlite3
import time
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from PIL import Image, ImageDraw, ImageFont
from streamlit.components.v1 import html as components_html

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
APP_NAME = "AUREUS"
TAGLINE = "Treasury · Cash Position · FX Exposure"
ENGINE_VERSION = "1.2.1"
RULES_VERSION = "TR-2026.11"
SCHEMA_VERSION = 1
BASE_SEED = 20261112

DATA_DIR = Path("aureus_data")
FILES_DIR = DATA_DIR / "source_files"
DB_PATH = DATA_DIR / "treasury.db"

BASE_CCY = "USD"
CURRENCIES = ["USD", "EUR", "GBP", "JPY", "SGD", "CHF"]
FOREIGN = [c for c in CURRENCIES if c != BASE_CCY]
CCY_META = {
    "USD": {"color": "#B8860B", "sym": "$",  "rate_dp": 4, "name": "US Dollar"},
    "EUR": {"color": "#2563EB", "sym": "€",  "rate_dp": 4, "name": "Euro"},
    "GBP": {"color": "#C026D3", "sym": "£",  "rate_dp": 4, "name": "Pound Sterling"},
    "JPY": {"color": "#DC2626", "sym": "¥",  "rate_dp": 5, "name": "Japanese Yen"},
    "SGD": {"color": "#059669", "sym": "S$", "rate_dp": 4, "name": "Singapore Dollar"},
    "CHF": {"color": "#0891B2", "sym": "Fr", "rate_dp": 4, "name": "Swiss Franc"},
}
BASE_RATES = {"EUR": 1.0850, "GBP": 1.2720, "JPY": 0.00665, "SGD": 0.7420, "CHF": 1.1280}

# ----------------------------------------------------------------------------
# Theme engine — every visual surface derives from the active theme.
# Legacy globals (GOLD/GREEN/...) are re-pointed by apply_theme() each run,
# so all existing call sites (chips, KPIs, alerts, charts, GIF) follow along.
# ----------------------------------------------------------------------------
THEMES: dict[str, dict] = {
    "Porcelain · Private Bank": {
        "app_bg": ("radial-gradient(1100px 560px at 88% -10%,rgba(15,118,110,.07),transparent 60%),"
                   "radial-gradient(900px 520px at -8% 0%,rgba(184,134,11,.08),transparent 55%),"
                   "linear-gradient(180deg,#FAF7F0 0%,#F3EFE6 100%)"),
        "sidebar_bg": "linear-gradient(180deg,#F2EDE1,#ECE6D6)",
        "ink": "#F7F4EC", "surf": "#FFFFFF", "surf2": "#F6F1E4",
        "edge": "#D8D0BC", "edge2": "#E6DFCE",
        "txt": "#2B2A26", "mute": "#6E6A5E", "faint": "#8B8676",
        "accent": "#0F766E", "accent_hi": "#14B8A6", "accent_lo": "#0B5D57",
        "primary_txt": "#FFFFFF",
        "pos": "#0E7A5F", "neg": "#C0392B", "warn": "#B45309", "info": "#2563EB", "gray": "#9AA1AE",
        "plot_bg": "rgba(255,255,255,0.65)", "chart_font": "#55524A",
        "band": "#EFE8D8", "badge_bg": "#F3EAD3", "area_fill": "#DCEDE7", "grid2": "#E0D8C4",
        "input_bg": "#FFFFFF", "btn_bg": "#FFFFFF",
        "expander_bg": "rgba(255,255,255,0.6)", "alert_bg": "rgba(255,255,255,0.8)",
        "scroll1": "#C9BFA6", "scroll2": "#A89C7E",
        "ccy": {"USD": "#B8860B", "EUR": "#2563EB", "GBP": "#C026D3",
                "JPY": "#DC2626", "SGD": "#059669", "CHF": "#0891B2"},
    },
    "Aureus · Midnight Gold": {
        "app_bg": ("radial-gradient(1100px 560px at 88% -10%,rgba(76,154,255,.09),transparent 60%),"
                   "radial-gradient(900px 520px at -8% 0%,rgba(245,184,65,.08),transparent 55%),"
                   "linear-gradient(180deg,#0B1220 0%,#0A101C 100%)"),
        "sidebar_bg": "linear-gradient(180deg,#0E1626,#0B1220)",
        "ink": "#0B1220", "surf": "#131C2E", "surf2": "#0E1524",
        "edge": "#2A3550", "edge2": "#1E2A44",
        "txt": "#E8ECF5", "mute": "#93A0B8", "faint": "#7C89A3",
        "accent": "#F5B841", "accent_hi": "#F8D488", "accent_lo": "#C9962E",
        "primary_txt": "#1A1405",
        "pos": "#2FBF71", "neg": "#E5484D", "warn": "#E8A33D", "info": "#4C9AFF", "gray": "#6B7690",
        "plot_bg": "rgba(13,20,35,0.55)", "chart_font": "#C7CFDF",
        "band": "#101A2E", "badge_bg": "#2E200E", "area_fill": "#33270F", "grid2": "#1B2740",
        "input_bg": "#0F1728", "btn_bg": "#121C30",
        "expander_bg": "rgba(15,23,40,0.5)", "alert_bg": "rgba(15,23,40,0.75)",
        "scroll1": "#8A6A2F", "scroll2": "#5E4718",
        "ccy": {"USD": "#F5B841", "EUR": "#4C9AFF", "GBP": "#E86BC7",
                "JPY": "#FF6B5E", "SGD": "#2FBF71", "CHF": "#35C4C8"},
    },
    "Emerald · Terminal": {
        "app_bg": ("radial-gradient(1100px 560px at 88% -10%,rgba(52,211,153,.08),transparent 60%),"
                   "radial-gradient(900px 520px at -8% 0%,rgba(56,189,248,.05),transparent 55%),"
                   "linear-gradient(180deg,#06120C 0%,#040D08 100%)"),
        "sidebar_bg": "linear-gradient(180deg,#0A1B12,#06120C)",
        "ink": "#06120C", "surf": "#0C1F16", "surf2": "#081711",
        "edge": "#1E3A2C", "edge2": "#163023",
        "txt": "#E5F5EC", "mute": "#8FB8A3", "faint": "#6E9A85",
        "accent": "#34D399", "accent_hi": "#6EE7B7", "accent_lo": "#059669",
        "primary_txt": "#052E1F",
        "pos": "#34D399", "neg": "#F87171", "warn": "#FBBF24", "info": "#38BDF8", "gray": "#64748B",
        "plot_bg": "rgba(8,23,17,0.6)", "chart_font": "#BFE3D2",
        "band": "#0A1B12", "badge_bg": "#123527", "area_fill": "#0E2E22", "grid2": "#163023",
        "input_bg": "#0A1B12", "btn_bg": "#0C1F16",
        "expander_bg": "rgba(12,31,22,0.5)", "alert_bg": "rgba(12,31,22,0.75)",
        "scroll1": "#1F5C40", "scroll2": "#123B29",
        "ccy": {"USD": "#FBBF24", "EUR": "#38BDF8", "GBP": "#F472B6",
                "JPY": "#FB7185", "SGD": "#4ADE80", "CHF": "#2DD4BF"},
    },
    "Royal · Violet Night": {
        "app_bg": ("radial-gradient(1100px 560px at 88% -10%,rgba(167,139,250,.10),transparent 60%),"
                   "radial-gradient(900px 520px at -8% 0%,rgba(96,165,250,.06),transparent 55%),"
                   "linear-gradient(180deg,#100E1D 0%,#0C0A17 100%)"),
        "sidebar_bg": "linear-gradient(180deg,#161329,#100E1D)",
        "ink": "#100E1D", "surf": "#1A1730", "surf2": "#131024",
        "edge": "#322C55", "edge2": "#262045",
        "txt": "#ECE9F8", "mute": "#A29BC4", "faint": "#837BA8",
        "accent": "#A78BFA", "accent_hi": "#C4B5FD", "accent_lo": "#7C3AED",
        "primary_txt": "#17102E",
        "pos": "#4ADE80", "neg": "#FB7185", "warn": "#FCD34D", "info": "#60A5FA", "gray": "#6B7280",
        "plot_bg": "rgba(19,16,36,0.6)", "chart_font": "#CFC8E8",
        "band": "#161329", "badge_bg": "#2A2350", "area_fill": "#221C42", "grid2": "#262045",
        "input_bg": "#161329", "btn_bg": "#1A1730",
        "expander_bg": "rgba(26,23,48,0.5)", "alert_bg": "rgba(26,23,48,0.75)",
        "scroll1": "#5B4A9E", "scroll2": "#3A2F6B",
        "ccy": {"USD": "#FBBF24", "EUR": "#60A5FA", "GBP": "#F0ABFC",
                "JPY": "#FB7185", "SGD": "#4ADE80", "CHF": "#22D3EE"},
    },
}
DEFAULT_THEME = "Porcelain · Private Bank"
THEME: dict = THEMES[DEFAULT_THEME]

# Active-palette globals (re-pointed by apply_theme before any rendering).
INK, SURF, EDGE, TXT, MUTE = "#F7F4EC", "#FFFFFF", "#D8D0BC", "#2B2A26", "#6E6A5E"
GOLD, GREEN, RED, BLUE, AMBER, GRAY = "#0F766E", "#0E7A5F", "#C0392B", "#2563EB", "#B45309", "#9AA1AE"


def apply_theme(name: str) -> None:
    """Activate a theme: re-point palette globals and currency colors."""
    global THEME, INK, SURF, EDGE, TXT, MUTE, GOLD, GREEN, RED, BLUE, AMBER, GRAY
    t = THEMES.get(name, THEMES[DEFAULT_THEME])
    THEME = t
    INK, SURF, EDGE, TXT, MUTE = t["ink"], t["surf"], t["edge"], t["txt"], t["mute"]
    GOLD, GREEN, RED, BLUE, AMBER, GRAY = t["accent"], t["pos"], t["neg"], t["info"], t["warn"], t["gray"]
    for c in CURRENCIES:
        CCY_META[c]["color"] = t["ccy"][c]


def _set_theme() -> None:
    set_meta("theme", st.session_state["theme_sel"])
    st.session_state["demo_gif"] = None  # reel re-renders in the new theme


CATEGORIES = ["Settlements", "Receivables", "Payroll", "Payables", "Tax", "Debt service",
              "Scheme fees", "Infrastructure", "Intercompany", "Facilities", "Investments", "Bank fees"]
ROLES = ["Administrator", "Analyst", "Reviewer", "Stakeholder"]
PERMS = {
    "Administrator": {"import", "edit", "delete", "run", "save", "submit", "approve", "override", "archive", "export", "reset"},
    "Analyst":       {"import", "edit", "delete", "run", "save", "submit", "export"},
    "Reviewer":      {"run", "approve", "override", "export"},
    "Stakeholder":   {"export"},
}
PAGES = ["🏛️ Cockpit", "📥 Import & Quality", "🗂️ Records", "🧪 Scenario Lab",
         "✅ Review & Approvals", "📄 Reports", "🎬 Demo Studio", "🧰 Setup & Source"]
STATUS_ICON = {"draft": "📝", "in_review": "🔎", "approved": "✅", "rejected": "⛔", "archived": "🗄️"}
EVENT_MEMOS = [("Card settlement received", "Supplier payout sent"),
               ("Merchant deposit cleared", "Scheme fee debit"),
               ("FX conversion settled", "Intercompany sweep out"),
               ("Acquirer batch received", "Payroll funding transfer"),
               ("Chargeback recovery", "Chargeback debit"),
               ("Interest credit", "Bank fee debit")]
ADHOC_CP = ["Ridgeline Supplies", "Harbor Cloud Services", "Beacon Marketing", "Quill & Ledger LLP"]

REQUIREMENTS_TXT = "streamlit\npandas\nnumpy\nplotly\npillow\nopenpyxl\n"

MALFORMED_CSV = """obligation_id,counterparty,category,direction,currency,amount,due_date,confidence,status
IMP-OK-01,Harborview Media,Receivables,inflow,USD,150000,2026-12-01,0.9,scheduled
IMP-OK-02,Delta Facilities,Facilities,outflow,GBP,42000,2026-12-05,1.0,scheduled
IMP-BAD-01,Unknown FX Co,Payables,outflow,XYZ,50000,2026-12-01,1.0,scheduled
IMP-BAD-02,Broken Amount Ltd,Payables,outflow,EUR,not-a-number,2026-12-01,1.0,scheduled
IMP-BAD-03,Bad Date GmbH,Receivables,inflow,EUR,80000,2026-13-45,0.9,scheduled
IMP-BAD-04,Sideways Inc,Payables,sideways,USD,60000,2026-12-01,1.0,scheduled
IMP-BAD-05,Overconfident SA,Receivables,inflow,USD,90000,2026-12-01,1.4,scheduled
IMP-BAD-02,Duplicate Key Co,Payables,outflow,USD,-5000,2026-12-01,1.0,scheduled
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def can(action: str) -> bool:
    return action in PERMS.get(st.session_state.get("role", "Stakeholder"), set())


def role() -> str:
    return st.session_state.get("role", "Stakeholder")


def actor() -> str:
    return (st.session_state.get("user_name") or "unknown").strip() or "unknown"


# ----------------------------------------------------------------------------
# Formatting helpers
# ----------------------------------------------------------------------------
def _rgba(hex_color: str, alpha: float) -> str:
    """Plotly-safe color: convert '#RRGGBB' + alpha to 'rgba(r,g,b,a)'.

    Plotly's Python validators reject 8-digit hex (#RRGGBBAA), so every
    translucent fill must go through this helper.
    """
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def compact(x: float) -> str:
    a = abs(x)
    if a >= 1e9: return f"{x/1e9:.2f}B"
    if a >= 1e6: return f"{x/1e6:.2f}M"
    if a >= 1e3: return f"{x/1e3:.1f}K"
    return f"{x:.0f}"


def money(x: float, ccy: str = "USD", comp: bool = False) -> str:
    sym = CCY_META.get(ccy, {}).get("sym", "")
    return f"{sym}{compact(x)}" if comp else f"{sym}{x:,.0f}"


def rate_fmt(ccy: str, r: float) -> str:
    return f"{r:.{CCY_META[ccy]['rate_dp']}f}"


def chip(text: str, color: str) -> str:
    return (f'<span style="background:{color}22;color:{color};border:1px solid {color}55;'
            f'padding:2px 10px;border-radius:999px;font-size:11px;font-weight:700;letter-spacing:.4px;">{text}</span>')


def kpi_html(label: str, value: str, sub: str = "", tone: str | None = None) -> str:
    tone = tone or GOLD
    return (f'<div class="t-card"><div class="kpi-label">{label}</div>'
            f'<div class="kpi-value" style="color:{tone};">{value}</div>'
            f'<div class="kpi-sub">{sub}</div></div>')


def alert_html(kind: str, text: str) -> str:
    c = {"ok": GREEN, "warn": AMBER, "bad": RED, "info": BLUE}[kind]
    return (f'<div class="t-alert" style="border-left-color:{c};">'
            f'<span style="color:{c};font-weight:700;">●</span> {text}</div>')


def _select(label: str, options: list, key: str, fmt=None):
    """Selectbox that survives option-list changes (deletes, status moves)."""
    opts = list(options)
    if key in st.session_state and st.session_state[key] not in opts:
        del st.session_state[key]
    return st.selectbox(label, opts, key=key, format_func=fmt if fmt else (lambda x: str(x)))


def _msanitize(key: str, options: list) -> None:
    """Drop stale multiselect values that no longer exist in options."""
    if key in st.session_state:
        st.session_state[key] = [v for v in st.session_state[key] if v in options]


# ----------------------------------------------------------------------------
# Database layer (SQLite, per-call connections, WAL)
# ----------------------------------------------------------------------------
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS accounts(
        account_id TEXT PRIMARY KEY, name TEXT, bank TEXT, entity TEXT, currency TEXT,
        balance REAL, min_buffer REAL, restricted INTEGER, provenance TEXT,
        schema_version INTEGER, created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS obligations(
        obligation_id TEXT PRIMARY KEY, counterparty TEXT, category TEXT, direction TEXT,
        currency TEXT, amount REAL, due_date TEXT, confidence REAL, status TEXT,
        provenance TEXT, schema_version INTEGER, created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS fx_rates(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, currency TEXT, rate REAL, provenance TEXT)""",
    """CREATE TABLE IF NOT EXISTS cash_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, account_id TEXT, currency TEXT,
        amount REAL, kind TEXT, memo TEXT)""",
    """CREATE TABLE IF NOT EXISTS scenarios(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, version INTEGER, parent_id INTEGER,
        status TEXT, params_json TEXT, results_json TEXT, funding_json TEXT, warnings_json TEXT,
        checksum TEXT, engine_version TEXT, rules_version TEXT, created_by TEXT, created_at TEXT,
        submitted_by TEXT, submitted_at TEXT, decided_by TEXT, decided_at TEXT,
        decision_note TEXT, override_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS imports(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, filename TEXT, kind TEXT, sha256 TEXT,
        rows_ok INTEGER, rows_err INTEGER, actor TEXT, stored_path TEXT)""",
    """CREATE TABLE IF NOT EXISTS audit(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, role TEXT,
        action TEXT, target TEXT, detail_json TEXT)""",
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)",
]


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def qdf(sql: str, params: tuple = ()) -> pd.DataFrame:
    with db() as c:
        return pd.read_sql_query(sql, c, params=params)


def qrows(sql: str, params: tuple = ()) -> list[dict]:
    with db() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def ex(sql: str, params: tuple = ()) -> int:
    with db() as c:
        return c.execute(sql, params).lastrowid


def get_meta(k: str, default: str | None = None) -> str | None:
    r = qrows("SELECT value FROM meta WHERE key=?", (k,))
    return r[0]["value"] if r else default


def set_meta(k: str, v: Any) -> None:
    ex("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))


def audit(action: str, target: str, detail: dict | None = None) -> None:
    ex("INSERT INTO audit(ts,actor,role,action,target,detail_json) VALUES(?,?,?,?,?,?)",
       (utcnow_iso(), actor(), role(), action, target, json.dumps(detail or {}, default=str)))


def get_rates() -> tuple[dict, dict]:
    rows = qrows("SELECT currency,rate,ts FROM fx_rates WHERE id IN "
                 "(SELECT MAX(id) FROM fx_rates GROUP BY currency)")
    rates, ts = {"USD": 1.0}, {}
    for r in rows:
        rates[r["currency"]] = float(r["rate"])
        ts[r["currency"]] = r["ts"]
    return rates, ts


def asof_date() -> pd.Timestamp:
    ts = pd.Timestamp(get_meta("sim_ts"))
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.normalize()


def snapshot_checksum() -> str:
    a = qdf("SELECT * FROM accounts ORDER BY account_id").to_csv(index=False)
    o = qdf("SELECT * FROM obligations ORDER BY obligation_id").to_csv(index=False)
    r = json.dumps(get_rates()[0], sort_keys=True)
    return hashlib.sha256((a + o + r).encode()).hexdigest()[:16]


# ----------------------------------------------------------------------------
# Deterministic domain engine (UI-independent, typed, hand-checkable)
# ----------------------------------------------------------------------------
class EngineError(ValueError):
    """Explicit, actionable calculation error."""


def default_params() -> dict:
    return {"horizon_days": 30, "shocks": {c: 0.0 for c in FOREIGN},
            "collection_delay_days": 0, "inflow_haircut_pct": 0.0,
            "floor_usd": 12_500_000.0, "buffer_scale": 1.0,
            "weight_confidence": True, "include_overdue": True, "spread_bps": 15}


PRESETS = {
    "Base": {},
    "Mild stress": {"shocks": {"EUR": -3, "GBP": -3, "JPY": -4, "SGD": -2, "CHF": -2},
                    "collection_delay_days": 3, "inflow_haircut_pct": 8, "spread_bps": 25},
    "Severe stress": {"shocks": {"EUR": -8, "GBP": -8, "JPY": -10, "SGD": -6, "CHF": -6},
                      "collection_delay_days": 7, "inflow_haircut_pct": 20,
                      "spread_bps": 40, "buffer_scale": 1.1},
    "Custom": {},
}


def build_forecast(accounts: pd.DataFrame, obligations: pd.DataFrame,
                   rates: dict, p: dict, asof: pd.Timestamp) -> dict:
    """Roll-forward daily cash forecast, USD consolidated.

    pos_ccy(d) = opening_balance_ccy + sum(signed effective flows due <= d)
    total_usd(d) = sum_ccy pos_ccy(d) * shocked_rate(ccy)
    Funding gap = max(0, policy_floor - min_d total_usd(d)).
    Rates are USD per 1 unit of currency. Restricted accounts excluded.
    """
    for col in ("account_id", "currency", "balance", "min_buffer", "restricted"):
        if col not in accounts.columns:
            raise EngineError(f"accounts missing column '{col}'")
    for col in ("obligation_id", "counterparty", "category", "direction", "currency",
                "amount", "due_date", "confidence", "status"):
        if col not in obligations.columns:
            raise EngineError(f"obligations missing column '{col}'")
    h = int(p["horizon_days"])
    delay = int(p["collection_delay_days"])
    haircut = float(p["inflow_haircut_pct"])
    scale = float(p["buffer_scale"])
    spread = int(p["spread_bps"])
    if not (7 <= h <= 120): raise EngineError("horizon_days must be 7..120")
    if not (0 <= delay <= 30): raise EngineError("collection_delay_days must be 0..30")
    if not (0 <= haircut <= 60): raise EngineError("inflow_haircut_pct must be 0..60")
    if not (0.25 <= scale <= 3): raise EngineError("buffer_scale must be 0.25..3")
    if not (0 <= spread <= 500): raise EngineError("spread_bps must be 0..500")
    if float(p["floor_usd"]) < 0: raise EngineError("floor_usd must be >= 0")
    for c, s in p["shocks"].items():
        if not (-50 <= float(s) <= 50): raise EngineError(f"FX shock {c} out of range (-50..50%)")

    used = set(accounts["currency"]) | set(obligations.loc[obligations["status"] != "settled", "currency"])
    missing = used - set(rates)
    if missing:
        raise EngineError(f"Missing FX rates for: {', '.join(sorted(missing))}. Import market observations first.")

    asof = pd.Timestamp(asof).normalize()
    rs = {c: float(rates[c]) * (1.0 + float(p["shocks"].get(c, 0.0)) / 100.0) for c in rates}
    avail = accounts[accounts["restricted"].astype(int) == 0]
    opening = {c: float(avail.loc[avail["currency"] == c, "balance"].sum()) for c in CURRENCIES}
    sleeve = {c: float(avail.loc[avail["currency"] == c, "min_buffer"].sum()) * scale for c in CURRENCIES}
    days = pd.date_range(asof, periods=h + 1, freq="D")

    ob = obligations[obligations["status"] != "settled"].copy()
    warns: list[str] = []
    ob["due"] = pd.to_datetime(ob["due_date"]).dt.normalize()
    od = ob[ob["due"] < asof]
    if len(od):
        od_usd = sum(float(r.amount) * (1 if r.direction == "inflow" else -1) * rs.get(r.currency, 1)
                     for r in od.itertuples())
        if p["include_overdue"]:
            ob.loc[ob["due"] < asof, "due"] = asof
            warns.append(f"{len(od)} overdue obligation(s) rolled to day 0 (net {money(od_usd, comp=True)}).")
        else:
            ob = ob[ob["due"] >= asof]
            warns.append(f"{len(od)} overdue obligation(s) EXCLUDED by scenario toggle (net {money(od_usd, comp=True)}).")
    ob["w"] = np.where(bool(p["weight_confidence"]), ob["confidence"].astype(float).clip(0, 1), 1.0)
    infl = ob["direction"] == "inflow"
    ob["eff_due"] = ob["due"]
    ob.loc[infl, "eff_due"] = ob.loc[infl, "due"] + pd.Timedelta(days=delay)
    ob["amt"] = ob["amount"].astype(float) * ob["w"]
    ob.loc[infl, "amt"] = ob.loc[infl, "amt"] * (1.0 - haircut / 100.0)
    beyond = ob["eff_due"] > days[-1]
    if int(beyond.sum()):
        warns.append(f"{int(beyond.sum())} flow(s) pushed beyond the {h}-day horizon by collection delay — excluded from forecast.")
    ob = ob[~beyond].sort_values(["eff_due", "obligation_id"])

    daily = pd.DataFrame(index=days)
    daily.index.name = "date"
    for c in CURRENCIES:
        net = pd.Series(0.0, index=days)
        for r in ob[ob["currency"] == c].itertuples():
            net.loc[r.eff_due] += float(r.amt) if r.direction == "inflow" else -float(r.amt)
        pos = opening[c] + net.cumsum()
        daily[f"pos_{c}"] = pos
        daily[f"usd_{c}"] = pos * rs[c]
    daily["total_usd"] = sum(daily[f"usd_{c}"] for c in CURRENCIES)
    floor = float(p["floor_usd"]) * scale
    daily["floor_usd"] = floor

    breach = daily["total_usd"] < floor
    mi = int(daily["total_usd"].values.argmin())
    min_usd, min_date = float(daily["total_usd"].iloc[mi]), days[mi]
    headroom = min_usd - floor
    gap = max(0.0, floor - min_usd)

    sleeve_breaches = []
    for c in CURRENCIES:
        m = float(daily[f"usd_{c}"].min())
        sv = sleeve[c] * rs[c]
        if sv > 0 and m < sv:
            sleeve_breaches.append({"ccy": c, "min_usd": m, "sleeve_usd": sv,
                                    "date": daily[f"usd_{c}"].idxmin().strftime("%Y-%m-%d")})
            warns.append(f"{c} sleeve dips below its buffer on {sleeve_breaches[-1]['date']} "
                         f"({money(m, comp=True)} < {money(sv, comp=True)}).")

    exp_rows = []
    for c in CURRENCIES:
        native = float(daily[f"pos_{c}"].iloc[-1])
        usd = native * rs[c]
        safe = max(0.0, float(daily[f"usd_{c}"].min()) - sleeve[c] * rs[c])
        exp_rows.append({"currency": c, "native_net": native, "usd_net": usd,
                         "sens_1pct_usd": usd * 0.01, "sleeve_buffer_usd": sleeve[c] * rs[c],
                         "safe_surplus_usd": safe if c != BASE_CCY else 0.0})
    exposure = pd.DataFrame(exp_rows)

    actions, need = [], gap
    sources = sorted(FOREIGN, key=lambda c: (-float(exposure.loc[exposure["currency"] == c, "safe_surplus_usd"].iloc[0]), c))
    for c in sources:
        surplus = float(exposure.loc[exposure["currency"] == c, "safe_surplus_usd"].iloc[0])
        take = min(need, surplus)
        if take > 1.0:
            actions.append({"action": "FX sell", "ccy": c, "amount_native": take / rs[c],
                            "usd": take, "rate": rs[c], "spread_bps": spread,
                            "cost_usd": take * spread / 1e4,
                            "note": f"Convert safe {c} surplus ahead of {min_date.strftime('%Y-%m-%d')}"})
            need -= take
    if need > 1.0:
        actions.append({"action": "Revolver draw", "ccy": "USD", "amount_native": need, "usd": need,
                        "rate": 1.0, "spread_bps": 0, "cost_usd": 0.0,
                        "note": "Residual gap — external facility draw requires credit approval"})

    win = ob[ob["eff_due"] <= min_date]
    def top(direction: str) -> list[dict]:
        sub = win[win["direction"] == direction].sort_values("amt", ascending=False).head(5)
        return [{"id": r.obligation_id, "counterparty": r.counterparty, "ccy": r.currency,
                 "amount_eff": float(r.amt), "due": r.eff_due.strftime("%Y-%m-%d"), "category": r.category}
                for r in sub.itertuples()]

    low = ob[(ob["direction"] == "inflow") & (ob["confidence"] < 0.7)]
    if len(low):
        warns.append(f"{len(low)} low-confidence inflow(s) (<0.7) — {'probability-weighted' if p['weight_confidence'] else 'counted at FULL value (weighting off)'}." )
    restricted_usd = float(sum(float(r.balance) * rs.get(r.currency, 1)
                               for r in accounts[accounts["restricted"].astype(int) == 1].itertuples()))
    if restricted_usd > 0:
        warns.append(f"Restricted cash excluded from available position: {money(restricted_usd, comp=True)}.")
    if 0 <= headroom < 0.04 * max(floor, 1):
        warns.append(f"Thin headroom: only {money(headroom, comp=True)} above policy floor on {min_date.strftime('%Y-%m-%d')}.")

    summary = {"opening_usd": float(daily["total_usd"].iloc[0]), "end_usd": float(daily["total_usd"].iloc[-1]),
               "min_usd": min_usd, "min_date": min_date.strftime("%Y-%m-%d"),
               "headroom_usd": headroom, "floor_usd": floor, "gap_usd": gap,
               "breach_days": int(breach.sum()),
               "first_breach": days[int(np.argmax(breach.values))].strftime("%Y-%m-%d") if bool(breach.any()) else None,
               "fx_exposure_usd": float(exposure.loc[exposure["currency"] != "USD", "usd_net"].abs().sum()),
               "sens_total_1pct": float(exposure.loc[exposure["currency"] != "USD", "sens_1pct_usd"].sum()),
               "n_accounts": int(len(accounts)), "n_obligations": int(len(ob)),
               "restricted_usd": restricted_usd}
    return {"daily": daily, "summary": summary, "exposure": exposure, "funding": actions,
            "warnings": warns, "contributions": {"outflows": top("outflow"), "inflows": top("inflow")},
            "params": dict(p), "engine_version": ENGINE_VERSION, "rules_version": RULES_VERSION}


def serialize_results(res: dict) -> dict:
    d = res["daily"]
    return {"summary": res["summary"],
            "daily": [{"date": idx.strftime("%Y-%m-%d"), "total_usd": float(r.total_usd),
                       "floor_usd": float(r.floor_usd)} for idx, r in zip(d.index, d.itertuples())],
            "exposure": res["exposure"].to_dict("records"),
            "contributions": res["contributions"]}


# ----------------------------------------------------------------------------
# Import validation (row-level, actionable errors; never overwrites)
# ----------------------------------------------------------------------------
ID_RE = re.compile(r"^[A-Za-z0-9_\-]{3,40}$")
REQUIRED = {
    "accounts": ["account_id", "name", "bank", "entity", "currency", "balance", "min_buffer", "restricted"],
    "obligations": ["obligation_id", "counterparty", "category", "direction", "currency",
                    "amount", "due_date", "confidence", "status"],
    "fx_rates": ["timestamp", "currency", "rate"],
}


def _num(s: Any) -> float | None:
    try:
        v = float(str(s).replace(",", "").strip())
        return v if math.isfinite(v) else None
    except Exception:
        return None


def validate_import(kind: str, raw: pd.DataFrame, existing_ids: set[str]) -> tuple[list[dict], list[dict], int]:
    errors: list[dict] = []
    df = raw.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    miss = [c for c in REQUIRED[kind] if c not in df.columns]
    if miss:
        return [], [{"row": 0, "column": ", ".join(miss), "value": "",
                     "problem": "Required column(s) missing",
                     "fix": f"Add columns: {', '.join(miss)} (see template)"}], len(df)
    clean, seen = [], set()

    def err(row, col, val, problem, fix):
        errors.append({"row": row, "column": col, "value": str(val)[:60], "problem": problem, "fix": fix})

    for i, r in df.iterrows():
        rowno = int(i) + 2
        n0 = len(errors)
        rec: dict = {"_row": rowno}

        def text(col, maxlen=120):
            v = str(r[col]).strip()
            if not v: err(rowno, col, v, "Required value empty", "Provide a non-empty value")
            elif len(v) > maxlen: err(rowno, col, v, f"Too long (>{maxlen})", "Shorten the value")
            return v

        def ccy():
            v = str(r["currency"]).strip().upper()
            if v not in CURRENCIES:
                err(rowno, "currency", v, "Unsupported currency", f"Use one of {', '.join(CURRENCIES)}")
            return v

        if kind in ("accounts", "obligations"):
            idcol = "account_id" if kind == "accounts" else "obligation_id"
            v = str(r[idcol]).strip()
            if not ID_RE.match(v):
                err(rowno, idcol, v, "Invalid identifier", "3–40 chars: letters, digits, _ or -")
            elif v in seen:
                err(rowno, idcol, v, "Duplicate key inside file", "Make identifiers unique within the file")
            elif v in existing_ids:
                err(rowno, idcol, v, "Key already exists in database",
                    "Imports never overwrite — edit in Records, or use a new identifier")
            seen.add(v)
            rec[idcol] = v
            rec["currency"] = ccy()

        if kind == "accounts":
            rec.update(name=text("name"), bank=text("bank"), entity=text("entity"))
            bal = _num(r["balance"])
            if bal is None or abs(bal) > 1e12:
                err(rowno, "balance", r["balance"], "Not a finite number in range", "Numeric, |balance| ≤ 1e12 (minor units excluded)")
            rec["balance"] = bal
            buf = _num(r["min_buffer"])
            if buf is None or not (0 <= buf <= 1e12):
                err(rowno, "min_buffer", r["min_buffer"], "Invalid buffer", "Numeric, 0 ≤ min_buffer ≤ 1e12")
            rec["min_buffer"] = buf
            rv = str(r["restricted"]).strip().lower()
            if rv not in ("0", "1", "true", "false", "yes", "no"):
                err(rowno, "restricted", r["restricted"], "Invalid flag", "Use 0/1, true/false or yes/no")
            rec["restricted"] = 1 if rv in ("1", "true", "yes") else 0
        elif kind == "obligations":
            rec.update(counterparty=text("counterparty"), category=text("category", 60))
            d = str(r["direction"]).strip().lower()
            if d not in ("inflow", "outflow"):
                err(rowno, "direction", d, "Invalid direction", "Use inflow or outflow")
            rec["direction"] = d
            amt = _num(r["amount"])
            if amt is None or not (0 < amt <= 1e12):
                err(rowno, "amount", r["amount"], "Invalid amount", "Numeric, strictly positive, ≤ 1e12, major currency units")
            rec["amount"] = amt
            ds = str(r["due_date"]).strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", ds) or pd.isna(pd.to_datetime(ds, errors="coerce")):
                err(rowno, "due_date", ds, "Invalid date", "Use ISO format YYYY-MM-DD")
            rec["due_date"] = ds
            cf = _num(r["confidence"])
            if cf is None or not (0 <= cf <= 1):
                err(rowno, "confidence", r["confidence"], "Invalid confidence", "Numeric probability 0.0–1.0")
            rec["confidence"] = cf
            s = str(r["status"]).strip().lower()
            if s not in ("scheduled", "confirmed", "settled"):
                err(rowno, "status", s, "Invalid status", "Use scheduled / confirmed / settled")
            rec["status"] = s
        else:  # fx_rates
            ts = pd.to_datetime(str(r["timestamp"]).strip(), errors="coerce", utc=True)
            if pd.isna(ts):
                err(rowno, "timestamp", r["timestamp"], "Invalid timestamp", "ISO 8601, e.g. 2026-11-12T16:00:00Z")
            rec["ts"] = ts.isoformat() if not pd.isna(ts) else None
            c = ccy()
            if c == "USD":
                err(rowno, "currency", c, "USD is the base currency", "Remove USD rows — base rate is fixed at 1.0")
            rec["currency"] = c
            rt = _num(r["rate"])
            if rt is None or not (0 < rt <= 1000):
                err(rowno, "rate", r["rate"], "Invalid rate", "USD per 1 unit of currency; numeric, 0 < rate ≤ 1000")
            rec["rate"] = rt
        if len(errors) == n0:
            clean.append(rec)
    return clean, errors, len(df)


def commit_import(kind: str, clean: list[dict], errors: list[dict], filename: str, payload: bytes) -> None:
    sha = hashlib.sha256(payload).hexdigest()
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", filename)[:80]
    stored = FILES_DIR / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{safe}"
    stored.write_bytes(payload)
    now = utcnow_iso()
    with db() as c:
        for rec in clean:
            prov = f"import:{filename}:r{rec['_row']}"
            if kind == "accounts":
                c.execute("INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (rec["account_id"], rec["name"], rec["bank"], rec["entity"], rec["currency"],
                           rec["balance"], rec["min_buffer"], rec["restricted"], prov, SCHEMA_VERSION, now, now))
            elif kind == "obligations":
                c.execute("INSERT INTO obligations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (rec["obligation_id"], rec["counterparty"], rec["category"], rec["direction"],
                           rec["currency"], rec["amount"], rec["due_date"], rec["confidence"], rec["status"],
                           prov, SCHEMA_VERSION, now, now))
            else:
                c.execute("INSERT INTO fx_rates(ts,currency,rate,provenance) VALUES(?,?,?,?)",
                          (rec["ts"], rec["currency"], rec["rate"], prov))
        c.execute("INSERT INTO imports(ts,filename,kind,sha256,rows_ok,rows_err,actor,stored_path) VALUES(?,?,?,?,?,?,?,?)",
                  (now, filename, kind, sha, len(clean), len(errors), actor(), str(stored)))
    audit("import_committed", f"file:{filename}",
          {"kind": kind, "rows_ok": len(clean), "rows_err": len(errors), "sha256": sha})


# ----------------------------------------------------------------------------
# Synthetic seed (clearly labeled) + scenarios
# ----------------------------------------------------------------------------
def seed_all() -> None:
    rng = np.random.default_rng(BASE_SEED)
    now = datetime.now(timezone.utc)
    iso = now.isoformat(timespec="seconds")
    today = now.date()
    prov = "synthetic-seed:v1"
    accounts = [
        ("ACC-US-01", "Operating", "JPMorgan", "Meridian Payments Inc (US)", "USD", 4_850_000, 1_500_000, 0),
        ("ACC-US-02", "Payroll", "Citi", "Meridian Payments Inc (US)", "USD", 920_000, 400_000, 0),
        ("ACC-US-03", "Client escrow (restricted)", "BNY", "Meridian Payments Inc (US)", "USD", 500_000, 0, 1),
        ("ACC-EU-01", "Operating", "Deutsche Bank", "Meridian EU GmbH", "EUR", 3_120_000, 900_000, 0),
        ("ACC-EU-02", "Collections", "ING", "Meridian EU GmbH", "EUR", 640_000, 200_000, 0),
        ("ACC-UK-01", "Operating", "Barclays", "Meridian UK Ltd", "GBP", 1_780_000, 600_000, 0),
        ("ACC-UK-02", "Tax reserve", "HSBC", "Meridian UK Ltd", "GBP", 310_000, 100_000, 0),
        ("ACC-JP-01", "Operating", "MUFG", "Meridian KK", "JPY", 210_000_000, 60_000_000, 0),
        ("ACC-SG-01", "Settlement", "DBS", "Meridian SG Pte", "SGD", 2_450_000, 700_000, 0),
        ("ACC-CH-01", "Reserve", "UBS", "Meridian CH AG", "CHF", 860_000, 250_000, 0),
    ]
    obs: list[tuple] = []

    def add(oid, cp, cat, d, ccy, amt, off, conf, status="scheduled"):
        due = (today + pd.Timedelta(days=off)).strftime("%Y-%m-%d") if isinstance(off, int) else off
        obs.append((oid, cp, cat, d, ccy, float(amt), due, float(conf), status, prov, SCHEMA_VERSION, iso, iso))

    for w in range(5):
        add(f"IN-US-00{w}", "NovaPay acquirer settlement", "Settlements", "inflow", "USD", 620_000, 1 + 7 * w, .98)
        add(f"IN-EU-00{w}", "Merchant receivables — NovaMart EU", "Receivables", "inflow", "EUR", 355_000, 2 + 7 * w, .90)
        add(f"IN-GB-00{w}", "Marketplace payout — TrendyBazaar", "Receivables", "inflow", "GBP", 185_000, 3 + 7 * w, .88)
        add(f"IN-SG-00{w}", "Remittance corridor receipts", "Settlements", "inflow", "SGD", 165_000, 1 + 7 * w, .93)
    add("IN-US-05", "Enterprise receivable — Helio Systems", "Receivables", "inflow", "USD", 410_000, 11, .85)
    add("IN-US-06", "Intercompany receipt — EU GmbH", "Intercompany", "inflow", "USD", 300_000, 20, .90)
    add("IN-EU-05", "VAT refund — DE", "Tax", "inflow", "EUR", 120_000, 18, .60)
    add("IN-JP-01", "Enterprise license — Kansai Corp", "Receivables", "inflow", "JPY", 18_000_000, 8, .90)
    add("IN-JP-02", "Settlement — JP acquirer", "Settlements", "inflow", "JPY", 25_000_000, 22, .92)
    add("IN-CH-01", "Treasury coupon — CHF notes", "Investments", "inflow", "CHF", 62_000, 6, .99)
    add("IN-XX-01", "Receivable past due — Atlas Retail", "Receivables", "inflow", "USD", 130_000, -2, .55)
    for i, off in enumerate([3, 17, 31]):
        add(f"OUT-US-0{i+1}", "Payroll — US (semi-monthly)", "Payroll", "outflow", "USD", 615_000, off, 1)
    add("OUT-US-04", "Term Loan B — bullet repayment", "Debt service", "outflow", "USD", 2_850_000, 21, 1)
    add("OUT-US-05", "Corporate tax — IRS installment", "Tax", "outflow", "USD", 900_000, 14, 1)
    add("OUT-US-06", "Card scheme fees — VisaNet", "Scheme fees", "outflow", "USD", 450_000, 7, 1)
    add("OUT-US-07", "Cloud — Nimbus Cloud", "Infrastructure", "outflow", "USD", 145_000, 5, 1)
    add("OUT-US-08", "Cloud — Nimbus Cloud", "Infrastructure", "outflow", "USD", 145_000, 26, 1)
    add("OUT-US-09", "Supplier — Shenzhen Components", "Payables", "outflow", "USD", 380_000, 10, .95)
    add("OUT-EU-01", "Payroll — EU", "Payroll", "outflow", "EUR", 425_000, 4, 1)
    add("OUT-EU-02", "Payroll — EU", "Payroll", "outflow", "EUR", 425_000, 19, 1)
    add("OUT-EU-03", "Dividend upstream — parent", "Intercompany", "outflow", "EUR", 1_200_000, 26, .95)
    add("OUT-EU-04", "Supplier — Lyon Logistics", "Payables", "outflow", "EUR", 265_000, 12, .95)
    add("OUT-GB-01", "VAT — HMRC", "Tax", "outflow", "GBP", 165_000, 9, 1)
    add("OUT-GB-02", "Payroll — UK", "Payroll", "outflow", "GBP", 212_000, 6, 1)
    add("OUT-GB-03", "Payroll — UK", "Payroll", "outflow", "GBP", 212_000, 20, 1)
    add("OUT-GB-04", "Office lease — London", "Facilities", "outflow", "GBP", 48_000, 15, 1)
    add("OUT-JP-01", "Payroll — JP", "Payroll", "outflow", "JPY", 12_000_000, 15, 1)
    add("OUT-JP-02", "Vendor — Osaka Fabrication", "Payables", "outflow", "JPY", 20_000_000, 10, .97)
    add("OUT-SG-01", "Corporate tax — IRAS", "Tax", "outflow", "SGD", 240_000, 12, 1)
    add("OUT-SG-02", "Payroll — SG", "Payroll", "outflow", "SGD", 132_000, 6, 1)
    add("OUT-SG-03", "Payroll — SG", "Payroll", "outflow", "SGD", 132_000, 20, 1)
    add("OUT-SG-04", "Partner fees — Straits Gateway", "Scheme fees", "outflow", "SGD", 92_000, 16, .95)
    add("OUT-CH-01", "Custody & bank fees", "Bank fees", "outflow", "CHF", 18_000, 13, 1)
    add("OUT-CH-02", "Intercompany sweep — to US", "Intercompany", "outflow", "CHF", 120_000, 18, .90)
    add("OUT-XX-01", "Supplier — overdue invoice AP-7741", "Payables", "outflow", "USD", 96_000, -3, 1)

    fxrows = []
    for ccy in FOREIGN:
        base = BASE_RATES[ccy]
        path = base * (1 + np.clip(np.cumsum(rng.normal(0, 0.004, 60)), -0.08, 0.08))
        for i, r in enumerate(path):
            ts = (now - pd.Timedelta(days=60 - i)).replace(hour=16, minute=0, second=0)
            fxrows.append((ts.isoformat(timespec="seconds"), ccy, float(r), prov))
        r = float(path[-1])
        for j in range(13, 0, -1):
            r = float(np.clip(r * (1 + rng.normal(0, 0.0006)), base * 0.8, base * 1.25))
            ts = now - pd.Timedelta(minutes=15 * j)
            fxrows.append((ts.isoformat(timespec="seconds"), ccy, r, prov))

    with db() as c:
        c.executemany("INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      [(*a, prov, SCHEMA_VERSION, iso, iso) for a in accounts])
        c.executemany("INSERT INTO obligations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", obs)
        c.executemany("INSERT INTO fx_rates(ts,currency,rate,provenance) VALUES(?,?,?,?)", fxrows)
        for j in range(6):
            a = accounts[j % len(accounts)]
            amt = a[5] * (0.001 + 0.001 * j) * (1 if j % 2 else -1)
            c.execute("INSERT INTO cash_events(ts,account_id,currency,amount,kind,memo) VALUES(?,?,?,?,?,?)",
                      ((now - pd.Timedelta(minutes=8 * (6 - j))).isoformat(timespec="seconds"),
                       a[0], a[4], amt, "credit" if amt > 0 else "debit",
                       EVENT_MEMOS[j % len(EVENT_MEMOS)][0 if amt > 0 else 1]))
        for k, v in {"seeded": "1", "sim_ts": iso, "tick": "0", "playing": "1", "vol": "1.0",
                     "seed_date": today.isoformat(), "engine_version": ENGINE_VERSION,
                     "rules_version": RULES_VERSION}.items():
            c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        c.execute("INSERT INTO audit(ts,actor,role,action,target,detail_json) VALUES(?,?,?,?,?,?)",
                  (iso, "system", "Administrator", "dataset_seeded", "database",
                   json.dumps({"label": "SYNTHETIC demo data", "accounts": len(accounts), "obligations": len(obs)})))

    acc, ob = qdf("SELECT * FROM accounts"), qdf("SELECT * FROM obligations")
    rates, _ = get_rates()
    chk = snapshot_checksum()
    seeds = [("Baseline funding plan — current week", default_params(), "approved"),
             ("Mild FX & collections stress", {**default_params(), **PRESETS["Mild stress"]}, "in_review"),
             ("Severe stress — bullet + delays", {**default_params(), **PRESETS["Severe stress"]}, "draft")]
    for name, p, status in seeds:
        res = build_forecast(acc, ob, rates, p, asof_date())
        sub = "demo.analyst"
        dec, note, dat = ("demo.reviewer", "Approved for illustration — synthetic dataset.", iso) if status == "approved" else (None, None, None)
        ex("""INSERT INTO scenarios(name,version,parent_id,status,params_json,results_json,funding_json,
              warnings_json,checksum,engine_version,rules_version,created_by,created_at,submitted_by,
              submitted_at,decided_by,decided_at,decision_note,override_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
           (name, 1, None, status, json.dumps(p), json.dumps(serialize_results(res)),
            json.dumps(res["funding"]), json.dumps(res["warnings"]), chk, ENGINE_VERSION, RULES_VERSION,
            sub, iso, sub if status != "draft" else None, iso if status != "draft" else None,
            dec, dat, note, None))
        audit("scenario_saved", f"scenario:{name}", {"status": status, "seeded": True})


def ensure_db() -> None:
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    with db() as c:
        for s in SCHEMA:
            c.execute(s)
    if get_meta("seeded") != "1":
        seed_all()


# ----------------------------------------------------------------------------
# Live simulation (atomic ticks; deterministic RNG per tick)
# ----------------------------------------------------------------------------
def sim_tick() -> None:
    with db() as c:
        m = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM meta")}
        tick = int(m.get("tick", "0")) + 1
        ts = pd.Timestamp(m["sim_ts"]) + pd.Timedelta(minutes=5)
        iso = ts.isoformat()
        rng = np.random.default_rng(BASE_SEED + tick * 7919)
        vol = float(m.get("vol", "1"))
        rates = {r["currency"]: r["rate"] for r in c.execute(
            "SELECT currency,rate FROM fx_rates WHERE id IN (SELECT MAX(id) FROM fx_rates GROUP BY currency)")}
        fxrows = []
        for ccy in FOREIGN:
            base = BASE_RATES[ccy]
            r = float(np.clip(rates.get(ccy, base) * (1 + rng.normal(0, 0.0006 * vol)), base * 0.8, base * 1.25))
            fxrows.append((iso, ccy, r, "simulation"))
        c.executemany("INSERT INTO fx_rates(ts,currency,rate,provenance) VALUES(?,?,?,?)", fxrows)
        if rng.random() < 0.55:
            accts = [dict(r) for r in c.execute("SELECT account_id,currency,balance FROM accounts WHERE restricted=0")]
            if accts:
                a = accts[int(rng.integers(len(accts)))]
                sign = 1.0 if rng.random() < 0.52 else -1.0
                amt = abs(a["balance"]) * float(rng.uniform(0.0008, 0.006)) * sign
                memo = EVENT_MEMOS[int(rng.integers(len(EVENT_MEMOS)))][0 if sign > 0 else 1]
                c.execute("UPDATE accounts SET balance=balance+?, updated_at=? WHERE account_id=?", (amt, iso, a["account_id"]))
                c.execute("INSERT INTO cash_events(ts,account_id,currency,amount,kind,memo) VALUES(?,?,?,?,?,?)",
                          (iso, a["account_id"], a["currency"], amt, "credit" if sign > 0 else "debit", memo))
        if rng.random() < 0.05:
            c.execute("UPDATE obligations SET status='confirmed', updated_at=? WHERE obligation_id="
                      "(SELECT obligation_id FROM obligations WHERE status='scheduled' LIMIT 1)", (iso,))
        if rng.random() < 0.03 and c.execute("SELECT COUNT(*) n FROM obligations").fetchone()["n"] < 160:
            ccy = FOREIGN[int(rng.integers(len(FOREIGN)))] if rng.random() < 0.5 else "USD"
            amt = float(rng.uniform(18_000, 90_000)) * (150.0 if ccy == "JPY" else 1.0)
            due = (ts + pd.Timedelta(days=int(rng.integers(2, 13)))).strftime("%Y-%m-%d")
            c.execute("INSERT INTO obligations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (f"SIM-{tick:05d}", ADHOC_CP[int(rng.integers(len(ADHOC_CP)))], "Payables", "outflow",
                       ccy, amt, due, float(rng.uniform(0.8, 1.0)), "scheduled", "simulation", SCHEMA_VERSION, iso, iso))
        c.execute("DELETE FROM fx_rates WHERE id < (SELECT COALESCE(MAX(id),0) FROM fx_rates)-4000")
        c.execute("DELETE FROM cash_events WHERE id < (SELECT COALESCE(MAX(id),0) FROM cash_events)-400")
        for k, v in (("tick", tick), ("sim_ts", iso)):
            c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))


def _toggle_play() -> None:
    set_meta("playing", "0" if get_meta("playing", "1") == "1" else "1")


def inject_shock() -> None:
    rng = np.random.default_rng(time.time_ns() & 0xFFFFFFFF)
    ccy = FOREIGN[int(rng.integers(len(FOREIGN)))]
    rates, _ = get_rates()
    r = rates[ccy] * 0.98
    ex("INSERT INTO fx_rates(ts,currency,rate,provenance) VALUES(?,?,?,?)",
       (get_meta("sim_ts"), ccy, float(r), "manual-shock"))
    audit("sim_fx_shock", f"fx:{ccy}", {"new_rate": float(r), "move_pct": -2.0})
    st.toast(f"Injected −2% shock on {ccy}", icon="⚡")


# ----------------------------------------------------------------------------
# Charts (Plotly — all colors resolved from the active theme at render time)
# ----------------------------------------------------------------------------
def _layout(fig: go.Figure, height: int = 360, money_y: bool = True) -> go.Figure:
    fig.update_layout(height=height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor=THEME["plot_bg"],
                      font=dict(color=THEME["chart_font"], family="Segoe UI, sans-serif", size=12),
                      margin=dict(l=10, r=10, t=34, b=10), hovermode="x unified",
                      legend=dict(orientation="h", y=1.12, x=0))
    fig.update_xaxes(gridcolor=THEME["edge2"], zerolinecolor=THEME["edge2"])
    fig.update_yaxes(gridcolor=THEME["edge2"], zerolinecolor=THEME["edge"],
                     tickprefix="$" if money_y else "", tickformat=".2s" if money_y else None)
    return fig


def fig_forecast(res: dict, baseline: dict | None = None, height: int = 380) -> go.Figure:
    d = res["daily"]
    fig = go.Figure()
    if baseline is not None:
        b = baseline["daily"]
        fig.add_trace(go.Scatter(x=b.index, y=b["total_usd"], name="Baseline", mode="lines",
                                 line=dict(color=GRAY, width=2, dash="dot")))
    fig.add_trace(go.Scatter(x=d.index, y=d["total_usd"], name="Scenario" if baseline else "Projected position",
                             mode="lines", line=dict(color=GOLD, width=3),
                             fill="tozeroy", fillcolor=_rgba(GOLD, 0.10),
                             hovertemplate="%{y:,.0f} USD<extra></extra>"))
    fig.add_trace(go.Scatter(x=[d.index[0], d.index[-1]], y=[d["floor_usd"].iloc[0]] * 2,
                             name="Policy floor", mode="lines", line=dict(color=AMBER, width=2, dash="dash")))
    br = d[d["total_usd"] < d["floor_usd"]]
    if len(br):
        fig.add_trace(go.Scatter(x=br.index, y=br["total_usd"], name="Floor breach", mode="markers",
                                 marker=dict(color=RED, size=9, symbol="x")))
    mi = int(d["total_usd"].values.argmin())
    fig.add_annotation(x=d.index[mi], y=float(d["total_usd"].iloc[mi]),
                       text=f"Trough {money(float(d['total_usd'].iloc[mi]), comp=True)}",
                       showarrow=True, arrowcolor=GOLD, font=dict(color=GOLD, size=12), ay=-34)
    return _layout(fig, height)


def fig_spark(vals: list[float], color: str) -> go.Figure:
    fig = go.Figure(go.Scatter(y=vals, mode="lines", line=dict(color=color, width=2),
                               fill="tozeroy", fillcolor=_rgba(color, 0.12)))
    fig.update_layout(height=92, margin=dict(l=2, r=2, t=2, b=2), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", showlegend=False)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False, range=[min(vals) * 0.9995, max(vals) * 1.0005])
    return fig


def fig_composition(accounts: pd.DataFrame, rates: dict) -> go.Figure:
    avail = accounts[accounts["restricted"].astype(int) == 0]
    vals = {c: float(avail.loc[avail["currency"] == c, "balance"].sum()) * rates.get(c, 1) for c in CURRENCIES}
    vals = {c: v for c, v in vals.items() if v > 0}
    fig = go.Figure(go.Pie(labels=list(vals), values=list(vals.values()), hole=0.62,
                           marker=dict(colors=[CCY_META[c]["color"] for c in vals],
                                       line=dict(color=INK, width=2)),
                           textinfo="label+percent", textfont=dict(color=THEME["txt"], size=12)))
    fig.add_annotation(text=money(sum(vals.values()), comp=True), showarrow=False,
                       font=dict(color=GOLD, size=20, family="Consolas, monospace"))
    fig.update_layout(height=300, margin=dict(l=6, r=6, t=10, b=6), showlegend=False,
                      paper_bgcolor="rgba(0,0,0,0)")
    return fig


def fig_exposure(exposure: pd.DataFrame) -> go.Figure:
    e = exposure[exposure["currency"] != "USD"]
    fig = go.Figure(go.Bar(x=e["currency"], y=e["usd_net"],
                           marker=dict(color=[CCY_META[c]["color"] for c in e["currency"]]),
                           text=[f"+{money(s, comp=True)} / 1%" for s in e["sens_1pct_usd"]],
                           textposition="outside", textfont=dict(size=11, color=THEME["chart_font"])))
    fig.update_yaxes(title="Net exposure (USD equiv)")
    return _layout(fig, 320)


def fig_ladder(base: dict, scen: dict) -> go.Figure:
    b = base["exposure"].set_index("currency")["usd_net"]
    s = scen["exposure"].set_index("currency")["usd_net"]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=CURRENCIES, y=[b[c] for c in CURRENCIES], name="Baseline end",
                         marker=dict(color=GRAY)))
    fig.add_trace(go.Bar(x=CURRENCIES, y=[s[c] for c in CURRENCIES], name="Scenario end",
                         marker=dict(color=[CCY_META[c]["color"] for c in CURRENCIES])))
    fig.update_layout(barmode="group")
    return _layout(fig, 320)


# ----------------------------------------------------------------------------
# Self-tests (executed live inside the app — real pass/fail)
# ----------------------------------------------------------------------------
def _fx_accounts(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame([{"account_id": a, "currency": c, "balance": b, "min_buffer": buf, "restricted": r}
                         for a, c, b, buf, r in rows])


def _fx_obs(rows: list[tuple], asof: pd.Timestamp) -> pd.DataFrame:
    cols = ["obligation_id", "counterparty", "category", "direction", "currency",
            "amount", "due_date", "confidence", "status"]
    return pd.DataFrame([{"obligation_id": oid, "counterparty": cp, "category": "Test", "direction": d,
                          "currency": c, "amount": amt, "due_date": (asof + pd.Timedelta(days=off)).strftime("%Y-%m-%d"),
                          "confidence": conf, "status": "scheduled"} for oid, cp, d, c, amt, off, conf in rows],
                        columns=cols)


def run_self_tests() -> list[dict]:
    results = []

    def t(name, fn):
        try:
            fn()
            results.append({"test": name, "status": "✅ PASS", "detail": "assertion satisfied"})
        except Exception as e:
            results.append({"test": name, "status": "❌ FAIL", "detail": str(e)[:160]})

    asof = pd.Timestamp("2026-11-12")
    acc = _fx_accounts([("A", "USD", 1000, 0, 0)])
    ob = _fx_obs([("O1", "Vendor", "outflow", "USD", 400, 2, 1.0),
                  ("I1", "Client", "inflow", "USD", 200, 3, 1.0)], asof)
    p = {**default_params(), "horizon_days": 5, "floor_usd": 700, "weight_confidence": False}
    r = {"USD": 1.0}

    def hand():
        res = build_forecast(acc, ob, r, p, asof)
        got = [round(v, 2) for v in res["daily"]["total_usd"]]
        assert got == [1000, 1000, 600, 800, 800, 800], got
        assert res["summary"]["min_usd"] == 600
    t("Hand-checkable roll-forward (1000 −400 +200)", hand)

    def breach():
        res = build_forecast(acc, ob, r, p, asof)
        assert res["summary"]["breach_days"] == 1
        assert res["summary"]["first_breach"] == "2026-11-14"
        assert res["summary"]["gap_usd"] == 100
    t("Policy-floor breach & gap detection", breach)

    def conf():
        ob2 = ob.copy(); ob2.loc[ob2["obligation_id"] == "I1", "confidence"] = 0.5
        res = build_forecast(acc, ob2, r, {**p, "weight_confidence": True}, asof)
        assert res["summary"]["end_usd"] == 700
    t("Confidence weighting (0.5 × 200 = 100)", conf)

    def delay():
        res = build_forecast(acc, ob, r, {**p, "collection_delay_days": 2}, asof)
        assert res["summary"]["end_usd"] == 700
    t("Collection delay shifts inflow to day 5", delay)

    def hair():
        res = build_forecast(acc, ob, r, {**p, "inflow_haircut_pct": 10}, asof)
        assert abs(res["summary"]["end_usd"] - 780) < 1e-9
    t("Inflow haircut 10% (200 → 180)", hair)

    def fx():
        a2 = _fx_accounts([("E", "EUR", 100, 0, 0)])
        e2 = _fx_obs([], asof)
        res = build_forecast(a2, e2, {"USD": 1.0, "EUR": 1.08},
                             {**p, "shocks": {**p["shocks"], "EUR": 10}}, asof)
        assert abs(res["summary"]["opening_usd"] - 118.8) < 1e-9
    t("FX conversion + shock (100 EUR × 1.188)", fx)

    def missing():
        a2 = _fx_accounts([("U", "USD", 10, 0, 0)])
        e2 = _fx_obs([("X", "cp", "outflow", "EUR", 5, 1, 1)], asof)
        try:
            build_forecast(a2, e2, {"USD": 1.0}, p, asof)
            raise AssertionError("expected EngineError")
        except EngineError:
            pass
    t("Missing FX rate raises explicit EngineError", missing)

    def funding():
        a2 = _fx_accounts([("U", "USD", 0, 0, 0), ("E", "EUR", 1000, 0, 0)])
        e2 = _fx_obs([("O", "v", "outflow", "USD", 500, 1, 1)], asof)
        res = build_forecast(a2, e2, {"USD": 1.0, "EUR": 1.1}, {**p, "floor_usd": 800}, asof)
        assert res["summary"]["gap_usd"] == 200
        covered = sum(a["usd"] for a in res["funding"] if a["action"] == "FX sell")
        assert abs(covered - 200) < 1e-6
        assert not any(a["action"] == "Revolver draw" for a in res["funding"])
        assert abs(res["funding"][0]["amount_native"] - 200 / 1.1) < 1e-6
    t("Funding plan covers gap from safe FX surplus", funding)

    def validator():
        raw = pd.DataFrame([
            {"obligation_id": "GOOD-1", "counterparty": "C", "category": "Tax", "direction": "outflow",
             "currency": "USD", "amount": "10", "due_date": "2026-12-01", "confidence": "1", "status": "scheduled"},
            {"obligation_id": "BAD-1", "counterparty": "C", "category": "Tax", "direction": "sideways",
             "currency": "XYZ", "amount": "abc", "due_date": "2026-13-45", "confidence": "1.4", "status": "scheduled"},
            {"obligation_id": "GOOD-1", "counterparty": "C", "category": "Tax", "direction": "outflow",
             "currency": "USD", "amount": "-5", "due_date": "2026-12-01", "confidence": "1", "status": "scheduled"},
        ])
        clean, errs, n = validate_import("obligations", raw, set())
        assert n == 3 and len(clean) == 1 and len(errs) >= 6, (len(clean), len(errs))
        assert any("Duplicate" in e["problem"] for e in errs)
    t("Validator: malformed rows rejected with row-level errors", validator)

    def dupdb():
        raw = pd.DataFrame([{"account_id": "ACC-US-01", "name": "n", "bank": "b", "entity": "e",
                             "currency": "USD", "balance": "1", "min_buffer": "0", "restricted": "0"}])
        clean, errs, _ = validate_import("accounts", raw, {"ACC-US-01"})
        assert len(clean) == 0 and any("never overwrite" in e["fix"] for e in errs)
    t("Duplicate-key import never overwrites", dupdb)

    def determinism():
        h1 = int(pd.util.hash_pandas_object(build_forecast(acc, ob, r, p, asof)["daily"]).sum())
        h2 = int(pd.util.hash_pandas_object(build_forecast(acc, ob, r, p, asof)["daily"]).sum())
        assert h1 == h2
    t("Deterministic replay (identical hash)", determinism)

    def roundtrip():
        sample = qdf("SELECT account_id,name,bank,entity,currency,balance,min_buffer,restricted FROM accounts LIMIT 5")
        buf = io.StringIO(); sample.to_csv(buf, index=False)
        raw = pd.read_csv(io.StringIO(buf.getvalue()), dtype=str, keep_default_na=False)
        clean, errs, _ = validate_import("accounts", raw, set())
        assert len(clean) == 5 and not errs, errs
    t("Export → re-import round-trip (0 errors)", roundtrip)

    return results


# ----------------------------------------------------------------------------
# Demo reel (live video file generated locally with Pillow, theme-aware)
# ----------------------------------------------------------------------------
def _font(sz: int, bold: bool = False):
    for name in (["arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold else ["arial.ttf", "DejaVuSans.ttf"]):
        try:
            return ImageFont.truetype(name, sz)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=sz)
    except TypeError:
        return ImageFont.load_default()


def _rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def render_demo_gif(n_frames: int, duration: int) -> bytes:
    acc, ob = qdf("SELECT * FROM accounts"), qdf("SELECT * FROM obligations")
    rates, _ = get_rates()
    asof = asof_date()
    base = build_forecast(acc, ob, rates, default_params(), asof)
    sev = build_forecast(acc, ob, rates, {**default_params(), **PRESETS["Severe stress"]}, asof)
    bc = list(base["daily"]["total_usd"]); sc = list(sev["daily"]["total_usd"])
    floor = float(base["daily"]["floor_usd"].iloc[0])
    gap_s = sev["summary"]["gap_usd"]
    cash = base["summary"]["opening_usd"]
    ev = qrows("SELECT memo,amount,currency FROM cash_events ORDER BY id DESC LIMIT 12") or \
        [{"memo": m[0], "amount": 12000, "currency": "USD"} for m in EVENT_MEMOS]
    rng = np.random.default_rng(BASE_SEED + int(get_meta("tick", "0")))
    rr = {c: rates[c] for c in FOREIGN}
    W, H = 960, 540
    lo = min(min(bc), min(sc), floor) * 0.97
    hi = max(max(bc), max(sc)) * 1.03
    cx0, cy0, cx1, cy1 = 24, 200, 620, 466
    frames = []
    f_title, f_lab, f_val, f_small = _font(25, True), _font(12, True), _font(28, True), _font(13)
    for i in range(n_frames):
        img = Image.new("RGB", (W, H), _rgb(INK))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W, 84], fill=_rgb(THEME["band"]))
        d.line([0, 84, W, 84], fill=_rgb(GOLD), width=2)
        d.text((24, 16), "AUREUS TREASURY — LIVE DEMO", font=f_title, fill=_rgb(GOLD))
        d.text((24, 50), "Consolidated cash · obligations · FX exposure — synthetic data", font=f_small, fill=_rgb(MUTE))
        d.rounded_rectangle([W - 208, 20, W - 24, 50], 8, fill=_rgb(THEME["badge_bg"]), outline=_rgb(GOLD))
        d.text((W - 194, 28), "SYNTHETIC DEMO", font=f_lab, fill=_rgb(GOLD))
        cash *= 1 + float(rng.normal(0, 0.0006))
        phase2 = i >= int(n_frames * 0.55)
        kpis = [("CONSOLIDATED CASH (LIVE)", money(cash, comp=True), GOLD),
                ("30-DAY TROUGH", money(base["summary"]["min_usd"], comp=True), BLUE),
                ("FUNDING GAP — SEVERE", money(gap_s, comp=True) if phase2 else "—", RED if phase2 else MUTE)]
        for j, (lab, val, col) in enumerate(kpis):
            x = 24 + j * 320
            d.rounded_rectangle([x, 100, x + 296, 176], 10, fill=_rgb(SURF), outline=_rgb(EDGE))
            d.text((x + 14, 110), lab, font=f_lab, fill=_rgb(MUTE))
            d.text((x + 14, 130), val, font=f_val, fill=_rgb(col))
        d.rectangle([cx0, cy0, cx1, cy1], outline=_rgb(EDGE))
        for g in range(1, 4):
            y = cy0 + g * (cy1 - cy0) // 4
            d.line([cx0, y, cx1, y], fill=_rgb(THEME["grid2"]))
            v = hi - g * (hi - lo) / 4
            d.text((cx0 + 4, y - 14), money(v, comp=True), font=f_lab, fill=_rgb(MUTE))

        def pts(curve, k):
            return [(cx0 + (cx1 - cx0) * j / (len(curve) - 1),
                     cy1 - (curve[j] - lo) / (hi - lo) * (cy1 - cy0)) for j in range(k)]
        k = max(2, int((i + 1) / n_frames * len(bc)))
        pb = pts(bc, k)
        d.polygon(pb + [(pb[-1][0], cy1), (pb[0][0], cy1)], fill=_rgb(THEME["area_fill"]))
        d.line(pb, fill=_rgb(GOLD), width=3, joint="curve")
        fy = cy1 - (floor - lo) / (hi - lo) * (cy1 - cy0)
        for x in range(cx0, cx1, 14):
            d.line([x, fy, x + 7, fy], fill=_rgb(AMBER), width=2)
        if phase2:
            k2 = max(2, int((i - n_frames * 0.55) / (n_frames * 0.45) * len(sc)))
            d.line(pts(sc, min(k2, len(sc))), fill=_rgb(RED), width=3, joint="curve")
            d.text((cx0 + 8, cy0 + 6), "severe stress", font=f_lab, fill=_rgb(RED))
        d.text((cx0 + 8, cy1 - 18), "baseline forecast", font=f_lab, fill=_rgb(GOLD))
        d.text((cx1 - 60, cy1 + 6), f"d{k}", font=f_lab, fill=_rgb(MUTE))
        px, py = 644, 200
        d.rounded_rectangle([px, py, 936, 466], 10, fill=_rgb(SURF), outline=_rgb(EDGE))
        d.text((px + 12, py + 8), "FX BOARD (USD per unit)", font=f_lab, fill=_rgb(MUTE))
        for j, ccy in enumerate(FOREIGN):
            prev = rr[ccy]
            rr[ccy] = float(np.clip(prev * (1 + rng.normal(0, 0.0012)), BASE_RATES[ccy] * .8, BASE_RATES[ccy] * 1.25))
            up = rr[ccy] >= prev
            y = py + 30 + j * 26
            d.rectangle([px + 12, y + 3, px + 22, y + 13], fill=_rgb(CCY_META[ccy]["color"]))
            d.text((px + 30, y), ccy, font=f_lab, fill=_rgb(TXT))
            d.text((px + 78, y), rate_fmt(ccy, rr[ccy]), font=f_small, fill=_rgb(TXT))
            d.text((px + 170, y), "▲" if up else "▼", font=f_small, fill=_rgb(GREEN if up else RED))
        d.text((px + 12, py + 168), "LIVE EVENTS", font=f_lab, fill=_rgb(MUTE))
        for j in range(3):
            e = ev[(i + j) % len(ev)]
            y = py + 188 + j * 24
            col = GREEN if e["amount"] > 0 else RED
            d.text((px + 12, y), ("+" if e["amount"] > 0 else "−") + money(abs(e["amount"]), e["currency"], comp=True),
                   font=f_lab, fill=_rgb(col))
            d.text((px + 92, y), str(e["memo"])[:26], font=f_small, fill=_rgb(MUTE))
        d.text((24, 482), f"SIM TICK {i + 1}/{n_frames}", font=f_lab, fill=_rgb(MUTE))
        d.text((640, 482), f"engine v{ENGINE_VERSION} · rules {RULES_VERSION}", font=f_lab, fill=_rgb(MUTE))
        d.rounded_rectangle([24, 504, 936, 514], 5, fill=_rgb(THEME["grid2"]))
        d.rounded_rectangle([24, 504, 24 + int(912 * (i + 1) / n_frames), 514], 5, fill=_rgb(GOLD))
        frames.append(img.convert("P", palette=Image.Palette.ADAPTIVE))
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:],
                   duration=duration, loop=0, optimize=True)
    return buf.getvalue()


# ----------------------------------------------------------------------------
# Report builder (approval-ready; fixed paper style for print, independent
# of the app theme by design)
# ----------------------------------------------------------------------------
def _tbl(headers: list[str], rows: list[list]) -> str:
    th = "".join(f"<th style='background:#F6F1E4;border:1px solid #E2DCC8;padding:6px 10px;text-align:left;font-size:12px;'>{h}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td style='border:1px solid #E7E3D4;padding:6px 10px;font-size:12px;'>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table style='border-collapse:collapse;width:100%;margin:10px 0;'><tr>{th}</tr>{trs}</table>"


def build_report(sc: dict) -> tuple[str, str]:
    p = json.loads(sc["params_json"]); r = json.loads(sc["results_json"])
    funding = json.loads(sc["funding_json"]); warns = json.loads(sc["warnings_json"])
    s = r["summary"]; ov = json.loads(sc["override_json"]) if sc.get("override_json") else None
    approved = sc["status"] == "approved"
    fig = go.Figure()
    dates = [d["date"] for d in r["daily"]]
    fig.add_trace(go.Scatter(x=dates, y=[d["total_usd"] for d in r["daily"]], mode="lines",
                             line=dict(color="#B8860B", width=3), name="Projected position"))
    fig.add_trace(go.Scatter(x=[dates[0], dates[-1]], y=[s["floor_usd"]] * 2, mode="lines",
                             line=dict(color="#C0392B", dash="dash", width=2), name="Policy floor"))
    fig.update_layout(height=340, paper_bgcolor="white", plot_bgcolor="white",
                      font=dict(color="#222", family="Georgia, serif"), margin=dict(l=10, r=10, t=30, b=10),
                      yaxis_tickprefix="$", yaxis_tickformat=".2s", legend=dict(orientation="h"))
    chart = fig.to_html(full_html=False, include_plotlyjs=True)
    fund_rows = [[a["action"], a["ccy"], money(a["amount_native"], a["ccy"]), money(a["usd"]),
                  f"{a['rate']:.5f}", money(a["cost_usd"]), a["note"]] for a in funding] or \
        [["—", "—", "—", "$0", "—", "$0", "No external funding required under this scenario"]]
    exp_rows = [[e["currency"], money(e["native_net"], e["currency"]), money(e["usd_net"]),
                 money(e["sens_1pct_usd"])] for e in r["exposure"]]
    wk = [r["daily"][i] for i in sorted({0, 7, 14, 21, 28, len(r["daily"]) - 1}) if i < len(r["daily"])]
    wk_rows = [[d["date"], money(d["total_usd"]), money(d["floor_usd"]),
                "BREACH" if d["total_usd"] < d["floor_usd"] else "ok"] for d in wk]
    banner = ("" if approved else
              f"<div style='background:#FFF4DC;border:1px solid #E8A33D;color:#7A5410;padding:10px 14px;border-radius:8px;font-weight:700;margin:10px 0;'>"
              f"WORKING VIEW — status: {sc['status'].upper()} · not approved for funding execution</div>")
    ov_html = (f"<p style='color:#7A5410;'><b>Reviewer override:</b> trough adjusted to {money(ov['value'])} — "
               f"“{ov['reason']}” (by {ov['by']}, {ov['ts']}). Engine value retained above.</p>" if ov else "")
    signoff = _tbl(["Role", "Name", "Date", "Signature"],
                   [["Prepared by", sc["created_by"], sc["created_at"][:10], "________________"],
                    ["Approved by", sc.get("decided_by") or "________", (sc.get("decided_at") or "________")[:10], "________________"]])
    html = f"""<div style="font-family:Georgia,serif;color:#1B1B1B;background:#FFFDF7;padding:28px;max-width:900px;">
      <div style="border-bottom:3px solid #B8860B;padding-bottom:8px;">
        <span style="font-size:26px;font-weight:700;color:#8A6A2F;">AUREUS</span>
        <span style="font-size:13px;color:#666;"> · Treasury Funding & FX Exposure Report</span></div>
      <h2 style="margin:14px 0 4px;">{sc['name']} <span style="font-size:14px;color:#888;">v{sc['version']}</span></h2>
      <p style="font-size:12px;color:#666;">Report ID SC-{sc['id']:04d} · generated {utcnow_iso()} ·
         engine v{sc['engine_version']} · rules {sc['rules_version']} · data checksum {sc['checksum']}</p>
      {banner}
      <h3>Executive summary</h3>
      {_tbl(["Metric", "Value"], [["Opening consolidated cash", money(s['opening_usd'])],
            [f"Projected trough ({s['min_date']})", money(s['min_usd'])],
            ["Policy floor", money(s['floor_usd'])], ["Funding gap", money(s['gap_usd'])],
            ["Floor-breach days", str(s['breach_days'])], ["Horizon-end position", money(s['end_usd'])],
            ["Gross FX exposure (USD equiv)", money(s['fx_exposure_usd'])]])}
      {ov_html}
      <h3>Projected consolidated position</h3>{chart}
      <h3>Recommended funding actions</h3>
      {_tbl(["Action", "Ccy", "Sell (native)", "USD raised", "Rate", "Spread cost", "Note"], fund_rows)}
      <h3>Weekly checkpoints</h3>{_tbl(["Date", "Position", "Floor", "Status"], wk_rows)}
      <h3>Currency exposure at horizon end</h3>
      {_tbl(["Currency", "Net (native)", "Net (USD)", "±1% FX sensitivity"], exp_rows)}
      <h3>Assumptions</h3>
      <ul style="font-size:12px;">
        <li>Horizon {p['horizon_days']} days; deterministic ledger roll-forward; rates = USD per unit, shocked by scenario.</li>
        <li>FX shocks (%): {json.dumps(p['shocks'])}</li>
        <li>Collection delay {p['collection_delay_days']}d · inflow haircut {p['inflow_haircut_pct']}% ·
            confidence weighting {'on' if p['weight_confidence'] else 'off'} · overdue {'included' if p['include_overdue'] else 'excluded'} ·
            buffer scale ×{p['buffer_scale']} · FX spread {p['spread_bps']} bps.</li>
        <li>Restricted cash excluded: {money(s['restricted_usd'])}. Sources: {s['n_accounts']} accounts, {s['n_obligations']} active flows.</li></ul>
      <h3>Data-quality warnings</h3>
      <ul style="font-size:12px;">{''.join(f'<li>{w}</li>' for w in warns) or '<li>None</li>'}</ul>
      <h3>Sign-off</h3>{signoff}
      <p style="font-size:11px;color:#777;border-top:1px solid #E2DCC8;padding-top:8px;">
        Decision support only — not financial advice. All funding actions require authorized human approval.
        This report was produced from a clearly-labeled SYNTHETIC demonstration dataset. No real customer data.</p></div>"""
    md = [f"# AUREUS Funding Report — {sc['name']} (v{sc['version']})",
          f"Status: **{sc['status'].upper()}** · engine v{sc['engine_version']} · rules {sc['rules_version']} · checksum {sc['checksum']}",
          "", "## Summary",
          f"- Opening: {money(s['opening_usd'])}", f"- Trough ({s['min_date']}): {money(s['min_usd'])}",
          f"- Floor: {money(s['floor_usd'])} · Gap: **{money(s['gap_usd'])}** · Breach days: {s['breach_days']}",
          "", "## Funding actions"]
    md += [f"- {a['action']} {a['ccy']} {money(a['amount_native'], a['ccy'])} → {money(a['usd'])} ({a['note']})"
           for a in funding] or ["- None required"]
    md += ["", "## Warnings"] + [f"- {w}" for w in warns or ["None"]]
    md += ["", f"Prepared by {sc['created_by']}; approved by {sc.get('decided_by') or '—'}.",
           "Decision support only. Synthetic demonstration data. Human approval required."]
    return "\n".join(md), html


# ----------------------------------------------------------------------------
# CSS template — %%tokens%% resolved from the active theme by build_css()
# ----------------------------------------------------------------------------
CSS_TEMPLATE = """
<style>
*{scrollbar-width:thin;scrollbar-color:%%scroll1%% %%scroll_track%%;}
*::-webkit-scrollbar{width:10px;height:10px;}
*::-webkit-scrollbar-track{background:%%scroll_track%%;}
*::-webkit-scrollbar-thumb{background:linear-gradient(180deg,%%scroll1%%,%%scroll2%%);border-radius:8px;border:2px solid %%scroll_track%%;}
*::-webkit-scrollbar-thumb:hover{background:%%scroll_hover%%;}
[data-testid="stAppViewContainer"]{background:%%app_bg%%;}
[data-testid="stSidebar"]{background:%%sidebar_bg%%;border-right:1px solid %%edge2%%;}
[data-testid="stHeader"]{background:transparent;}
#MainMenu,footer{display:none;}
.block-container{padding-top:1.1rem;max-width:1500px;}
h1,h2,h3{font-family:Georgia,'Times New Roman',serif;color:%%txt%%;letter-spacing:.3px;}
.brand{font-family:Georgia,serif;font-size:32px;font-weight:700;line-height:1;
 background:linear-gradient(90deg,%%brand1%%,%%brand2%%,%%brand3%%);-webkit-background-clip:text;background-clip:text;color:transparent;}
.brand-sub{color:%%mute%%;font-size:12px;letter-spacing:2.5px;text-transform:uppercase;margin-top:2px;}
.t-card{background:linear-gradient(160deg,%%surf%%,%%surf2%%);border:1px solid %%edge%%;border-radius:14px;
 padding:14px 16px;box-shadow:0 6px 22px rgba(0,0,0,.12);margin-bottom:6px;}
.kpi-label{color:%%mute%%;font-size:11px;letter-spacing:1.4px;text-transform:uppercase;font-weight:700;}
.kpi-value{font-family:'Cascadia Mono',Consolas,Menlo,monospace;font-size:27px;font-weight:700;margin:4px 0 2px;font-variant-numeric:tabular-nums;}
.kpi-sub{color:%%faint%%;font-size:11.5px;}
.t-alert{background:%%alert_bg%%;border:1px solid %%edge2%%;border-left:4px solid %%accent%%;
 border-radius:10px;padding:9px 13px;font-size:12.5px;color:%%txt%%;margin-bottom:6px;}
.clockchip{display:inline-flex;align-items:center;gap:9px;background:%%btn_bg%%;border:1px solid %%edge2%%;
 border-radius:10px;padding:7px 14px;font-family:Consolas,monospace;font-size:12.5px;color:%%txt%%;}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;}
.dot.live{background:%%pos%%;box-shadow:0 0 8px %%pos%%;animation:pulse 1.6s infinite;}
.dot.paused{background:%%warn%%;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.fx-chip{display:inline-flex;gap:8px;align-items:center;background:%%btn_bg%%;border:1px solid %%edge2%%;
 border-radius:10px;padding:6px 12px;font-family:Consolas,monospace;font-size:12.5px;color:%%txt%%;margin:2px;}
.feed{max-height:330px;overflow-y:auto;padding-right:6px;}
.feed-row{display:flex;justify-content:space-between;gap:10px;border-bottom:1px dashed %%edge2%%;
 padding:7px 2px;font-size:12.5px;color:%%txt%%;}
.stButton>button,.stDownloadButton>button{background:%%btn_bg%%;border:1px solid %%edge%%;color:%%txt%%;
 border-radius:10px;transition:all .18s ease;}
.stButton>button:hover,.stDownloadButton>button:hover{border-color:%%accent%%;color:%%accent%%;
 box-shadow:0 0 0 1px %%glow%%,0 4px 18px %%glowsoft%%;transform:translateY(-1px);}
button[kind="primary"],[data-testid="stBaseButton-primary"]{background:linear-gradient(135deg,%%accent_hi%%,%%accent%%)!important;
 color:%%primary_txt%%!important;border:none!important;font-weight:700!important;}
button[kind="primary"]:hover,[data-testid="stBaseButton-primary"]:hover{filter:brightness(1.06);color:%%primary_txt%%!important;}
[data-baseweb="select"]>div,input,textarea{background:%%input_bg%%!important;border-color:%%edge%%!important;color:%%txt%%!important;}
[data-testid="stExpander"]{border:1px solid %%edge2%%;border-radius:12px;background:%%expander_bg%%;}
[data-testid="stDataFrame"]{border:1px solid %%edge2%%;border-radius:12px;}
button[data-baseweb="tab"]{color:%%mute%%;}
button[data-baseweb="tab"][aria-selected="true"]{color:%%accent%%;}
[data-baseweb="tab-highlight"]{background-color:%%accent%%;}
[data-testid="stSlider"] [role="slider"]{background:%%accent%%;}
[data-testid="stProgress"]>div>div{background:linear-gradient(90deg,%%accent_lo%%,%%accent%%);}
.flow{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0;}
.flow .node{background:%%surf%%;border:1px solid %%edge%%;border-left:3px solid %%accent%%;border-radius:10px;
 padding:8px 12px;font-size:12px;color:%%txt%%;}
.flow .node.exc{border-left-color:%%neg%%;}
.flow .arrow{color:%%accent%%;font-weight:700;}
.small-note{color:%%faint%%;font-size:11.5px;}
</style>
"""


def build_css() -> str:
    t = THEME
    tokens = {
        "app_bg": t["app_bg"], "sidebar_bg": t["sidebar_bg"],
        "surf": t["surf"], "surf2": t["surf2"], "edge": t["edge"], "edge2": t["edge2"],
        "txt": t["txt"], "mute": t["mute"], "faint": t["faint"],
        "accent": t["accent"], "accent_hi": t["accent_hi"], "accent_lo": t["accent_lo"],
        "primary_txt": t["primary_txt"], "pos": t["pos"], "neg": t["neg"], "warn": t["warn"],
        "btn_bg": t["btn_bg"], "input_bg": t["input_bg"],
        "expander_bg": t["expander_bg"], "alert_bg": t["alert_bg"],
        "glow": _rgba(t["accent"], 0.28), "glowsoft": _rgba(t["accent"], 0.12),
        "scroll_track": t["ink"], "scroll1": t["scroll1"], "scroll2": t["scroll2"],
        "scroll_hover": t["accent"],
        "brand1": t["accent"], "brand2": t["accent_hi"], "brand3": t["accent_lo"],
    }
    css = CSS_TEMPLATE
    for k, v in tokens.items():
        css = css.replace("%%" + k + "%%", v)
    return css


# ----------------------------------------------------------------------------
# Shared UI: header heartbeat (owns the simulation on every page)
# ----------------------------------------------------------------------------
@st.fragment(run_every=2.0)
def heartbeat() -> None:
    playing = get_meta("playing", "1") == "1"
    if playing:
        for _ in range(int(st.session_state.get("hb_speed", 2))):
            sim_tick()
    ts = pd.Timestamp(get_meta("sim_ts"))
    tick = get_meta("tick", "0")
    c1, c2, c3 = st.columns([3.4, 1.1, 1.5])
    c1.markdown(f'<div class="clockchip"><span class="dot {"live" if playing else "paused"}"></span>'
                f'{"LIVE" if playing else "PAUSED"} · SIM {ts:%Y-%m-%d %H:%M} UTC · tick {tick}</div>',
                unsafe_allow_html=True)
    c2.button("⏸ Pause" if playing else "▶ Resume", on_click=_toggle_play, key="hb_play", use_container_width=True)
    c3.select_slider("Speed", options=[1, 2, 3, 4], value=2, key="hb_speed", label_visibility="collapsed")


def page_header(title: str, sub: str) -> None:
    st.markdown(f"## {title}")
    st.caption(sub)


# ----------------------------------------------------------------------------
# Page: Cockpit
# ----------------------------------------------------------------------------
@st.fragment(run_every=2.0)
def cockpit_body() -> None:
    acc = qdf("SELECT * FROM accounts ORDER BY account_id")
    ob = qdf("SELECT * FROM obligations ORDER BY due_date, obligation_id")
    rates, rts = get_rates()
    res = build_forecast(acc, ob, rates, default_params(), asof_date())
    s = res["summary"]
    recent = qdf("SELECT currency,rate FROM fx_rates ORDER BY id DESC LIMIT 60")
    prev = {}
    for c in FOREIGN:
        v = recent.loc[recent["currency"] == c, "rate"].tolist()
        prev[c] = v[1] if len(v) > 1 else (v[0] if v else rates[c])

    chips = "".join(
        f'<span class="fx-chip"><span style="color:{CCY_META[c]["color"]};font-weight:700;">{c}</span>'
        f'{rate_fmt(c, rates[c])}'
        f'<span style="color:{GREEN if rates[c] >= prev[c] else RED};">{"▲" if rates[c] >= prev[c] else "▼"}</span></span>'
        for c in FOREIGN)
    st.markdown(chips, unsafe_allow_html=True)

    head_tone = RED if s["headroom_usd"] < 0 else (AMBER if s["headroom_usd"] < 500_000 else GREEN)
    cols = st.columns(5)
    cards = [("Consolidated cash", money(s["opening_usd"], comp=True),
              f"{int((acc['restricted']==0).sum())} accounts · {acc['currency'].nunique()} currencies", GOLD),
             (f"{default_params()['horizon_days']}-day trough", money(s["min_usd"], comp=True), s["min_date"], BLUE),
             ("Floor headroom", money(s["headroom_usd"], comp=True), f"policy floor {money(s['floor_usd'], comp=True)}", head_tone),
             ("Gross FX exposure", money(s["fx_exposure_usd"], comp=True), f"±1% ⇒ {money(s['sens_total_1pct'], comp=True)}", CCY_META["GBP"]["color"]),
             ("Funding gap (base)", money(s["gap_usd"], comp=True),
              "no draw needed" if s["gap_usd"] < 1 else f"first breach {s['first_breach']}", GREEN if s["gap_usd"] < 1 else RED)]
    for col, (l, v, sub, tone) in zip(cols, cards):
        col.markdown(kpi_html(l, v, sub, tone), unsafe_allow_html=True)

    alerts = []
    if s["headroom_usd"] < 0:
        alerts.append(alert_html("bad", f"Baseline breaches the policy floor on {s['first_breach']} — gap {money(s['gap_usd'], comp=True)}. Open Scenario Lab → Review."))
    elif s["headroom_usd"] < 500_000:
        alerts.append(alert_html("warn", f"Thin baseline headroom: {money(s['headroom_usd'], comp=True)} on {s['min_date']} (Term Loan B bullet). Stress-test it in Scenario Lab."))
    else:
        alerts.append(alert_html("ok", f"Baseline clears the policy floor · minimum headroom {money(s['headroom_usd'], comp=True)}."))
    asof = asof_date()
    od = ob[(pd.to_datetime(ob["due_date"]) < asof) & (ob["status"] != "settled")]
    if len(od):
        alerts.append(alert_html("warn", f"{len(od)} overdue obligation(s) need attention — see Records (exception path)."))
    outs = ob[ob["direction"] == "outflow"]
    if len(outs) and outs["amount"].sum() > 0:
        big = outs.loc[outs["amount"].idxmax()]
        share = big["amount"] / outs["amount"].sum()
        if share > 0.20:
            alerts.append(alert_html("info", f"Concentration: “{big['counterparty']}” is {share:.0%} of scheduled outflows ({money(big['amount'], big['currency'], comp=True)})."))
    sim_ts = pd.Timestamp(get_meta("sim_ts"))
    stale = [(c, (sim_ts - pd.Timestamp(rts[c])).total_seconds() / 60) for c in FOREIGN if c in rts]
    if any(m > 45 for _, m in stale):
        alerts.append(alert_html("warn", "FX observations are stale (>45 sim-minutes) — resume the simulation or import market observations."))
    for a in alerts[:4]:
        st.markdown(a, unsafe_allow_html=True)

    left, right = st.columns([2.1, 1])
    with left:
        st.markdown("#### Consolidated projection · baseline")
        st.plotly_chart(fig_forecast(res), use_container_width=True, key="ck_main",
                        config={"displayModeBar": False})
        st.markdown("#### Net FX exposure by currency")
        st.plotly_chart(fig_exposure(res["exposure"]), use_container_width=True, key="ck_exp",
                        config={"displayModeBar": False})
    with right:
        st.markdown("#### Currency mix (available)")
        if s["opening_usd"] > 0:
            st.plotly_chart(fig_composition(acc, rates), use_container_width=True, key="ck_pie",
                            config={"displayModeBar": False})
        st.markdown("#### FX tape (last observations)")
        hist = qdf("SELECT currency,rate FROM fx_rates ORDER BY id DESC LIMIT 400")
        for c in FOREIGN:
            v = hist.loc[hist["currency"] == c, "rate"].tolist()[::-1][-60:]
            if len(v) > 1:
                st.caption(f"{c} · {rate_fmt(c, v[-1])}")
                st.plotly_chart(fig_spark(v, CCY_META[c]["color"]), use_container_width=True,
                                key=f"ck_sp_{c}", config={"displayModeBar": False})

    l2, r2 = st.columns([1.4, 1])
    with l2:
        st.markdown("#### Next 7 days — actionable obligations")
        up = ob[(pd.to_datetime(ob["due_date"]) >= asof) &
                (pd.to_datetime(ob["due_date"]) <= asof + pd.Timedelta(days=7)) &
                (ob["status"] != "settled")].copy()
        if len(up):
            up["USD equiv"] = [float(r.amount) * rates.get(r.currency, 1) for r in up.itertuples()]
            up["amount"] = [money(r.amount, r.currency) for r in up.itertuples()]
            st.dataframe(up[["due_date", "counterparty", "direction", "currency", "amount", "USD equiv", "status"]],
                         use_container_width=True, hide_index=True,
                         column_config={"USD equiv": st.column_config.NumberColumn(format="%.0f")})
        else:
            st.info("No obligations due in the next 7 days.")
    with r2:
        st.markdown("#### Live cash events")
        ev = qrows("SELECT ts,account_id,currency,amount,memo FROM cash_events ORDER BY id DESC LIMIT 9")
        rows = "".join(
            f'<div class="feed-row"><span>{pd.Timestamp(e["ts"]):%H:%M} · {e["memo"]}<br>'
            f'<span class="small-note">{e["account_id"]}</span></span>'
            f'<span style="color:{GREEN if e["amount"] > 0 else RED};font-family:Consolas,monospace;">'
            f'{"+" if e["amount"] > 0 else "−"}{money(abs(e["amount"]), e["currency"], comp=True)}</span></div>'
            for e in ev)
        st.markdown(f'<div class="feed">{rows}</div>', unsafe_allow_html=True)
        st.markdown("#### Simulator")
        st.slider("FX volatility ×", 0.2, 3.0, float(get_meta("vol", "1.0")), 0.1, key="ck_vol",
                  on_change=lambda: set_meta("vol", st.session_state.ck_vol))
        st.button("⚡ Inject −2% FX shock", on_click=inject_shock, key="ck_shock",
                  disabled=not can("run"), use_container_width=True)
        st.caption("Play/pause & speed live in the header on every page. Shocks are audited.")


def page_cockpit() -> None:
    page_header("Treasury Cockpit", "Live consolidated position, obligations and currency exposure — synthetic demo, decision support only.")
    cockpit_body()


# ----------------------------------------------------------------------------
# Page: Import & Quality
# ----------------------------------------------------------------------------
def sample_bytes(kind: str) -> bytes:
    if kind == "accounts":
        d = qdf("SELECT account_id,name,bank,entity,currency,balance,min_buffer,restricted FROM accounts LIMIT 6")
        d["account_id"] = "IMP-" + d["account_id"]
    elif kind == "obligations":
        d = qdf("SELECT obligation_id,counterparty,category,direction,currency,amount,due_date,confidence,status FROM obligations LIMIT 12")
        d["obligation_id"] = "IMP-" + d["obligation_id"]
    else:
        d = qdf("SELECT ts AS timestamp,currency,rate FROM fx_rates ORDER BY id DESC LIMIT 20")
    return d.to_csv(index=False).encode()


def page_import() -> None:
    page_header("Import & Data Quality", "CSV/XLSX ingestion with preview, row-level validation, error reports and preserved source files. Imports never overwrite existing records.")
    kind = st.radio("Dataset", ["accounts", "obligations", "fx_rates"], horizontal=True, key="im_kind")
    c1, c2, c3 = st.columns(3)
    c1.download_button(f"⬇ Valid sample · {kind}", sample_bytes(kind), f"sample_{kind}.csv", "text/csv", key="im_smp")
    c2.download_button("⬇ Malformed sample (obligations)", MALFORMED_CSV.encode(), "malformed_obligations.csv",
                       "text/csv", key="im_bad")
    c3.download_button("⬇ Empty template", (",".join(REQUIRED[kind]) + "\n").encode(),
                       f"template_{kind}.csv", "text/csv", key="im_tpl")
    st.caption("Valid samples use fresh IMP- identifiers so a full import succeeds end-to-end. Re-importing the same file demonstrates duplicate-key protection.")
    with st.expander("Schema contract & validation rules"):
        st.write({"required_columns": REQUIRED[kind],
                  "rules": ["identifiers: 3–40 chars [A-Za-z0-9_-], unique in file and database",
                            "currency ∈ " + ", ".join(CURRENCIES), "amounts: positive, ≤ 1e12, major units",
                            "due_date: ISO YYYY-MM-DD", "confidence: 0.0–1.0",
                            "fx rate: USD per 1 unit, 0 < rate ≤ 1000", "original file preserved with SHA-256 checksum"]})
    up = st.file_uploader("Upload CSV or XLSX", type=["csv", "xlsx"],
                          key=f"im_up_{kind}_{st.session_state.get('up_counter', 0)}")
    if up is not None:
        payload = up.getvalue()
        try:
            if up.name.lower().endswith(".xlsx"):
                raw = pd.read_excel(io.BytesIO(payload), dtype=str).fillna("")
            else:
                raw = pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False)
        except Exception as e:
            st.error(f"Could not parse file: {e}. Save as UTF-8 CSV or a plain .xlsx table and retry.")
            return
        with st.expander("Preview (first 20 rows)", expanded=True):
            st.dataframe(raw.head(20), use_container_width=True, hide_index=True)
        ids = set()
        if kind == "accounts":
            ids = set(qdf("SELECT account_id FROM accounts")["account_id"])
        elif kind == "obligations":
            ids = set(qdf("SELECT obligation_id FROM obligations")["obligation_id"])
        clean, errors, n = validate_import(kind, raw, ids)
        m1, m2, m3, m4 = st.columns(4)
        m1.markdown(kpi_html("Rows", str(n), up.name, BLUE), unsafe_allow_html=True)
        m2.markdown(kpi_html("Valid", str(len(clean)), "ready to commit", GREEN), unsafe_allow_html=True)
        m3.markdown(kpi_html("Rejected", str(len(errors)), "row-level errors", RED if errors else GREEN), unsafe_allow_html=True)
        m4.markdown(kpi_html("Accept rate", f"{(len(clean)/n*100 if n else 0):.0f}%", "valid / total", GOLD), unsafe_allow_html=True)
        if errors:
            st.markdown("##### Row-level errors — fix and re-upload")
            st.dataframe(pd.DataFrame(errors), use_container_width=True, hide_index=True)
            st.download_button("⬇ Error report (CSV)", pd.DataFrame(errors).to_csv(index=False).encode(),
                               f"errors_{up.name}.csv", "text/csv", key="im_err_dl")
        if clean:
            if st.button(f"✅ Commit {len(clean)} valid row(s)", type="primary", key="im_commit",
                         disabled=not can("import")):
                commit_import(kind, clean, errors, up.name, payload)
                st.session_state["up_counter"] = st.session_state.get("up_counter", 0) + 1
                st.toast(f"Imported {len(clean)} {kind} row(s); {len(errors)} rejected", icon="📥")
                st.rerun()
            if not can("import"):
                st.caption("Your role cannot commit imports (Analyst or Administrator required).")
    st.divider()
    st.markdown("#### Data-quality scorecard")
    acc, ob = qdf("SELECT * FROM accounts"), qdf("SELECT * FROM obligations")
    rates, rts = get_rates()
    asof, sim_ts = asof_date(), pd.Timestamp(get_meta("sim_ts"))
    used_ccy = set(acc["currency"]) | set(ob["currency"])
    stale_min = max(((sim_ts - pd.Timestamp(rts[c])).total_seconds() / 60 for c in FOREIGN if c in rts), default=0)
    od_n = int(((pd.to_datetime(ob["due_date"]) < asof) & (ob["status"] != "settled")).sum())
    low_n = int(((ob["direction"] == "inflow") & (ob["confidence"] < 0.7) & (ob["status"] != "settled")).sum())
    checks = [("Accounts present", len(acc) > 0, f"{len(acc)} accounts"),
              ("Obligations present", len(ob) > 0, f"{len(ob)} obligations"),
              ("All currencies have FX rates", not (used_ccy - set(rates)), f"missing: {sorted(used_ccy - set(rates)) or 'none'}"),
              ("FX freshness ≤ 45 sim-min", stale_min <= 45, f"oldest {stale_min:.0f} min"),
              ("Overdue items triaged", od_n == 0, f"{od_n} overdue — exception path in Records"),
              ("Low-confidence inflows reviewed", low_n <= 3, f"{low_n} below 0.7"),
              ("Restricted cash segregated", True, f"{money(acc.loc[acc['restricted']==1,'balance'].sum(), comp=True)} excluded"),
              ("Provenance on every record", bool(acc['provenance'].ne('').all() and ob['provenance'].ne('').all()), "source · file · row tracked")]
    passed = sum(1 for _, ok, _ in checks if ok)
    st.progress(passed / len(checks), text=f"{passed}/{len(checks)} checks passing")
    st.dataframe(pd.DataFrame([{"check": c, "status": "✅" if ok else "⚠️", "detail": d} for c, ok, d in checks]),
                 use_container_width=True, hide_index=True)
    st.markdown("#### Import history & lineage")
    hist = qdf("SELECT ts,filename,kind,rows_ok,rows_err,sha256,stored_path,actor FROM imports ORDER BY id DESC LIMIT 10")
    if len(hist):
        hist["sha256"] = hist["sha256"].str[:12]
        st.dataframe(hist, use_container_width=True, hide_index=True)
    else:
        st.info("No imports yet — original files, checksums and per-row provenance will appear here.")
    st.caption(f"Transformation versions: engine v{ENGINE_VERSION} · rules {RULES_VERSION} · schema v{SCHEMA_VERSION}. Every derived result is traceable to source file + row or simulation tick.")


# ----------------------------------------------------------------------------
# Page: Records (CRUD, search/filter/sort, undo, detail drawer)
# ----------------------------------------------------------------------------
def _search_filter(d: pd.DataFrame, search: str) -> pd.DataFrame:
    """Regex-safe, empty-table-safe full-row text search."""
    if search and len(d):
        hay = d.astype(str).agg(" ".join, axis=1).str.lower()
        d = d[hay.str.contains(search.lower(), regex=False)]
    return d


def _undo_banner() -> None:
    u = st.session_state.get("undo")
    if not u:
        return
    c1, c2 = st.columns([4, 1])
    c1.markdown(alert_html("warn", f"Deleted {u['table'][:-1]} <b>{u['id']}</b> — undo available."), unsafe_allow_html=True)
    if c2.button("↩ Undo delete", key="undo_btn", use_container_width=True):
        row = u["row"]; cols = list(row)
        ex(f"INSERT INTO {u['table']}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
           tuple(row[c] for c in cols))
        audit("undo_delete", f"{u['table']}:{u['id']}")
        st.session_state["undo"] = None
        st.toast("Record restored", icon="↩")
        st.rerun()


def _delete_row(table: str, idcol: str, rid: str) -> None:
    row = qrows(f"SELECT * FROM {table} WHERE {idcol}=?", (rid,))[0]
    ex(f"DELETE FROM {table} WHERE {idcol}=?", (rid,))
    st.session_state["undo"] = {"table": table, "id": rid, "row": row}
    audit("record_deleted", f"{table}:{rid}", {"undoable": True})
    st.toast(f"Deleted {rid} — undo on Records page", icon="🗑️")
    st.rerun()


def page_records() -> None:
    page_header("Records", "Accounts and obligations with live search, filters, editing, lineage and undo-able deletion.")
    _undo_banner()
    rates, _ = get_rates()
    asof = asof_date()
    ta, to = st.tabs(["🏦 Accounts", "🧾 Obligations"])

    with ta:
        acc = qdf("SELECT * FROM accounts ORDER BY account_id")
        f1, f2, f3 = st.columns([2, 1.4, 1])
        search = f1.text_input("Search accounts", key="ac_search", placeholder="name, bank, entity, id…")
        fccy = f2.multiselect("Currency", CURRENCIES, default=[], key="ac_ccy")
        frest = f3.selectbox("Availability", ["All", "Available", "Restricted"], key="ac_rest")
        d = _search_filter(acc.copy(), search)
        if fccy: d = d[d["currency"].isin(fccy)]
        if frest != "All": d = d[d["restricted"] == (1 if frest == "Restricted" else 0)]
        d["usd_value"] = [float(r.balance) * rates.get(r.currency, 1) for r in d.itertuples()]
        st.dataframe(d[["account_id", "name", "bank", "entity", "currency", "balance", "min_buffer",
                        "restricted", "usd_value", "updated_at"]].sort_values("usd_value", ascending=False),
                     use_container_width=True, hide_index=True,
                     column_config={"balance": st.column_config.NumberColumn(format="%.0f"),
                                    "min_buffer": st.column_config.NumberColumn(format="%.0f"),
                                    "usd_value": st.column_config.NumberColumn("USD value", format="%.0f")})
        if len(acc):
            sel = _select("Open account detail", acc["account_id"].tolist(), "ac_sel",
                          fmt=lambda i: f"{i} · {acc.loc[acc['account_id']==i,'name'].iloc[0]}")
            row = acc[acc["account_id"] == sel].iloc[0]
            c1, c2 = st.columns([1.4, 1])
            c1.markdown(
                f'<div class="t-card"><b style="color:{GOLD};">{row["name"]}</b> · {row["bank"]}<br>'
                f'{chip(row["currency"], CCY_META[row["currency"]]["color"])} '
                f'{" " + chip("RESTRICTED", RED) if row["restricted"] else chip("AVAILABLE", GREEN)}<br>'
                f'<span class="kpi-value">{money(row["balance"], row["currency"])}</span><br>'
                f'<span class="small-note">buffer {money(row["min_buffer"], row["currency"])} · entity {row["entity"]}<br>'
                f'provenance: {row["provenance"]} · created {row["created_at"]} · updated {row["updated_at"]}</span></div>',
                unsafe_allow_html=True)
            ev = qdf("SELECT ts,amount,memo FROM cash_events WHERE account_id=? ORDER BY id DESC LIMIT 6", (sel,))
            c2.dataframe(ev, use_container_width=True, hide_index=True,
                         column_config={"amount": st.column_config.NumberColumn(format="%.0f")})
            if can("edit"):
                with st.expander("✏️ Edit account"):
                    with st.form("ac_edit"):
                        n = st.text_input("Name", row["name"]); b = st.text_input("Bank", row["bank"])
                        e = st.text_input("Entity", row["entity"])
                        bal = st.number_input("Balance", value=float(row["balance"]), step=1000.0)
                        buf = st.number_input("Min buffer", value=float(row["min_buffer"]), step=1000.0)
                        rst = st.toggle("Restricted", bool(row["restricted"]))
                        if st.form_submit_button("Save changes", type="primary"):
                            raw = pd.DataFrame([{"account_id": sel, "name": n, "bank": b, "entity": e,
                                                 "currency": row["currency"], "balance": str(bal),
                                                 "min_buffer": str(buf), "restricted": str(int(rst))}])
                            others = set(acc["account_id"]) - {sel}
                            clean, errs, _ = validate_import("accounts", raw, others)
                            if errs:
                                st.dataframe(pd.DataFrame(errs), hide_index=True)
                            else:
                                r0 = clean[0]
                                ex("UPDATE accounts SET name=?,bank=?,entity=?,balance=?,min_buffer=?,restricted=?,updated_at=? WHERE account_id=?",
                                   (r0["name"], r0["bank"], r0["entity"], r0["balance"], r0["min_buffer"],
                                    r0["restricted"], utcnow_iso(), sel))
                                audit("account_updated", f"accounts:{sel}", {"balance": r0["balance"]})
                                st.toast("Account updated", icon="💾"); st.rerun()
            if can("delete") and st.button("🗑️ Delete account (undo available)", key="ac_del"):
                _delete_row("accounts", "account_id", sel)
        if can("edit"):
            with st.expander("➕ Add account"):
                with st.form("ac_add"):
                    nid = st.text_input("Account ID", f"ACC-NEW-{int(time.time()) % 100000}")
                    n = st.text_input("Name", "New operating account"); b = st.text_input("Bank", "")
                    e = st.text_input("Entity", "Meridian Payments Inc (US)")
                    c = st.selectbox("Currency", CURRENCIES)
                    bal = st.number_input("Opening balance", value=100000.0, step=1000.0)
                    buf = st.number_input("Min buffer", value=25000.0, step=1000.0)
                    rst = st.toggle("Restricted", False)
                    if st.form_submit_button("Create account", type="primary"):
                        raw = pd.DataFrame([{"account_id": nid, "name": n, "bank": b, "entity": e,
                                             "currency": c, "balance": str(bal), "min_buffer": str(buf),
                                             "restricted": str(int(rst))}])
                        clean, errs, _ = validate_import("accounts", raw, set(acc["account_id"]))
                        if errs:
                            st.dataframe(pd.DataFrame(errs), hide_index=True)
                        else:
                            commit_import("accounts", clean, [], f"manual:{nid}", b"manual entry")
                            st.toast("Account created", icon="🏦"); st.rerun()
        if not can("edit"):
            st.caption("Read-only for your role.")

    with to:
        ob = qdf("SELECT * FROM obligations ORDER BY due_date, obligation_id")
        f1, f2, f3, f4 = st.columns([2, 1, 1.2, 1.2])
        search = f1.text_input("Search obligations", key="ob_search", placeholder="counterparty, id, category…")
        fdir = f2.selectbox("Direction", ["All", "inflow", "outflow"], key="ob_dir")
        fst = f3.multiselect("Status", ["scheduled", "confirmed", "settled"], key="ob_st")
        fccy2 = f4.multiselect("Currency ", CURRENCIES, key="ob_ccy")
        win = st.slider("Due window (days from today)", -10, 60, (-10, 45), key="ob_win")
        d = ob.copy()
        d["due"] = pd.to_datetime(d["due_date"])
        d = _search_filter(d, search)
        if fdir != "All": d = d[d["direction"] == fdir]
        if fst: d = d[d["status"].isin(fst)]
        if fccy2: d = d[d["currency"].isin(fccy2)]
        d = d[(d["due"] >= asof + pd.Timedelta(days=win[0])) & (d["due"] <= asof + pd.Timedelta(days=win[1]))]
        d["overdue"] = np.where((d["due"] < asof) & (d["status"] != "settled"), "⚠️", "")
        d["usd_equiv"] = [float(r.amount) * rates.get(r.currency, 1) for r in d.itertuples()]
        st.dataframe(d[["overdue", "obligation_id", "counterparty", "category", "direction", "currency",
                        "amount", "usd_equiv", "due_date", "confidence", "status"]],
                     use_container_width=True, hide_index=True,
                     column_config={"amount": st.column_config.NumberColumn(format="%.0f"),
                                    "usd_equiv": st.column_config.NumberColumn("USD equiv", format="%.0f"),
                                    "confidence": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f")})
        if len(ob):
            sel = _select("Open obligation detail", ob["obligation_id"].tolist(), "ob_sel",
                          fmt=lambda i: f"{i} · {ob.loc[ob['obligation_id']==i,'counterparty'].iloc[0]}")
            row = ob[ob["obligation_id"] == sel].iloc[0]
            st.markdown(
                f'<div class="t-card"><b style="color:{GOLD};">{row["counterparty"]}</b> · {row["category"]}<br>'
                f'{chip(row["direction"].upper(), GREEN if row["direction"]=="inflow" else RED)} '
                f'{chip(row["currency"], CCY_META[row["currency"]]["color"])} {chip(row["status"].upper(), BLUE)}<br>'
                f'<span class="kpi-value">{money(row["amount"], row["currency"])}</span> '
                f'<span class="small-note">≈ {money(row["amount"]*rates.get(row["currency"],1), comp=True)} · due {row["due_date"]} · confidence {row["confidence"]:.2f}<br>'
                f'provenance: {row["provenance"]} · updated {row["updated_at"]}</span></div>',
                unsafe_allow_html=True)
            b1, b2, b3 = st.columns(3)
            if b1.button("✅ Mark settled", key="ob_settle", disabled=not can("edit") or row["status"] == "settled"):
                ex("UPDATE obligations SET status='settled', updated_at=? WHERE obligation_id=?", (utcnow_iso(), sel))
                audit("obligation_settled", f"obligations:{sel}")
                st.toast("Marked settled", icon="✅"); st.rerun()
            if b3.button("🗑️ Delete (undo available)", key="ob_del", disabled=not can("delete")):
                _delete_row("obligations", "obligation_id", sel)
            if can("edit"):
                with st.expander("✏️ Edit obligation"):
                    with st.form("ob_edit"):
                        cp = st.text_input("Counterparty", row["counterparty"])
                        cat = st.selectbox("Category", CATEGORIES,
                                           index=CATEGORIES.index(row["category"]) if row["category"] in CATEGORIES else 0)
                        dirc = st.selectbox("Direction", ["inflow", "outflow"], index=0 if row["direction"] == "inflow" else 1)
                        ccy = st.selectbox("Currency", CURRENCIES, index=CURRENCIES.index(row["currency"]))
                        amt = st.number_input("Amount", value=float(row["amount"]), step=1000.0)
                        due = st.date_input("Due date", date.fromisoformat(row["due_date"]))
                        cf = st.slider("Confidence", 0.0, 1.0, float(row["confidence"]), 0.05)
                        if st.form_submit_button("Save changes", type="primary"):
                            raw = pd.DataFrame([{"obligation_id": sel, "counterparty": cp, "category": cat,
                                                 "direction": dirc, "currency": ccy, "amount": str(amt),
                                                 "due_date": due.isoformat(), "confidence": str(cf), "status": row["status"]}])
                            clean, errs, _ = validate_import("obligations", raw, set(ob["obligation_id"]) - {sel})
                            if errs:
                                st.dataframe(pd.DataFrame(errs), hide_index=True)
                            else:
                                r0 = clean[0]
                                ex("UPDATE obligations SET counterparty=?,category=?,direction=?,currency=?,amount=?,due_date=?,confidence=?,updated_at=? WHERE obligation_id=?",
                                   (r0["counterparty"], r0["category"], r0["direction"], r0["currency"],
                                    r0["amount"], r0["due_date"], r0["confidence"], utcnow_iso(), sel))
                                audit("obligation_updated", f"obligations:{sel}", {"amount": r0["amount"]})
                                st.toast("Obligation updated", icon="💾"); st.rerun()
                with st.expander("➕ Add obligation"):
                    with st.form("ob_add"):
                        nid = st.text_input("Obligation ID", f"OBL-NEW-{int(time.time()) % 100000}")
                        cp = st.text_input("Counterparty", "New counterparty")
                        cat = st.selectbox("Category ", CATEGORIES)
                        dirc = st.selectbox("Direction ", ["inflow", "outflow"])
                        ccy = st.selectbox("Currency  ", CURRENCIES)
                        amt = st.number_input("Amount ", value=50000.0, step=1000.0)
                        due = st.date_input("Due date ", date.today() + pd.Timedelta(days=7))
                        cf = st.slider("Confidence ", 0.0, 1.0, 0.95, 0.05)
                        if st.form_submit_button("Create obligation", type="primary"):
                            raw = pd.DataFrame([{"obligation_id": nid, "counterparty": cp, "category": cat,
                                                 "direction": dirc, "currency": ccy, "amount": str(amt),
                                                 "due_date": due.isoformat(), "confidence": str(cf), "status": "scheduled"}])
                            clean, errs, _ = validate_import("obligations", raw, set(ob["obligation_id"]))
                            if errs:
                                st.dataframe(pd.DataFrame(errs), hide_index=True)
                            else:
                                commit_import("obligations", clean, [], f"manual:{nid}", b"manual entry")
                                st.toast("Obligation created", icon="🧾"); st.rerun()
                with st.expander("☑️ Bulk settle"):
                    bulk_opts = ob.loc[ob["status"] != "settled", "obligation_id"].tolist()
                    _msanitize("ob_bulk", bulk_opts)
                    ids = st.multiselect("Select scheduled/confirmed obligations", bulk_opts, key="ob_bulk")
                    if st.button(f"Settle {len(ids)} selected", key="ob_bulk_btn", disabled=not ids):
                        ph = ",".join("?" * len(ids))
                        ex(f"UPDATE obligations SET status='settled', updated_at=? WHERE obligation_id IN ({ph})",
                           (utcnow_iso(), *ids))
                        audit("bulk_settle", "obligations", {"ids": ids})
                        st.session_state["ob_bulk"] = []
                        st.toast(f"Settled {len(ids)} obligation(s)", icon="✅"); st.rerun()
        if not can("edit"):
            st.caption("Read-only for your role.")
    with st.expander("🕒 Recent audit events"):
        st.dataframe(qdf("SELECT ts,actor,role,action,target FROM audit ORDER BY id DESC LIMIT 15"),
                     use_container_width=True, hide_index=True)


# ----------------------------------------------------------------------------
# Page: Scenario Lab (permanent live control region, 13 tools)
# ----------------------------------------------------------------------------
LAB_DEFAULTS = {"lab_horizon": 30, "lab_delay": 0, "lab_haircut": 0.0, "lab_floor_m": 12.5,
                "lab_scale": 1.0, "lab_weight": True, "lab_overdue": True, "lab_spread": 15,
                "lab_preset": "Base", **{f"lab_shock_{c}": 0.0 for c in FOREIGN}}


def _apply_preset() -> None:
    name = st.session_state.get("lab_preset", "Base")
    p = {**default_params(), **PRESETS.get(name, {})}
    st.session_state["lab_delay"] = int(p["collection_delay_days"])
    st.session_state["lab_haircut"] = float(p["inflow_haircut_pct"])
    st.session_state["lab_spread"] = int(p["spread_bps"])
    st.session_state["lab_scale"] = float(p["buffer_scale"])
    for c in FOREIGN:
        st.session_state[f"lab_shock_{c}"] = float(p["shocks"].get(c, 0.0))


def _lab_reset() -> None:
    for k, v in LAB_DEFAULTS.items():
        st.session_state[k] = v


def _lab_randomize() -> None:
    rng = np.random.default_rng(time.time_ns() & 0xFFFFFFFF)
    for c in FOREIGN:
        st.session_state[f"lab_shock_{c}"] = float(round(rng.uniform(-10, 6) * 2) / 2)
    st.session_state["lab_delay"] = int(rng.integers(0, 11))
    st.session_state["lab_haircut"] = float(rng.integers(0, 26))
    st.session_state["lab_spread"] = int(rng.choice([10, 20, 35, 50]))
    st.session_state["lab_preset"] = "Custom"


def _lab_params() -> dict:
    g = st.session_state.get
    return {"horizon_days": int(g("lab_horizon", 30)),
            "shocks": {c: float(g(f"lab_shock_{c}", 0.0)) for c in FOREIGN},
            "collection_delay_days": int(g("lab_delay", 0)),
            "inflow_haircut_pct": float(g("lab_haircut", 0.0)),
            "floor_usd": float(g("lab_floor_m", 12.5)) * 1e6,
            "buffer_scale": float(g("lab_scale", 1.0)),
            "weight_confidence": bool(g("lab_weight", True)),
            "include_overdue": bool(g("lab_overdue", True)),
            "spread_bps": int(g("lab_spread", 15))}


def _save_scenario(submit: bool) -> None:
    name = (st.session_state.get("lab_name") or "").strip()
    if not name:
        st.toast("Give the scenario a name first", icon="✏️"); return
    acc, ob = qdf("SELECT * FROM accounts"), qdf("SELECT * FROM obligations")
    rates, _ = get_rates()
    p = _lab_params()
    res = build_forecast(acc, ob, rates, p, asof_date())
    now = utcnow_iso()
    sid = ex("""INSERT INTO scenarios(name,version,parent_id,status,params_json,results_json,funding_json,
                warnings_json,checksum,engine_version,rules_version,created_by,created_at,submitted_by,
                submitted_at,decided_by,decided_at,decision_note,override_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             (name, 1, None, "in_review" if submit else "draft", json.dumps(p),
              json.dumps(serialize_results(res)), json.dumps(res["funding"]), json.dumps(res["warnings"]),
              snapshot_checksum(), ENGINE_VERSION, RULES_VERSION, actor(), now,
              actor() if submit else None, now if submit else None, None, None, None, None))
    audit("scenario_submitted" if submit else "scenario_saved", f"scenario:{sid}", {"name": name})
    st.toast("Submitted for review" if submit else "Draft saved — see Review & Approvals", icon="🧪")


def page_lab() -> None:
    page_header("Scenario Lab", "Every control recomputes the forecast instantly. Deterministic engine — no black boxes; save to enter the approval workflow.")
    dis = not can("run")
    ctrl, main = st.columns([1, 2.3])
    with ctrl:
        with st.container(border=True):
            st.markdown("##### 🎛 Scenario controls · live")
            st.radio("Stress preset", list(PRESETS), key="lab_preset", on_change=_apply_preset, disabled=dis)
            st.slider("Horizon (days)", 14, 90, 30, key="lab_horizon", disabled=dis)
            st.markdown("**FX shocks vs market (%)**")
            for c in FOREIGN:
                st.slider(f"{c} shock %", -15.0, 15.0, 0.0, 0.5, key=f"lab_shock_{c}", disabled=dis)
            st.slider("Collection delay (days)", 0, 15, 0, key="lab_delay", disabled=dis)
            st.slider("Inflow haircut (%)", 0.0, 40.0, 0.0, 1.0, key="lab_haircut", disabled=dis)
            st.slider("Policy floor ($M)", 5.0, 25.0, 12.5, 0.5, key="lab_floor_m", disabled=dis)
            st.slider("Buffer tightness ×", 0.5, 2.0, 1.0, 0.05, key="lab_scale", disabled=dis)
            st.slider("FX spread (bps)", 0, 100, 15, key="lab_spread", disabled=dis)
            st.toggle("Probability-weight flows", True, key="lab_weight", disabled=dis)
            st.toggle("Include overdue at day 0", True, key="lab_overdue", disabled=dis)
            b1, b2 = st.columns(2)
            b1.button("↺ Reset", on_click=_lab_reset, key="lab_reset", use_container_width=True, disabled=dis)
            b2.button("🎲 Randomize", on_click=_lab_randomize, key="lab_rand", use_container_width=True, disabled=dis)
            st.download_button("⬇ Export params (JSON)", json.dumps(_lab_params(), indent=2).encode(),
                               "scenario_params.json", "application/json", key="lab_exp", use_container_width=True)
    with main:
        acc, ob = qdf("SELECT * FROM accounts"), qdf("SELECT * FROM obligations")
        rates, _ = get_rates()
        p = _lab_params()
        base_p = {**p, "shocks": {c: 0.0 for c in FOREIGN}, "collection_delay_days": 0, "inflow_haircut_pct": 0.0}
        try:
            scen = build_forecast(acc, ob, rates, p, asof_date())
            base = build_forecast(acc, ob, rates, base_p, asof_date())
        except EngineError as e:
            st.error(f"Engine validation: {e}")
            return
        s, b = scen["summary"], base["summary"]
        dtrough = s["min_usd"] - b["min_usd"]
        cols = st.columns(4)
        cards = [("Scenario trough", money(s["min_usd"], comp=True),
                  f"{'▲' if dtrough >= 0 else '▼'} {money(abs(dtrough), comp=True)} vs baseline",
                  GREEN if dtrough >= 0 else RED),
                 ("Horizon end", money(s["end_usd"], comp=True),
                  f"{'▲' if s['end_usd'] >= b['end_usd'] else '▼'} {money(abs(s['end_usd']-b['end_usd']), comp=True)} vs baseline", BLUE),
                 ("Floor-breach days", str(s["breach_days"]),
                  f"first: {s['first_breach'] or '—'}", RED if s["breach_days"] else GREEN),
                 ("Funding gap", money(s["gap_usd"], comp=True),
                  f"{len(scen['funding'])} recommended action(s)", RED if s["gap_usd"] > 1 else GREEN)]
        for col, (l, v, sub, tone) in zip(cols, cards):
            col.markdown(kpi_html(l, v, sub, tone), unsafe_allow_html=True)
        st.plotly_chart(fig_forecast(scen, base, 400), use_container_width=True, key="lab_main",
                        config={"displayModeBar": False})
        st.markdown("##### Recommended funding actions (deterministic, constraint-checked)")
        if scen["funding"]:
            fdf = pd.DataFrame([{"Action": a["action"], "Currency": a["ccy"],
                                 "Sell (native)": money(a["amount_native"], a["ccy"]),
                                 "USD raised": money(a["usd"]), "Rate used": f"{a['rate']:.5f}",
                                 "Spread cost": money(a["cost_usd"]), "Note": a["note"]} for a in scen["funding"]])
            st.dataframe(fdf, use_container_width=True, hide_index=True)
        else:
            st.success("No external funding required — projected position stays above the policy floor.")
        c1, c2 = st.columns(2)
        c1.markdown("##### End positions by currency")
        c1.plotly_chart(fig_ladder(base, scen), use_container_width=True, key="lab_ladder",
                        config={"displayModeBar": False})
        c2.markdown("##### Exposure & ±1% sensitivity")
        c2.plotly_chart(fig_exposure(scen["exposure"]), use_container_width=True, key="lab_exp_chart",
                        config={"displayModeBar": False})
        with st.expander("🔍 Explanation panel — inputs, rules, contributors, warnings"):
            e1, e2 = st.columns(2)
            e1.markdown(f"**Engine** v{ENGINE_VERSION} · **rules** {RULES_VERSION} · deterministic roll-forward, USD per-unit rates. "
                        "Baseline method: auditable ledger projection (no ML in MVP — ML inflow-timing model is post-MVP, "
                        "to be evaluated on time-split holdout with MAE/RMSE vs this baseline).")
            e2.json(p, expanded=False)
            st.markdown("**Largest outflows before trough**")
            st.dataframe(pd.DataFrame(scen["contributions"]["outflows"]), hide_index=True, use_container_width=True)
            st.markdown("**Largest inflows before trough**")
            st.dataframe(pd.DataFrame(scen["contributions"]["inflows"]), hide_index=True, use_container_width=True)
            for w in scen["warnings"]:
                st.markdown(alert_html("warn", w), unsafe_allow_html=True)
            st.caption("Uncertainty: confidence weights are judgmental probabilities, not calibrated forecasts. "
                       "Synthetic data validates plumbing only — no real-world predictive claim. Reviewer status: ad-hoc what-if until saved.")
        st.divider()
        s1, s2, s3 = st.columns([2, 1, 1])
        s1.text_input("Scenario name", f"Scenario {datetime.now():%Y-%m-%d %H:%M}", key="lab_name")
        s2.button("💾 Save draft", on_click=_save_scenario, args=(False,), key="lab_save",
                  disabled=not can("save"), use_container_width=True)
        s3.button("📤 Save & submit", on_click=_save_scenario, args=(True,), key="lab_submit",
                  disabled=not can("submit"), type="primary", use_container_width=True)


# ----------------------------------------------------------------------------
# Page: Review & Approvals (lifecycle, SoD, override, rollback)
# ----------------------------------------------------------------------------
def _load_into_lab() -> None:
    sc = st.session_state.get("rv_current")
    if not sc:
        return
    p = json.loads(sc["params_json"])
    st.session_state["lab_horizon"] = int(p["horizon_days"])
    st.session_state["lab_delay"] = int(p["collection_delay_days"])
    st.session_state["lab_haircut"] = float(p["inflow_haircut_pct"])
    st.session_state["lab_floor_m"] = float(p["floor_usd"]) / 1e6
    st.session_state["lab_scale"] = float(p["buffer_scale"])
    st.session_state["lab_weight"] = bool(p["weight_confidence"])
    st.session_state["lab_overdue"] = bool(p["include_overdue"])
    st.session_state["lab_spread"] = int(p["spread_bps"])
    for c in FOREIGN:
        st.session_state[f"lab_shock_{c}"] = float(p["shocks"].get(c, 0.0))
    st.session_state["lab_preset"] = "Custom"
    st.session_state["nav"] = PAGES[3]


def _recompute(sid: int) -> None:
    sc = qrows("SELECT * FROM scenarios WHERE id=?", (sid,))[0]
    acc, ob = qdf("SELECT * FROM accounts"), qdf("SELECT * FROM obligations")
    rates, _ = get_rates()
    res = build_forecast(acc, ob, rates, json.loads(sc["params_json"]), asof_date())
    ex("UPDATE scenarios SET results_json=?,funding_json=?,warnings_json=?,checksum=?,engine_version=?,rules_version=? WHERE id=?",
       (json.dumps(serialize_results(res)), json.dumps(res["funding"]), json.dumps(res["warnings"]),
        snapshot_checksum(), ENGINE_VERSION, RULES_VERSION, sid))
    audit("scenario_recomputed", f"scenario:{sid}")
    st.toast("Recomputed against current data", icon="🔁")


def page_review() -> None:
    page_header("Review & Approvals", "Lifecycle: draft → in review → approved / rejected → archived. Separation of duties enforced; every decision audited.")
    scs = qrows("SELECT * FROM scenarios ORDER BY created_at DESC, id DESC")
    if not scs:
        st.info("No scenarios yet — create one in the Scenario Lab.")
        return
    filt = st.multiselect("Filter status", list(STATUS_ICON), default=list(STATUS_ICON), key="rv_filt",
                          format_func=lambda s: f"{STATUS_ICON[s]} {s}")
    shown = [s for s in scs if s["status"] in filt] or scs
    labels = {s["id"]: f"{STATUS_ICON[s['status']]} {s['name']} · v{s['version']} · {s['status'].upper()}" for s in shown}
    sid = _select("Scenario", [s["id"] for s in shown], "rv_sel", fmt=lambda i: labels[i])
    sc = next(s for s in shown if s["id"] == sid)
    st.session_state["rv_current"] = sc
    r = json.loads(sc["results_json"]); s = r["summary"]
    funding = json.loads(sc["funding_json"]); warns = json.loads(sc["warnings_json"])
    if sc["checksum"] != snapshot_checksum():
        st.markdown(alert_html("warn", "Source data changed since this scenario was computed — recompute before deciding."), unsafe_allow_html=True)
    if sc["engine_version"] != ENGINE_VERSION:
        st.markdown(alert_html("warn", f"Computed with engine v{sc['engine_version']} (current v{ENGINE_VERSION}) — recompute required for approval."), unsafe_allow_html=True)
    k = st.columns(4)
    for col, (l, v, tone) in zip(k, [("Trough", money(s["min_usd"], comp=True), GOLD),
                                     ("Funding gap", money(s["gap_usd"], comp=True), RED if s["gap_usd"] > 1 else GREEN),
                                     ("Breach days", str(s["breach_days"]), RED if s["breach_days"] else GREEN),
                                     ("End position", money(s["end_usd"], comp=True), BLUE)]):
        col.markdown(kpi_html(l, v, sc["name"], tone), unsafe_allow_html=True)
    left, right = st.columns([1.5, 1])
    with left:
        st.markdown("##### Funding actions on record")
        if funding:
            st.dataframe(pd.DataFrame([{"Action": a["action"], "Ccy": a["ccy"],
                                        "Sell": money(a["amount_native"], a["ccy"]), "USD": money(a["usd"]),
                                        "Cost": money(a["cost_usd"]), "Note": a["note"]} for a in funding]),
                         hide_index=True, use_container_width=True)
        else:
            st.success("No external funding required.")
        st.markdown("##### Parameters")
        st.json(json.loads(sc["params_json"]), expanded=False)
        for w in warns:
            st.markdown(alert_html("warn", w), unsafe_allow_html=True)
        if sc.get("override_json"):
            ov = json.loads(sc["override_json"])
            st.markdown(alert_html("info", f"Reviewer override: trough → {money(ov['value'])} · “{ov['reason']}” ({ov['by']})"), unsafe_allow_html=True)
    with right:
        st.markdown("##### Decision")
        st.caption(f"Created by {sc['created_by']} · submitted by {sc.get('submitted_by') or '—'} · "
                   f"checksum {sc['checksum']} · engine v{sc['engine_version']}")
        note = st.text_area("Decision note (required, ≥ 10 chars for approve/reject)", key="rv_note", height=90)
        sod_block = sc.get("submitted_by") == actor()
        if sc["status"] == "draft":
            c1, c2 = st.columns(2)
            if c1.button("📤 Submit for review", key="rv_submit", disabled=not can("submit"), type="primary", use_container_width=True):
                ex("UPDATE scenarios SET status='in_review',submitted_by=?,submitted_at=? WHERE id=?", (actor(), utcnow_iso(), sid))
                audit("scenario_submitted", f"scenario:{sid}"); st.toast("Submitted", icon="📤"); st.rerun()
            c2.button("🔁 Recompute", on_click=_recompute, args=(sid,), key="rv_rec1", disabled=not can("run"), use_container_width=True)
            st.button("🧪 Load into Scenario Lab", on_click=_load_into_lab, key="rv_load", use_container_width=True)
        elif sc["status"] == "in_review":
            if sod_block:
                st.caption("🔒 Separation of duties: the submitter cannot approve their own scenario.")
            c1, c2 = st.columns(2)
            approve_ok = can("approve") and not sod_block
            if c1.button("✅ Approve", key="rv_app", disabled=not approve_ok, type="primary", use_container_width=True):
                if len(note.strip()) < 10:
                    st.error("Approvals require a decision note of at least 10 characters.")
                else:
                    ex("UPDATE scenarios SET status='approved',decided_by=?,decided_at=?,decision_note=? WHERE id=?",
                       (actor(), utcnow_iso(), note.strip(), sid))
                    audit("scenario_approved", f"scenario:{sid}", {"note": note.strip()})
                    st.toast("Approved — report is now approval-ready", icon="✅"); st.rerun()
            if c2.button("⛔ Reject", key="rv_rej", disabled=not can("approve"), use_container_width=True):
                if len(note.strip()) < 10:
                    st.error("Rejections require a decision note of at least 10 characters.")
                else:
                    ex("UPDATE scenarios SET status='rejected',decided_by=?,decided_at=?,decision_note=? WHERE id=?",
                       (actor(), utcnow_iso(), note.strip(), sid))
                    audit("scenario_rejected", f"scenario:{sid}", {"note": note.strip()})
                    st.toast("Rejected", icon="⛔"); st.rerun()
            st.button("🔁 Recompute against current data", on_click=_recompute, args=(sid,), key="rv_rec2",
                      disabled=not can("run"), use_container_width=True)
        elif sc["status"] in ("approved", "rejected"):
            c1, c2 = st.columns(2)
            if c1.button("↩ Reopen as new draft version", key="rv_reopen", disabled=not can("approve"), use_container_width=True):
                vmax = max(x["version"] for x in scs if x["name"] == sc["name"])
                ex("""INSERT INTO scenarios(name,version,parent_id,status,params_json,results_json,funding_json,
                      warnings_json,checksum,engine_version,rules_version,created_by,created_at,submitted_by,
                      submitted_at,decided_by,decided_at,decision_note,override_json)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (sc["name"], vmax + 1, sid, "draft", sc["params_json"], sc["results_json"], sc["funding_json"],
                    sc["warnings_json"], sc["checksum"], sc["engine_version"], sc["rules_version"],
                    actor(), utcnow_iso(), None, None, None, None, None, None))
                audit("scenario_reopened", f"scenario:{sid}", {"new_version": vmax + 1})
                st.toast(f"Reopened as v{vmax + 1} draft", icon="↩"); st.rerun()
            if c2.button("🗄️ Archive", key="rv_arch", disabled=not can("archive"), use_container_width=True):
                ex("UPDATE scenarios SET status='archived' WHERE id=?", (sid,))
                audit("scenario_archived", f"scenario:{sid}"); st.rerun()
        elif sc["status"] == "archived":
            if st.button("♻ Restore to draft", key="rv_restore", disabled=not can("archive")):
                ex("UPDATE scenarios SET status='draft' WHERE id=?", (sid,))
                audit("scenario_restored", f"scenario:{sid}"); st.rerun()
        if can("override") and sc["status"] in ("approved", "in_review"):
            with st.expander("✍️ Manual override (audited, engine value preserved)"):
                val = st.number_input("Adjusted trough (USD)", value=float(s["min_usd"]), step=10000.0, key="rv_ov_val")
                reason = st.text_input("Override reason", key="rv_ov_reason")
                if st.button("Apply override", key="rv_ov_btn"):
                    if len(reason.strip()) < 8:
                        st.error("Override requires a reason (≥ 8 chars).")
                    else:
                        ex("UPDATE scenarios SET override_json=? WHERE id=?",
                           (json.dumps({"field": "min_usd", "value": float(val), "reason": reason.strip(),
                                        "by": actor(), "ts": utcnow_iso()}), sid))
                        audit("scenario_overridden", f"scenario:{sid}", {"value": float(val), "reason": reason.strip()})
                        st.toast("Override recorded", icon="✍️"); st.rerun()
    st.divider()
    h1, h2 = st.columns(2)
    with h1:
        st.markdown("##### Version history & rollback")
        hist = [x for x in scs if x["name"] == sc["name"]]
        st.dataframe(pd.DataFrame([{"version": x["version"], "status": x["status"],
                                    "engine": x["engine_version"], "by": x["created_by"],
                                    "at": x["created_at"]} for x in hist]), hide_index=True, use_container_width=True)
        approved_old = [x for x in hist if x["status"] == "approved" and x["id"] != sid]
        if approved_old:
            rid = _select("Rollback source (approved)", [x["id"] for x in approved_old], "rv_rb_sel",
                          fmt=lambda i: f"v{next(x['version'] for x in hist if x['id']==i)} · SC-{i:04d}")
            if st.button("⏪ Rollback → new draft from approved version", key="rv_rb", disabled=not can("approve")):
                src = next(x for x in hist if x["id"] == rid)
                vmax = max(x["version"] for x in hist)
                ex("""INSERT INTO scenarios(name,version,parent_id,status,params_json,results_json,funding_json,
                      warnings_json,checksum,engine_version,rules_version,created_by,created_at,submitted_by,
                      submitted_at,decided_by,decided_at,decision_note,override_json)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (src["name"], vmax + 1, rid, "draft", src["params_json"], src["results_json"], src["funding_json"],
                    src["warnings_json"], src["checksum"], src["engine_version"], src["rules_version"],
                    actor(), utcnow_iso(), None, None, None, None, None, None))
                audit("scenario_rollback", f"scenario:{rid}", {"new_version": vmax + 1})
                st.toast("Rollback draft created — history preserved", icon="⏪"); st.rerun()
    with h2:
        st.markdown("##### Audit trail for this scenario")
        st.dataframe(qdf("SELECT ts,actor,role,action,detail_json FROM audit WHERE target=? ORDER BY id DESC LIMIT 12",
                         (f"scenario:{sid}",)), hide_index=True, use_container_width=True)


# ----------------------------------------------------------------------------
# Page: Reports
# ----------------------------------------------------------------------------
def page_reports() -> None:
    page_header("Reports & Export", "Approval-ready funding views with lineage, assumptions, warnings and sign-off. Exports are audited. Reports keep a fixed paper style for print.")
    scs = qrows("SELECT * FROM scenarios ORDER BY (status='approved') DESC, created_at DESC")
    if not scs:
        st.info("No scenarios to report on yet.")
        return
    labels = {s["id"]: f"{STATUS_ICON[s['status']]} {s['name']} · v{s['version']} · {s['status'].upper()}" for s in scs}
    sid = _select("Scenario", [s["id"] for s in scs], "rp_sel", fmt=lambda i: labels[i])
    sc = next(s for s in scs if s["id"] == sid)
    md, html = build_report(sc)
    c1, c2, c3, c4, c5 = st.columns(5)
    if c1.download_button("⬇ Report (HTML)", html.encode(), f"aureus_report_SC-{sid:04d}.html", "text/html", key="rp_html", type="primary", use_container_width=True):
        audit("report_exported", f"scenario:{sid}", {"format": "html"})
    c2.download_button("⬇ Report (MD)", md.encode(), f"aureus_report_SC-{sid:04d}.md", "text/markdown", key="rp_md", use_container_width=True)
    daily = pd.DataFrame(json.loads(sc["results_json"])["daily"])
    c3.download_button("⬇ Forecast (CSV)", daily.to_csv(index=False).encode(), f"forecast_SC-{sid:04d}.csv", "text/csv", key="rp_fc", use_container_width=True)
    c4.download_button("⬇ Funding actions (CSV)", pd.DataFrame(json.loads(sc["funding_json"])).to_csv(index=False).encode(),
                       f"funding_SC-{sid:04d}.csv", "text/csv", key="rp_fa", use_container_width=True)
    c5.download_button("⬇ Exposure (CSV)", pd.DataFrame(json.loads(sc["results_json"])["exposure"]).to_csv(index=False).encode(),
                       f"exposure_SC-{sid:04d}.csv", "text/csv", key="rp_ex", use_container_width=True)
    st.caption("HTML report is fully self-contained (chart JS embedded) and works offline. Draft/rejected reports carry a working-view watermark.")
    components_html(html, height=640, scrolling=True)
    st.divider()
    z1, z2 = st.columns(2)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("accounts.csv", qdf("SELECT * FROM accounts").to_csv(index=False))
        z.writestr("obligations.csv", qdf("SELECT * FROM obligations").to_csv(index=False))
        z.writestr("fx_rates.csv", qdf("SELECT * FROM fx_rates").to_csv(index=False))
        z.writestr("scenarios.json", json.dumps(qrows("SELECT * FROM scenarios"), indent=2))
        z.writestr("audit.csv", qdf("SELECT * FROM audit").to_csv(index=False))
        z.writestr("README.txt", "AUREUS export — SYNTHETIC demonstration data. Decision support only.\n"
                   f"engine v{ENGINE_VERSION} · rules {RULES_VERSION} · exported {utcnow_iso()}\n")
    if z1.download_button("📦 Full dataset & audit (ZIP)", buf.getvalue(), "aureus_dataset_export.zip",
                          "application/zip", key="rp_zip", use_container_width=True):
        audit("dataset_exported", "database", {"format": "zip"})
    z2.download_button("⬇ Audit log (CSV)", qdf("SELECT * FROM audit ORDER BY id").to_csv(index=False).encode(),
                       "audit_log.csv", "text/csv", key="rp_audit", use_container_width=True)


# ----------------------------------------------------------------------------
# Page: Demo Studio
# ----------------------------------------------------------------------------
DEMO_SCRIPT = """**60-second demo script**
1. **0:00 — Cockpit is already live.** Point at the pulsing SIM clock, FX tape and cash-event feed: "Balances, obligations and FX consolidated automatically — synthetic data, clearly labeled."
2. **0:10 — The story.** Baseline trough sits close to the policy floor around the Term Loan B bullet. Headroom card turns amber.
3. **0:20 — Scenario Lab.** Pick *Severe stress*: FX shocks, 7-day collection delay, 20% haircut recompute instantly; funding actions appear (sell safe EUR/GBP surplus, residual revolver draw).
4. **0:35 — Governance.** Save & submit → Review & Approvals: approve with a note (separation of duties enforced), audit trail visible.
5. **0:45 — Report.** Reports page: approval-ready HTML with lineage, checksums, assumptions, sign-off. Download.
6. **0:55 — This reel.** The GIF you are watching was rendered locally from the running simulation, in the active theme — no screen capture, no cloud, no API keys."""


def page_demo() -> None:
    page_header("Demo Studio", "A live demo video file, generated in-app from the running simulation — downloadable, shareable, and rendered in the active theme. Local Pillow rendering; no external services.")
    c1, c2, c3 = st.columns([1, 1, 1])
    n = c1.slider("Frames", 24, 72, 48, key="ds_frames")
    pace = c2.selectbox("Pace", ["Smooth", "Standard", "Slow"], index=1, key="ds_pace")
    dur = {"Smooth": 110, "Standard": 160, "Slow": 240}[pace]
    if c3.button("🔄 Regenerate now", key="ds_regen", use_container_width=True):
        st.session_state["demo_gif"] = None
    meta = (n, dur, snapshot_checksum(), get_meta("theme", DEFAULT_THEME))
    if st.session_state.get("demo_gif") is None or st.session_state.get("demo_meta") != meta:
        with st.spinner("Rendering live demo reel from the current simulation state…"):
            st.session_state["demo_gif"] = render_demo_gif(n, dur)
            st.session_state["demo_meta"] = meta
    gif = st.session_state["demo_gif"]
    st.image(gif, caption=f"AUREUS live demo reel · {n} frames · {len(gif)/1e6:.1f} MB · theme: {get_meta('theme', DEFAULT_THEME)} · generated {utcnow_iso()}",
             use_container_width=True)
    d1, d2 = st.columns([1, 2])
    d1.download_button("⬇ Download demo video (GIF)", gif, "aureus_demo_reel.gif", "image/gif",
                       key="ds_dl", type="primary", use_container_width=True)
    d2.caption("Animated GIF plays in browsers, LinkedIn and most chat tools. It shows the live cash KPI, the baseline forecast drawing in, the severe-stress overlay, the FX board and the event feed — all computed from this database. Switch the sidebar theme and the reel re-renders to match.")
    with st.expander("🎤 Demo script & talking points"):
        st.markdown(DEMO_SCRIPT)
    with st.expander("📎 Attach your own screen recording (optional)"):
        vid = st.file_uploader("MP4 / WebM / GIF", type=["mp4", "webm", "gif"], key="ds_attach")
        if vid is not None:
            data = vid.getvalue()
            if vid.name.lower().endswith(".gif"):
                st.image(data, use_container_width=True)
            else:
                st.video(data)
            st.download_button("⬇ Re-download attached video", data, vid.name, key="ds_attach_dl")


# ----------------------------------------------------------------------------
# Page: Setup & Source (clipboard, commands, tests, backup, docs)
# ----------------------------------------------------------------------------
DOC_BACKLOG = [
    ("MVP", "CSV/XLSX import + row-level validation + error reports", "Done"),
    ("MVP", "Deterministic multi-currency roll-forward forecast", "Done"),
    ("MVP", "Scenario lab (FX, delays, haircuts, buffers) + funding plan", "Done"),
    ("MVP", "Roles, review/approval lifecycle, SoD, audit, overrides", "Done"),
    ("MVP", "Approval-ready HTML/MD/CSV exports + lineage", "Done"),
    ("MVP", "Live simulation + in-app demo reel + self-tests + themes", "Done"),
    ("Post-MVP", "Bank-statement reconciliation matching", "Planned"),
    ("Post-MVP", "Inflow-timing ML vs deterministic baseline (time-split MAE/RMSE)", "Planned"),
    ("Post-MVP", "Scheduled imports, alerts, PostgreSQL team edition", "Planned"),
    ("Post-MVP", "Hedge proposal optimization with hard constraints", "Planned"),
    ("Enterprise", "SSO/directory integration, audit exports, SLAs", "Planned"),
    ("Enterprise", "ERP/TMS adapters via documented APIs (mocked first)", "Planned"),
    ("Enterprise", "Multi-entity consolidation & intercompany elimination", "Planned"),
]
DOC_RISKS = [
    ("Domain", "FX convention inverted (USD-per vs per-USD)", "M", "High", "Convention documented + hand-checkable unit tests + practitioner review"),
    ("Domain", "Buffers/floors mis-set → false comfort", "M", "High", "Policy config versioned; reviewer sign-off; sensitivity shown"),
    ("Data", "Late/stale market observations", "M", "Med", "Freshness checks, warnings on every output, sim freshness alert"),
    ("Data", "Duplicate or contradictory imports", "M", "Med", "Key constraints, never-overwrite policy, row-level errors"),
    ("Security", "Sensitive balances leaked via logs/exports", "M", "High", "Synthetic-only MVP; no sensitive logging; export audit; retention docs"),
    ("Adoption", "Treasurers distrust black-box output", "M", "High", "Deterministic engine, explanation panel, traceable lineage"),
    ("Operational", "DB corruption / interrupted import", "L", "High", "SQLite transactions, checksummed source files, backup/restore tested in-app"),
    ("Operational", "Version drift between rules and results", "M", "Med", "Engine/rules versions stamped; recompute banners; rollback"),
]
DOC_ACCESS = [(a, *["✔" if a in PERMS[r] else "—" for r in ROLES]) for a in
              ["import", "edit", "delete", "run", "save", "submit", "approve", "override", "archive", "export", "reset"]]
DOC_DICT = [
    ("accounts.balance", "REAL", "major units, native ccy", "Available or restricted cash", "finite, |x|≤1e12", "Internal"),
    ("accounts.min_buffer", "REAL", "major units", "Per-account liquidity buffer", "0..1e12", "Internal"),
    ("accounts.restricted", "INT", "flag", "Excluded from available cash", "0/1", "Internal"),
    ("obligations.amount", "REAL", "major units", "Signed by direction field", ">0, ≤1e12", "Confidential"),
    ("obligations.direction", "TEXT", "enum", "Cash sign", "inflow/outflow", "Internal"),
    ("obligations.due_date", "TEXT", "ISO date", "Settlement expectation", "YYYY-MM-DD", "Internal"),
    ("obligations.confidence", "REAL", "probability", "Judgmental collection/payment likelihood", "0..1", "Internal"),
    ("fx_rates.rate", "REAL", "USD per 1 unit", "Market observation", "0<r≤1000", "Public"),
    ("scenarios.params_json", "TEXT", "JSON", "Full scenario inputs (reproducible)", "engine-validated", "Internal"),
    ("scenarios.checksum", "TEXT", "sha256[:16]", "Data snapshot identity (drift detection)", "hex", "Internal"),
    ("*.provenance", "TEXT", "lineage", "synthetic-seed / import:file:row / simulation / manual", "non-empty", "Internal"),
    ("audit.detail_json", "TEXT", "JSON", "Material change evidence", "append-only", "Restricted"),
]
DOC_LICENSES = [("Streamlit", "Apache-2.0", "Yes", "UI framework"), ("pandas", "BSD-3-Clause", "Yes", "Data frames"),
                ("NumPy", "BSD-3-Clause", "Yes", "Numerics/RNG"), ("Plotly", "MIT", "Yes", "Charts (JS bundled in reports)"),
                ("Pillow", "MIT-CMU", "Yes", "Demo-reel rendering"), ("openpyxl", "MIT", "Yes", "XLSX I/O"),
                ("Python stdlib (sqlite3, zipfile…)", "PSF", "Yes", "Persistence & exports")]
ACCEPTANCE = [
    ("Import documented sample succeeds", "Import page → valid sample → 0 errors → commit", "manual-ready"),
    ("Malformed rows rejected with actionable errors", "Self-test: validator + duplicate-key", "auto"),
    ("Reproducible calculations", "Self-test: deterministic replay hash", "auto"),
    ("Hand-checkable engine math", "Self-test: 1000 −400 +200 roll-forward", "auto"),
    ("Traceable outputs", "Explanation panel + provenance + checksums on every report", "manual-ready"),
    ("Report export works", "Reports page → HTML/MD/CSV downloads", "manual-ready"),
    ("Approval requires human + SoD", "Review page: note ≥10 chars, submitter ≠ approver", "manual-ready"),
    ("Engine runs without UI", "Self-tests execute build_forecast directly", "auto"),
    ("Errors never corrupt records", "Transactional commits; never-overwrite import policy", "auto"),
    ("Clean-DB demo repeatability", "Sidebar reset (admin) reseeds identical synthetic dataset", "manual-ready"),
]


def _ensure_tests() -> None:
    if not st.session_state.get("tests"):
        st.session_state["tests"] = run_self_tests()
        st.session_state["tests_ts"] = utcnow_iso()


def page_setup() -> None:
    page_header("Setup & Source", "Clipboard-ready files, exact Windows commands, live self-tests, backup/restore and the governance pack.")
    t1, t2, t3, t4, t5 = st.tabs(["🚀 Run & Files", "🧪 Self-tests", "📋 Acceptance", "🗄️ Backup & Restore", "📚 Docs & Governance"])
    with t1:
        st.markdown("##### Windows PowerShell — from zero to running")
        st.code("""python -m venv .venv                 # create an isolated virtual environment
.\\.venv\\Scripts\\Activate.ps1        # activate it (this shell only)
pip install -r requirements.txt      # install the six open-source dependencies
streamlit run app.py                 # launch — demo data seeds & simulation starts automatically""",
                language="powershell")
        st.caption("If activation is blocked: `Set-ExecutionPolicy -Scope Process RemoteSigned` then retry. No API keys, works offline after install. Themes: sidebar → Theme (persisted; charts and demo reel follow).")
        req_path = Path("requirements.txt")
        req_txt = req_path.read_text(encoding="utf-8") if req_path.exists() else REQUIREMENTS_TXT
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("##### requirements.txt")
            st.code(req_txt, language="text")
            st.download_button("⬇ requirements.txt", req_txt.encode(), "requirements.txt", "text/plain", key="st_req_dl")
        with c2:
            st.markdown("##### app.py")
            src = Path(__file__).read_text(encoding="utf-8")
            st.caption(f"{len(src.splitlines())} lines · copy icon at the top-right of the code block puts it on your clipboard.")
            st.download_button("⬇ app.py", src.encode(), "app.py", "text/x-python", key="st_app_dl")
        with st.expander("View full app.py source (clipboard-ready)"):
            st.code(src, language="python")
    with t2:
        _ensure_tests()
        if st.button("🔁 Re-run self-tests", key="st_rerun"):
            st.session_state["tests"] = run_self_tests()
            st.session_state["tests_ts"] = utcnow_iso()
        res = st.session_state["tests"]
        passed = sum(1 for r in res if "PASS" in r["status"])
        if passed == len(res):
            st.success(f"{passed}/{len(res)} tests passing — executed live at {st.session_state['tests_ts']}")
        else:
            st.error(f"{passed}/{len(res)} passing — see failures below")
        st.dataframe(pd.DataFrame(res), use_container_width=True, hide_index=True)
        st.caption("These are real assertions executed in this process against the pure engine and validator — the same functions the UI calls. Extend with pytest for CI.")
    with t3:
        _ensure_tests()
        auto_ok = all("PASS" in r["status"] for r in st.session_state["tests"])
        rows = [{"criterion": c, "how verified": h,
                 "status": ("✅ PASS" if auto_ok else "❌ FAIL") if k == "auto" else "🔎 verify in UI"}
                for c, h, k in ACCEPTANCE]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption("Pass/fail is observable: automated rows come from the live self-test run; manual rows name the exact page and action. No customer-impact claims are made — pilot validation with real users is required.")
    with t4:
        st.markdown("##### Backup")
        with db() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        b1, b2 = st.columns(2)
        b1.download_button("⬇ Database backup (.db)", DB_PATH.read_bytes(),
                           f"aureus_backup_{datetime.now():%Y%m%d_%H%M%S}.db", "application/octet-stream", key="bk_dl")
        b2.caption("Consistent snapshot (WAL checkpointed). Source uploads are preserved separately under aureus_data/source_files with SHA-256 checksums in the imports table.")
        st.markdown("##### Restore (Administrator)")
        confirm = st.checkbox("I understand restore replaces the current local database", key="bk_confirm")
        upf = st.file_uploader("Backup file (.db)", type=["db"], key="bk_up")
        if st.button("♻ Restore database", key="bk_btn", disabled=not (confirm and upf is not None and role() == "Administrator")):
            data = upf.getvalue()
            if not data.startswith(b"SQLite format 3\x00"):
                st.error("Not a SQLite database file (bad header). Nothing was changed.")
            else:
                tmp = DATA_DIR / "restore_tmp.db"
                tmp.write_bytes(data)
                try:
                    conn = sqlite3.connect(tmp)
                    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    conn.close()
                    need = {"accounts", "obligations", "fx_rates", "scenarios", "audit", "meta"}
                    if not need.issubset(tables):
                        raise ValueError(f"missing tables: {sorted(need - tables)}")
                    (DATA_DIR / f"pre_restore_{datetime.now():%Y%m%d_%H%M%S}.db").write_bytes(DB_PATH.read_bytes())
                    DB_PATH.write_bytes(data)
                    for ext in ("-wal", "-shm"):
                        try:
                            Path(str(DB_PATH) + ext).unlink(missing_ok=True)
                        except OSError:
                            pass
                    tmp.unlink(missing_ok=True)
                    audit("database_restored", "database", {"filename": upf.name})
                    st.session_state["demo_gif"] = None
                    st.toast("Database restored — previous state kept as pre_restore_*.db", icon="♻")
                    st.rerun()
                except Exception as e:
                    tmp.unlink(missing_ok=True)
                    st.error(f"Restore rejected: {e}. Current database untouched.")
        st.caption("Retention: simulation FX/event tables auto-prune (4000/400 rows). User records persist until deleted (undoable) or reset. PostgreSQL migration path: same normalized schema, swap the connection layer, add server-side auth.")
    with t5:
        d1, d2, d3, d4 = st.tabs(["Brief & Workflow", "Backlog & Risks", "Data & Access", "Licenses & Runbook"])
        with d1:
            st.markdown("""**Product brief.** Treasury teams at payment companies and fintechs consolidate balances across banks and currencies in spreadsheets, chase obligation dates over email, and discover funding gaps late. AUREUS consolidates accounts, obligations and FX observations into one deterministic daily forecast, stresses it with live scenarios, and produces an approval-ready funding view with lineage. **Initial narrow workflow:** daily multi-currency cash position → 30-day forecast → stress scenario → reviewed funding plan → exported report. **Personas:** Analyst (enters/imports, runs scenarios), Reviewer (approves with note, SoD), Administrator (config, reset, archive), Stakeholder (read-only, exports). **Success criteria (to be measured in a pilot, not claimed):** time-to-funding-view, import error rate, forecast reproducibility, approval turnaround. **Out of scope (MVP):** autonomous execution, lending decisions, real bank connectivity, regulated advice.""")
            st.markdown("**Workflow — actors, entry, review, approval, exceptions**")
            st.markdown("""<div class="flow">
<span class="node">👩‍💻 Analyst · banks/CSV/XLSX</span><span class="arrow">→</span>
<span class="node">📥 Ingest + validate (preview, row errors)</span><span class="arrow">→</span>
<span class="node">🧮 Deterministic engine (v-stamped)</span><span class="arrow">→</span>
<span class="node">🧪 Scenario & funding plan</span><span class="arrow">→</span>
<span class="node">🔎 Reviewer (SoD, note)</span><span class="arrow">→</span>
<span class="node">✅ Approved view</span><span class="arrow">→</span>
<span class="node">📄 Export + audit</span></div>
<div class="flow">
<span class="node exc">⚠️ missing / malformed / duplicate / late / out-of-range</span><span class="arrow">→</span>
<span class="node exc">row-level rejection + error report</span><span class="arrow">→</span>
<span class="node exc">fix & safe re-import (never overwrite)</span><span class="arrow">→</span>
<span class="node exc">overdue triage in Records</span></div>""", unsafe_allow_html=True)
            st.markdown("**Lifecycle:** draft → in_review → approved / rejected → (reopen v+1) → archived → exported. Corrections create new versions; approved records are immutable; rollback copies an approved version into a new draft.")
        with d2:
            st.markdown("**Prioritized backlog**")
            st.dataframe(pd.DataFrame(DOC_BACKLOG, columns=["tier", "capability", "status"]), hide_index=True, use_container_width=True)
            st.markdown("**Risk register**")
            st.dataframe(pd.DataFrame(DOC_RISKS, columns=["category", "risk", "likelihood", "impact", "mitigation"]), hide_index=True, use_container_width=True)
            st.markdown("**Release tiers (pricing hypotheses — unvalidated):** Local MVP (free evaluation, one operator) · Team edition (shared PostgreSQL, roles, approvals; per-seat hypothesis) · Enterprise (SSO, audit exports, deployment controls, annual support). Willingness-to-pay must be tested via interviews and a pilot.")
        with d3:
            st.markdown("**Data dictionary (core fields)**")
            st.dataframe(pd.DataFrame(DOC_DICT, columns=["field", "type", "unit", "meaning", "validation", "sensitivity"]), hide_index=True, use_container_width=True)
            st.markdown("**Access-control matrix** (enforced in handlers, not only hidden in UI)")
            st.dataframe(pd.DataFrame(DOC_ACCESS, columns=["action", *ROLES]), hide_index=True, use_container_width=True)
            st.markdown("**Threat model & handling:** parameterized SQL only · uploads parsed as data, size-bounded, checksummed · no secrets in code (none needed) · no sensitive values in logs · exports audited · HTTPS + server-side authn/authz required before any multi-user deployment · synthetic data only until permissioned real data with governance.")
        with d4:
            st.markdown("**License inventory / SBOM** — all dependencies are free, open-source and commercial-compatible; versions resolve at install time and should be pinned + scanned before production release.")
            st.dataframe(pd.DataFrame(DOC_LICENSES, columns=["component", "license", "commercial use", "role"]), hide_index=True, use_container_width=True)
            st.markdown("""**Operational runbook:** ① health = Cockpit clock advancing + self-tests green ② backup via Setup tab before upgrades ③ upgrade = backup → replace app.py → run self-tests → verify report ④ incident = pause simulation, export audit, restore last backup ⑤ support boundary = decision support; funding execution stays in authorized human hands.
**90-day roadmap (pilot-driven):** days 1–30 onboard 2–3 design partners on synthetic + permissioned data, measure time-to-view; 31–60 reconciliation matching + PostgreSQL team edition; 61–90 ML timing baseline comparison (time-split MAE/RMSE) and hedge optimization with hard-constraint tests.""")


# ----------------------------------------------------------------------------
# Sidebar & main
# ----------------------------------------------------------------------------
def sidebar() -> str:
    with st.sidebar:
        st.markdown(f'<div class="brand">{APP_NAME}</div><div class="brand-sub">{TAGLINE}</div>', unsafe_allow_html=True)
        st.divider()
        names = list(THEMES)
        cur = get_meta("theme", DEFAULT_THEME)
        st.selectbox("🎨 Theme", names, index=names.index(cur) if cur in names else 0,
                     key="theme_sel", on_change=_set_theme)
        st.caption("Theme persists and restyles charts, cards and the demo reel.")
        st.selectbox("Role", ROLES, index=0, key="role")
        st.text_input("Your name (audit identity)", "alex.morgan", key="user_name")
        st.caption("Permissions: " + ", ".join(sorted(PERMS[role()])))
        page = st.radio("Navigation", PAGES, key="nav", label_visibility="collapsed")
        st.divider()
        st.markdown(chip("SYNTHETIC DEMO DATA", AMBER), unsafe_allow_html=True)
        age = (date.today() - date.fromisoformat(get_meta("seed_date", date.today().isoformat()))).days
        if age > 7:
            st.caption(f"⚠️ Demo dataset is {age} days old — consider a reseed.")
        if role() == "Administrator":
            with st.expander("🧨 Reset demo database"):
                ok = st.checkbox("Wipe local data and reseed the identical synthetic dataset", key="sb_confirm")
                if st.button("Reset & reseed", key="sb_reset", disabled=not ok, use_container_width=True):
                    with db() as c:
                        for t in ("accounts", "obligations", "fx_rates", "cash_events", "scenarios", "imports", "audit", "meta"):
                            c.execute(f"DROP TABLE IF EXISTS {t}")
                    ensure_db()
                    st.session_state["demo_gif"] = None
                    st.session_state["undo"] = None
                    st.session_state["tests"] = None
                    st.toast("Clean database reseeded — demo restarts from tick 0", icon="🧨")
                    st.rerun()
        st.caption(f"engine v{ENGINE_VERSION} · rules {RULES_VERSION} · schema v{SCHEMA_VERSION}")
        st.caption("Decision support only — not financial advice. All funding actions require authorized human approval.")
    return page


def main() -> None:
    st.set_page_config(page_title=f"{APP_NAME} — Treasury & FX Command", page_icon="🏛️", layout="wide")
    ensure_db()
    apply_theme(get_meta("theme", DEFAULT_THEME))
    st.markdown(build_css(), unsafe_allow_html=True)
    for k, v in {"up_counter": 0, "undo": None, "demo_gif": None}.items():
        st.session_state.setdefault(k, v)
    page = sidebar()
    h1, h2 = st.columns([1.2, 2])
    h1.markdown(f'<div class="brand">{APP_NAME}</div><div class="brand-sub">{TAGLINE}</div>', unsafe_allow_html=True)
    with h2:
        heartbeat()
    st.divider()
    if page == PAGES[0]: page_cockpit()
    elif page == PAGES[1]: page_import()
    elif page == PAGES[2]: page_records()
    elif page == PAGES[3]: page_lab()
    elif page == PAGES[4]: page_review()
    elif page == PAGES[5]: page_reports()
    elif page == PAGES[6]: page_demo()
    else: page_setup()
    st.markdown('<div class="small-note" style="text-align:center;margin-top:18px;">'
                f'AUREUS v{ENGINE_VERSION} · synthetic demonstration data · deterministic, traceable, human-approved · '
                'no API keys · runs fully offline</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()