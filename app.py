"""
NIFTY Swing Trading Screener — mobile-friendly Streamlit app.

Ported from a Google Colab notebook. Same Stage 1 (technical) + Stage 2
(earnings) logic as the original script, now driven by on-screen
parameters instead of hardcoded constants. Results are shown on screen;
Excel files can be auto-saved to Google Drive (optional, see
SETUP_GUIDE.md) and are also available as direct downloads.
"""

import io
import math
import time
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# ============================================================
# FIXED SETTINGS
# ============================================================

HISTORY_PERIOD = "5y"
NIFTY200_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

STATUS_BUY = "🟢 BUY CANDIDATE"
STATUS_WATCH = "🟡 WATCH"
STATUS_DEVELOPING = "🟡 DEVELOPING WATCH"
STATUS_SKIP = "🔴 SKIP"
STATUS_SKIP_EARNINGS = "🔴 SKIP - EARNINGS SOON"
STATUS_VERIFY_EARNINGS = "🟠 VERIFY EARNINGS"
EARNINGS_CLEAR = "🟢 CLEAR"
EARNINGS_UNKNOWN = "🟠 UNKNOWN"


def earnings_within_label(days):
    return f"🔴 WITHIN {days} DAYS"


st.set_page_config(page_title="NIFTY Swing Screener", page_icon="📈", layout="wide")


# ============================================================
# OPTIONAL PASSWORD GATE
# Only active if you add app_password to Streamlit secrets.
# ============================================================

def check_password():
    try:
        required = st.secrets.get("app_password")
    except Exception:
        required = None

    if not required:
        return True

    def _check():
        st.session_state["_pw_ok"] = st.session_state.get("_pw_input") == required

    if st.session_state.get("_pw_ok"):
        return True

    st.text_input("Password", type="password", key="_pw_input", on_change=_check)
    if "_pw_ok" in st.session_state and not st.session_state["_pw_ok"]:
        st.error("Incorrect password.")
    return False


if not check_password():
    st.stop()


st.title("📈 NIFTY Swing Trading Screener")
st.caption("Stage 1 technical scan + Stage 2 earnings check — same rules as your Colab scanner.")


# ============================================================
# INDICATORS
# ============================================================

def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def calculate_sma(series, period):
    return series.rolling(period, min_periods=period).mean()


def calculate_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_atr(df, period=14):
    previous_close = df["Close"].shift(1)
    tr1 = df["High"] - df["Low"]
    tr2 = (df["High"] - previous_close).abs()
    tr3 = (df["Low"] - previous_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


# ============================================================
# UNIVERSE
# ============================================================

@st.cache_data(ttl=3600, show_spinner=False)
def get_nifty200_symbols():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
        ),
        "Referer": "https://www.niftyindices.com/",
    }
    try:
        response = requests.get(NIFTY200_URL, headers=headers, timeout=30)
        response.raise_for_status()
        df = pd.read_csv(io.StringIO(response.text))
        if "Symbol" not in df.columns:
            raise ValueError("Symbol column not found.")
        symbols = df["Symbol"].dropna().astype(str).str.strip().tolist()
        symbols = list(dict.fromkeys(symbols))
        if len(symbols) < 150:
            raise ValueError(f"Only {len(symbols)} stocks received.")
        return symbols
    except Exception as e:
        st.error(f"Could not load the NIFTY 200 list: {e}")
        return []


def get_universe():
    if universe_choice == "NIFTY 200":
        return get_nifty200_symbols()

    raw = custom_text.replace("\n", ",").split(",")
    symbols = [s.strip().upper() for s in raw if s.strip()]
    symbols = [s[:-3] if s.endswith(".NS") else s for s in symbols]

    seen = set()
    cleaned = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            cleaned.append(s)
    return cleaned


# ============================================================
# DATA DOWNLOAD
# ============================================================

def download_nifty50():
    try:
        df = yf.download(
            "^NSEI", period=HISTORY_PERIOD, interval="1d",
            auto_adjust=False, progress=False, threads=False,
        )
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception:
        return None


def download_stock(symbol):
    try:
        df = yf.download(
            symbol + ".NS", period=HISTORY_PERIOD, interval="1d",
            auto_adjust=False, progress=False, threads=False,
        )
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        required = ["Open", "High", "Low", "Close", "Volume"]
        if not all(c in df.columns for c in required):
            return None
        df = df[required].copy()
        df.dropna(inplace=True)
        if len(df) < 260:
            return None
        return df
    except Exception:
        return None


def add_indicators(df):
    df = df.copy()
    df["EMA20"] = calculate_ema(df["Close"], 20)
    df["EMA50"] = calculate_ema(df["Close"], 50)
    df["SMA200"] = calculate_sma(df["Close"], 200)
    df["RSI14"] = calculate_rsi(df["Close"], 14)
    df["ATR14"] = calculate_atr(df, 14)
    df["AvgVolume20"] = df["Volume"].rolling(20, min_periods=20).mean()
    df["Previous20DHigh"] = df["High"].shift(1).rolling(20, min_periods=20).max()
    df["52WHigh"] = df["High"].rolling(252, min_periods=252).max()
    return df


def calculate_nifty_regime():
    df = download_nifty50()
    if df is None:
        return None
    df["EMA20"] = calculate_ema(df["Close"], 20)
    df["EMA50"] = calculate_ema(df["Close"], 50)
    row = df.iloc[-2]
    close = float(row["Close"])
    ema20 = float(row["EMA20"])
    ema50 = float(row["EMA50"])
    bullish = (close > ema20) and (ema20 > ema50)
    return {"bullish": bullish, "close": close, "ema20": ema20, "ema50": ema50}


# ============================================================
# STAGE 1 — TECHNICAL SCAN
# ============================================================

def scan_stock(symbol):
    df = download_stock(symbol)
    if df is None:
        return None

    df = add_indicators(df)
    row = df.iloc[-2]
    signal_date = df.index[-2]

    close = float(row["Close"])
    ema20 = float(row["EMA20"])
    ema50 = float(row["EMA50"])
    sma200 = float(row["SMA200"])
    rsi14 = float(row["RSI14"])
    atr14 = float(row["ATR14"])
    volume = float(row["Volume"])
    avg_volume20 = float(row["AvgVolume20"])
    previous_20d_high = float(row["Previous20DHigh"])
    high_52w = float(row["52WHigh"])

    values = [close, ema20, ema50, sma200, rsi14, atr14, volume, avg_volume20, previous_20d_high, high_52w]
    if any(pd.isna(x) for x in values):
        return None

    close_above_ema20 = close > ema20
    ema20_above_ema50 = ema20 > ema50
    ema50_above_sma200 = ema50 > sma200
    trend_pass = close_above_ema20 and ema20_above_ema50 and ema50_above_sma200

    rsi_pass = RSI_MIN <= rsi14 <= RSI_MAX

    distance_from_52w = (high_52w - close) / high_52w
    near_52w_pass = distance_from_52w <= MAX_DISTANCE_52W

    breakout_pass = close > previous_20d_high

    volume_ratio = volume / avg_volume20
    volume_pass = volume_ratio >= MIN_VOLUME_RATIO

    max_entry_price = previous_20d_high * (1 + MAX_ENTRY_EXTENSION)
    extension_pass = close <= max_entry_price

    failed_conditions = []
    if not close_above_ema20:
        failed_conditions.append("🔴 Close < 20 EMA")
    if not ema20_above_ema50:
        failed_conditions.append("🔴 20 EMA < 50 EMA")
    if not ema50_above_sma200:
        failed_conditions.append("🔴 50 EMA < 200 SMA")
    if not rsi_pass:
        if rsi14 < RSI_MIN:
            failed_conditions.append(f"🔴 RSI below {RSI_MIN}")
        elif rsi14 > RSI_MAX:
            failed_conditions.append(f"🔴 RSI above {RSI_MAX}")
    if not near_52w_pass:
        failed_conditions.append(f"🔴 More than {MAX_DISTANCE_52W * 100:.0f}% below 52W High")
    if not breakout_pass:
        failed_conditions.append("🔴 No 20D breakout")
    if not volume_pass:
        failed_conditions.append(f"🔴 Volume < {MIN_VOLUME_RATIO}× Avg20")
    if not extension_pass:
        failed_conditions.append(f"🔴 Price > Breakout + {MAX_ENTRY_EXTENSION * 100:.0f}%")

    technical_pass = trend_pass and rsi_pass and near_52w_pass and breakout_pass and volume_pass and extension_pass

    entry = close
    stop = entry - ATR_MULTIPLIER * atr14
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return None

    quantity = math.floor(RISK_AMOUNT / risk_per_share)
    actual_risk = quantity * risk_per_share
    capital_required = quantity * entry
    target_2r = entry + 2 * risk_per_share

    score = 0
    if trend_pass:
        score += 30
    if rsi_pass:
        score += 10
    if near_52w_pass:
        score += 10
    if breakout_pass:
        score += 20
    if volume_pass:
        score += 20
    if extension_pass:
        score += 10

    if technical_pass:
        technical_status = STATUS_BUY if score >= BUY_SCORE_MIN else STATUS_WATCH
    elif score >= DEVELOPING_SCORE_MIN:
        technical_status = STATUS_DEVELOPING
    else:
        technical_status = STATUS_SKIP

    failed_text = " | ".join(failed_conditions) if failed_conditions else "🟢 ALL CONDITIONS PASSED"

    return {
        "Stock": symbol,
        "Signal Date": signal_date.strftime("%Y-%m-%d"),
        "Close": round(close, 2),
        "20 EMA": round(ema20, 2),
        "50 EMA": round(ema50, 2),
        "200 SMA": round(sma200, 2),
        "RSI(14)": round(rsi14, 2),
        "52W High": round(high_52w, 2),
        "Distance 52W High %": round(distance_from_52w * 100, 2),
        "Previous 20D High": round(previous_20d_high, 2),
        "Volume": int(volume),
        "Avg20 Volume": int(avg_volume20),
        "Volume Ratio": round(volume_ratio, 2),
        "ATR(14)": round(atr14, 2),
        "Entry": round(entry, 2),
        "Stop": round(stop, 2),
        "Risk/Share": round(risk_per_share, 2),
        "Quantity": quantity,
        "Capital Required": round(capital_required, 2),
        "Actual Risk": round(actual_risk, 2),
        "2R Target": round(target_2r, 2),
        "Max Hold": MAX_HOLDING_SESSIONS,
        "Score": score,
        "Technical Status": technical_status,
        "Failed Conditions": failed_text,
    }


# ============================================================
# STAGE 2 — EARNINGS CHECK
# ============================================================

def check_upcoming_earnings(symbol, days=7):
    try:
        ticker = yf.Ticker(symbol + ".NS")
        earnings = ticker.get_earnings_dates(limit=12)
        if earnings is None or earnings.empty:
            return {"Earnings Status": EARNINGS_UNKNOWN, "Earnings Date": "N/A", "Days Until Earnings": "N/A"}

        today = pd.Timestamp.now().normalize()
        future_dates = []
        for date in earnings.index:
            try:
                earnings_date = pd.Timestamp(date)
                if earnings_date.tzinfo is not None:
                    earnings_date = earnings_date.tz_localize(None)
                earnings_date = earnings_date.normalize()
                if earnings_date >= today:
                    future_dates.append(earnings_date)
            except Exception:
                continue

        if not future_dates:
            return {"Earnings Status": EARNINGS_UNKNOWN, "Earnings Date": "N/A", "Days Until Earnings": "N/A"}

        next_earnings = min(future_dates)
        days_until = (next_earnings - today).days
        earnings_status = earnings_within_label(days) if days_until <= days else EARNINGS_CLEAR

        return {
            "Earnings Status": earnings_status,
            "Earnings Date": next_earnings.strftime("%Y-%m-%d"),
            "Days Until Earnings": days_until,
        }
    except Exception:
        return {"Earnings Status": EARNINGS_UNKNOWN, "Earnings Date": "N/A", "Days Until Earnings": "N/A"}


# ============================================================
# GOOGLE DRIVE (optional)
# ============================================================

def drive_ready():
    try:
        return "gcp_service_account" in st.secrets and "drive_folder_id" in st.secrets
    except Exception:
        return False


def get_drive_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = dict(st.secrets["gcp_service_account"])
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_excel_to_drive(df, filename):
    from googleapiclient.http import MediaIoBaseUpload

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    buffer.seek(0)

    service = get_drive_service()
    metadata = {"name": filename, "parents": [st.secrets["drive_folder_id"]]}
    media = MediaIoBaseUpload(buffer, mimetype=XLSX_MIME, resumable=True)
    file = service.files().create(body=metadata, media_body=media, fields="id, webViewLink").execute()
    return file.get("webViewLink")


def dataframe_to_excel_bytes(df):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buffer.getvalue()


# ============================================================
# MAIN SCANNER
# ============================================================

def run_scanner():
    status_box = st.empty()
    progress_bar = st.progress(0)

    status_box.info("Checking NIFTY 50 market regime...")
    market = calculate_nifty_regime()
    if market is None:
        status_box.error("Could not fetch NIFTY 50 data. Please try again shortly.")
        return None, None, []

    regime_label = "🟢 BULLISH" if market["bullish"] else "🔴 NOT BULLISH"
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("NIFTY Close", f"₹{market['close']:,.2f}")
    c2.metric("20 EMA", f"₹{market['ema20']:,.2f}")
    c3.metric("50 EMA", f"₹{market['ema50']:,.2f}")
    c4.metric("Regime", regime_label)

    status_box.info("Loading stock universe...")
    symbols = get_universe()
    if not symbols:
        status_box.error("No symbols to scan — check your universe selection.")
        return None, None, []

    results = []
    failed = []
    total = len(symbols)
    for i, symbol in enumerate(symbols, 1):
        status_box.info(f"Scanning {symbol}  ({i}/{total})")
        result = scan_stock(symbol)
        if result is None:
            failed.append(symbol)
        else:
            results.append(result)
        progress_bar.progress(i / total)

    status_box.empty()
    progress_bar.empty()

    if not results:
        st.error("No valid stock data returned for any symbol.")
        return None, None, failed

    all_results = pd.DataFrame(results)

    stage1_buy_candidates = (
        all_results[all_results["Technical Status"] == STATUS_BUY]
        .sort_values(by=["Score", "Volume Ratio"], ascending=[False, False])
        .head(TOP_RESULTS)
        .copy()
    )

    developing_watchlist = (
        all_results[
            (all_results["Score"] >= DEVELOPING_SCORE_MIN)
            & (all_results["Score"] < BUY_SCORE_MIN)
        ]
        .sort_values(by=["Score", "Volume Ratio"], ascending=[False, False])
        .copy()
    )

    stage2_rows = []
    if not stage1_buy_candidates.empty:
        earnings_box = st.empty()
        for _, candidate in stage1_buy_candidates.iterrows():
            symbol = candidate["Stock"]
            earnings_box.info(f"Checking earnings: {symbol}")
            earnings = check_upcoming_earnings(symbol, EARNINGS_LOOKAHEAD_DAYS)
            row = candidate.to_dict()
            row.update(earnings)

            if earnings["Earnings Status"] == earnings_within_label(EARNINGS_LOOKAHEAD_DAYS):
                final_decision = STATUS_SKIP_EARNINGS
            elif earnings["Earnings Status"] == EARNINGS_UNKNOWN:
                final_decision = STATUS_VERIFY_EARNINGS
            else:
                final_decision = STATUS_BUY

            row["FINAL DECISION"] = final_decision
            stage2_rows.append(row)
        earnings_box.empty()

    stage2_candidates = pd.DataFrame(stage2_rows)

    if stage2_candidates.empty:
        final_candidates = developing_watchlist.copy()
        final_candidates["Earnings Status"] = "N/A"
        final_candidates["Earnings Date"] = "N/A"
        final_candidates["Days Until Earnings"] = "N/A"
        final_candidates["FINAL DECISION"] = STATUS_DEVELOPING
    else:
        developing_copy = developing_watchlist.copy()
        developing_copy["Earnings Status"] = "N/A"
        developing_copy["Earnings Date"] = "N/A"
        developing_copy["Days Until Earnings"] = "N/A"
        developing_copy["FINAL DECISION"] = STATUS_DEVELOPING
        final_candidates = pd.concat([stage2_candidates, developing_copy], ignore_index=True)

    if not final_candidates.empty:
        final_candidates = (
            final_candidates
            .sort_values(by=["Score", "Volume Ratio"], ascending=[False, False])
            .reset_index(drop=True)
        )

    return all_results, final_candidates, failed


# ============================================================
# RESULTS DISPLAY
# ============================================================

def render_results(all_results, final_candidates, failed):
    buy = final_candidates[final_candidates["FINAL DECISION"] == STATUS_BUY]
    verify = final_candidates[final_candidates["FINAL DECISION"] == STATUS_VERIFY_EARNINGS]
    blocked = final_candidates[final_candidates["FINAL DECISION"] == STATUS_SKIP_EARNINGS]
    developing = final_candidates[final_candidates["FINAL DECISION"] == STATUS_DEVELOPING]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("🟢 Buy Candidates", len(buy))
    m2.metric("🟠 Verify Earnings", len(verify))
    m3.metric("🔴 Earnings Blocked", len(blocked))
    m4.metric("🟡 Developing Watch", len(developing))

    display_cols = [
        "Stock", "Signal Date", "Close", "RSI(14)", "Volume Ratio", "ATR(14)",
        "Entry", "Stop", "Risk/Share", "Quantity", "Capital Required", "Actual Risk",
        "2R Target", "Score", "Technical Status", "Failed Conditions",
        "Earnings Status", "Earnings Date", "Days Until Earnings", "FINAL DECISION",
    ]
    display_cols = [c for c in display_cols if c in final_candidates.columns]

    if not buy.empty:
        st.subheader("🟢 Buy Candidates")
        st.dataframe(buy[display_cols], use_container_width=True, hide_index=True)

    if not verify.empty:
        st.subheader("🟠 Verify Earnings Manually")
        st.dataframe(verify[display_cols], use_container_width=True, hide_index=True)

    if not developing.empty:
        st.subheader("🟡 Developing Watchlist")
        st.dataframe(developing[display_cols], use_container_width=True, hide_index=True)

    if not blocked.empty:
        with st.expander("🔴 Skipped — earnings within window"):
            st.dataframe(blocked[display_cols], use_container_width=True, hide_index=True)

    if final_candidates.empty:
        st.warning("No stocks scored 80+ today. Nothing to show.")

    with st.expander(f"Full universe scan ({len(all_results)} stocks, {len(failed)} unavailable)"):
        if failed:
            st.caption("No data for: " + ", ".join(failed))
        st.dataframe(all_results, use_container_width=True, hide_index=True)


# ============================================================
# SIDEBAR — PARAMETERS
# ============================================================

with st.sidebar:
    st.header("⚙️ Settings")

    universe_choice = st.radio("Stock Universe", ["NIFTY 200", "Custom List"])
    custom_text = ""
    if universe_choice == "Custom List":
        custom_text = st.text_area(
            "NSE symbols (comma or newline separated, no .NS)",
            placeholder="RELIANCE, TCS, INFY, HDFCBANK",
        )

    CAPITAL = st.number_input("Capital (₹)", min_value=1000, value=20000, step=1000)
    risk_pct = st.number_input("Risk % per trade", min_value=0.1, max_value=5.0, value=1.0, step=0.1)
    RISK_PERCENT = risk_pct / 100
    RISK_AMOUNT = CAPITAL * RISK_PERCENT
    st.caption(f"Risk per trade: ₹{RISK_AMOUNT:,.0f}")

    with st.expander("Advanced strategy settings"):
        RSI_MIN, RSI_MAX = st.slider("RSI(14) range", 0, 100, (55, 72))
        MAX_DISTANCE_52W = st.slider("Max distance from 52W high (%)", 0, 30, 7) / 100
        MIN_VOLUME_RATIO = st.number_input("Min volume ratio vs 20D avg", min_value=0.1, value=1.5, step=0.1)
        ATR_MULTIPLIER = st.number_input("ATR stop multiplier", min_value=0.1, value=1.2, step=0.1)
        MAX_ENTRY_EXTENSION = st.slider("Max entry extension above breakout (%)", 0, 15, 3) / 100
        MAX_HOLDING_SESSIONS = st.number_input("Max holding sessions", min_value=1, value=10, step=1)
        BUY_SCORE_MIN = st.number_input("BUY score threshold", min_value=0, max_value=100, value=90, step=5)
        DEVELOPING_SCORE_MIN = st.number_input("Developing watch score threshold", min_value=0, max_value=100, value=80, step=5)
        TOP_RESULTS = st.number_input("Max buy candidates shown", min_value=1, value=10, step=1)
        EARNINGS_LOOKAHEAD_DAYS = st.number_input("Earnings lookahead (days)", min_value=0, value=7, step=1)

    save_to_drive = st.checkbox("Auto-save Excel to Google Drive", value=drive_ready())
    if save_to_drive and not drive_ready():
        st.caption("⚠️ Drive isn't set up yet — see SETUP_GUIDE.md. You can still download the Excel below.")

    run_clicked = st.button("🚀 Run Screener", type="primary", use_container_width=True)


# ============================================================
# MAIN EXECUTION
# ============================================================

if run_clicked:
    start = time.time()
    try:
        with st.spinner("Running scanner — this can take several minutes for the full universe..."):
            all_results, final_candidates, failed = run_scanner()
    except Exception as e:
        st.error(f"Scanner hit an unexpected error: {e}")
        all_results, final_candidates, failed = None, None, []
    elapsed = time.time() - start

    if all_results is not None:
        st.session_state["all_results"] = all_results
        st.session_state["final_candidates"] = final_candidates
        st.session_state["failed"] = failed
        st.session_state["run_time"] = datetime.now()
        st.session_state["elapsed"] = elapsed

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        st.session_state["candidates_filename"] = f"Scanner_Candidates_{timestamp}.xlsx"
        st.session_state["all_filename"] = f"Scanner_All_{timestamp}.xlsx"

        if save_to_drive and drive_ready():
            try:
                link1 = upload_excel_to_drive(final_candidates, st.session_state["candidates_filename"])
                link2 = upload_excel_to_drive(all_results, st.session_state["all_filename"])
                st.success(
                    f"Saved to Google Drive → "
                    f"[{st.session_state['candidates_filename']}]({link1}) and "
                    f"[{st.session_state['all_filename']}]({link2})"
                )
            except Exception as e:
                st.warning(f"Could not save to Drive: {e}")

if "final_candidates" in st.session_state:
    run_time_str = st.session_state["run_time"].strftime("%Y-%m-%d %H:%M:%S")
    st.caption(f"Last run: {run_time_str} • took {st.session_state['elapsed'] / 60:.1f} min")

    render_results(
        st.session_state["all_results"],
        st.session_state["final_candidates"],
        st.session_state["failed"],
    )

    dl_col1, dl_col2 = st.columns(2)
    dl_col1.download_button(
        "⬇️ Download Candidates Excel",
        data=dataframe_to_excel_bytes(st.session_state["final_candidates"]),
        file_name=st.session_state["candidates_filename"],
        mime=XLSX_MIME,
        use_container_width=True,
    )
    dl_col2.download_button(
        "⬇️ Download Full Scan Excel",
        data=dataframe_to_excel_bytes(st.session_state["all_results"]),
        file_name=st.session_state["all_filename"],
        mime=XLSX_MIME,
        use_container_width=True,
    )
else:
    st.info("Set your parameters in the sidebar and tap **Run Screener** to begin.")
