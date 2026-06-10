import json
import os
import re
import sqlite3
import subprocess
import sys

import pandas as pd
import plotly.express as px
import streamlit as st
from bs4 import BeautifulSoup

from app_config import DB_NAME
from db import init_database
from estimation import (
    estimate_fallback_price,
    estimate_fallback_score,
    external_cache_key,
)
from scoring import (
    MAX_PRICE_MDL,
    MDL_USD_RATE,  # Import MDL_USD_RATE from scoring
    MIN_PRICE_MDL,
    classify_laptop,
    is_unwanted_ad,
    score_laptop,
)


init_database(DB_NAME)

# Set page configurations with descriptive premium branding
st.set_page_config(page_title="NotebookBuy | Ultimate Laptop Analytics 999", layout="wide", page_icon="💻")

# Premium Aesthetic CSS Injection
st.markdown("""
<style>
    /* Styling for Streamlit elements */
    .stApp {
        background: linear-gradient(135deg, #f0f2f6 0%, #e0e2e6 100%); /* Light background */
        color: #333333; /* Darker text for contrast */
    }

    /* Premium Title and Headers */
    h1, h2, h3 {
        color: #4f46e5 !important; /* A vibrant but not too dark blue */
        font-family: 'Outfit', 'Inter', sans-serif !important;
        font-weight: 700 !important;
        text-shadow: 0 0 5px rgba(79, 70, 229, 0.1);
    }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #ffffff !important; /* White sidebar */
        border-right: 1px solid #cccccc; /* Light border */
        color: #333333;
    }

    /* Metrics panel custom premium design */
    div[data-testid="metric-container"] {
        background: rgba(255, 255, 255, 0.8); /* Light background for metrics */
        border: 1px solid rgba(79, 70, 229, 0.2); /* Blue border */
        padding: 20px;
        border-radius: 12px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); /* Lighter shadow */
        transition: transform 0.2s, border-color 0.2s, box-shadow 0.2s;
    }

    div[data-testid="metric-container"]:hover {
        transform: translateY(-2px);
        border-color: rgba(79, 70, 229, 0.6); /* More vibrant blue on hover */
        box_shadow: 0 0 10px rgba(79, 70, 229, 0.15); /* Lighter hover shadow */
    }

    /* Custom Styling for Buttons */
    div.stButton > button {
        background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%) !important; /* Keep vibrant blue */
        color: white !important;
        font-weight: bold !important;
        border: none !important;
        border-radius: 8px !important;
        padding: 10px 20px !important;
        transition: transform 0.1s, box-shadow 0.1s !important;
    }

    div.stButton > button:hover {
        transform: scale(1.02);
        box_shadow: 0 0 15px rgba(79, 70, 229, 0.4); /* Lighter hover shadow */
    }

    /* Adjust text input and select box for light theme */
    .stTextInput > div > div > input, .stSelectbox > div > div > div > div {
        background-color: #ffffff;
        color: #333333;
        border: 1px solid #cccccc;
    }
    .stTextInput > label, .stSelectbox > label, .stMultiSelect > label, .stSlider > label, .stCheckbox > label {
        color: #333333;
    }
    .stMultiSelect > div > div {
        background-color: #ffffff;
        border: 1px solid #cccccc;
    }
    .stMultiSelect > div > div > div > span {
        color: #333333;
    }
    .stMultiSelect > div > div > div > div > div {
        background-color: #e0e0e0;
        color: #333333;
    }
    .stMultiSelect > div > div > div > div > div > svg {
        color: #333333;
    }
</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load_data() -> pd.DataFrame:
    query = """
        SELECT
            a.id,
            a.title,
            a.price,
            a.url,
            a.image_url,
            a.description,
            c.cpu,
            c.gpu,
            c.ram,
            c.ssd,
            c.is_broken,
            c.year_est,
            c.cpu_score,
            c.gpu_score
        FROM ads a
        JOIN analysis_cache c ON CAST(a.id AS TEXT) = CAST(c.id AS TEXT)
        WHERE a.parsed_at >= datetime(substr((SELECT MAX(parsed_at) FROM ads), 1, 19), '-2 hours')
    """
    try:
        with sqlite3.connect(DB_NAME) as conn:
            return pd.read_sql_query(query, conn)
    except sqlite3.Error as exc:
        st.error(f"Database read failed: {exc}")
        return pd.DataFrame()


@st.cache_data(show_spinner=False)
def apply_scoring(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    def calculate(row: pd.Series) -> pd.Series:
        scored = score_laptop(
            cpu_score=row["cpu_score"] or 0,
            gpu_score=row["gpu_score"] or 0,
            ram=row["ram"] or 4,
            ssd=row["ssd"] or 0,
            year_est=row["year_est"],
            price=row["price"],
            is_broken=bool(row["is_broken"]),
        )
        category = classify_laptop(row["cpu"], row["gpu_score"] or 0, row["price"])
        return pd.Series(
            [scored["value_score"], scored["tech_pts"], category],
            index=["value_score", "tech_pts", "category"],
        )

    return pd.concat([df, df.apply(calculate, axis=1)], axis=1)


def load_external_data():
    price_cache = {}
    nbc_cache = {}
    if os.path.exists("pricehistory_cache.json"):
        with open("pricehistory_cache.json", encoding="utf-8") as f:
            price_cache = json.load(f)
    if os.path.exists("notebookcheck_cache.json"):
        with open("notebookcheck_cache.json", encoding="utf-8") as f:
            nbc_cache = json.load(f)
    return price_cache, nbc_cache


def run_command(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=900)


def run_refresh_pipeline(region: str = "balti") -> None:
    steps = [
        ("Fetching latest ads", [sys.executable, "lappars.py", "--once", "--region", region]),
        ("Analyzing specs and scores", [sys.executable, "laptop_analyzer_v3.py"]),
    ]

    progress = st.progress(0)
    log_box = st.empty()

    for idx, (label, command) in enumerate(steps, start=1):
        log_box.info(label)
        result = run_command(command)
        if result.returncode != 0:
            st.error(f"{label} failed.")
            st.code((result.stderr or result.stdout or "No output")[-4000:])
            return
        progress.progress(idx / len(steps))

    st.cache_data.clear()
    st.success("Refresh complete.")
    st.rerun()


def extract_brand(title: str) -> str:
    title_lower = title.lower()
    brands = ["lenovo", "asus", "hp", "apple", "macbook", "dell", "acer", "msi", "gigabyte", "samsung", "huawei", "xiaomi", "microsoft", "razer", "toshiba", "sony"]
    for b in brands:
        if b in title_lower:
            return "Apple" if b in ("apple", "macbook") else b.upper()
    return "OTHER"


def extract_cpu_brand(cpu: str) -> str:
    cpu_lower = str(cpu).lower()
    if "ryzen" in cpu_lower or "amd" in cpu_lower or "athlon" in cpu_lower:
        return "AMD"
    elif "m1" in cpu_lower or "m2" in cpu_lower or "m3" in cpu_lower or "m4" in cpu_lower or "apple" in cpu_lower:
        return "Apple"
    elif any(k in cpu_lower for k in ("i3", "i5", "i7", "i9", "intel", "celeron", "pentium", "xeon")):
        return "Intel"
    return "Other"


# UI Header
st.title("💻 NotebookBuy — 999.md Premium Analytics")

df_raw = load_data()
if df_raw.empty:
    has_db = os.path.exists(DB_NAME)
    if has_db:
        st.info("Database exists but no analyzed laptops yet. Run **Run Refresh** to fetch ads and analyze.")
    else:
        st.info("No database yet. Run **Run Refresh** to initialize database and fetch ads.")

    region_choice = st.selectbox("Scrape Region", ["Balti", "All of Moldova"], index=0, key="init_region")
    region_arg = "balti" if region_choice == "Balti" else "all"

    if st.button("Run Refresh"):
        with st.spinner("Refreshing data..."):
            run_refresh_pipeline(region_arg)
    st.stop()

# Data Transformations & Filter Extras
# Clean up unwanted ads (scam / buying / broken)
parts_keywords = ["defect", "piese", "запчасти"]
def is_clean(row):
    if any(kw in str(row["title"]).lower() for kw in parts_keywords):
        return False
    return not is_unwanted_ad(row["title"], row.get("description", ""))

df_raw = df_raw[df_raw.apply(is_clean, axis=1)]

df = apply_scoring(df_raw)
df = df.drop_duplicates(subset=['url'], keep='last')
price_cache, nbc_cache = load_external_data()

df["brand"] = df["title"].apply(extract_brand)

# Smart Brand Normalization
def fix_apple_brand(row):
    title_lower = str(row['title']).lower()
    if any(w in title_lower for w in ['mackbook', 'macbook', 'apple', 'mac']):
        row['brand'] = 'Apple'
        row['category'] = 'MacBook'
    return row

df = df.apply(fix_apple_brand, axis=1)
df["cpu_brand"] = df["cpu"].apply(extract_cpu_brand)

# Sidebar Filters
with st.sidebar:
    st.header("🎯 Filters")

    # Price Slider
    max_seen_price = int(max(df["price"].max(), MAX_PRICE_MDL))
    default_max = min(MAX_PRICE_MDL, max_seen_price)
    price_range = st.slider(
        "Price Range (MDL)",
        0,
        max_seen_price,
        (MIN_PRICE_MDL, default_max),
    )

    # Min Value Score
    max_score = float(df["value_score"].max() or 0)
    min_score = st.slider("Min Value Score", 0.0, max(max_score, 20.0), 20.0)

    # Brand Filters
    all_brands = sorted(df["brand"].unique())
    brands = st.multiselect("Brands", all_brands, default=all_brands)

    # CPU Brand
    all_cpu_brands = sorted(df["cpu_brand"].unique())
    cpu_brands = st.multiselect("CPU Brands", all_cpu_brands, default=all_cpu_brands)

    # Categories
    all_categories = sorted(df["category"].dropna().unique())
    categories = st.multiselect("Categories", all_categories, default=all_categories)

    # Min RAM
    min_ram = st.selectbox("Minimum RAM (GB)", [0, 4, 8, 12, 16, 24, 32, 64], index=0)

    # Discrete GPU
    only_discrete = st.checkbox("Only Discrete GPU", value=False)

    # Broken Filter
    include_broken = st.checkbox("Include Broken/Spare Parts", value=False)

    st.divider()
    region_choice_sidebar = st.selectbox("Scrape Region", ["Balti", "All of Moldova"], index=0, key="sidebar_region")
    region_arg_sidebar = "balti" if region_choice_sidebar == "Balti" else "all"

    if st.button("Run Pipeline Refresh"):
        with st.spinner("Fetching and analyzing ads..."):
            run_refresh_pipeline(region_arg_sidebar)

# Filtering logic
mask = (
    df["price"].between(*price_range)
    & (df["value_score"] >= min_score)
    & (df["brand"].isin(brands))
    & (df["cpu_brand"].isin(cpu_brands))
    & (df["category"].isin(categories))
    & (df["ram"] >= min_ram)
)

if only_discrete:
    mask = mask & (df["gpu"] != "integrated") & (df["gpu_score"] > 0)
if not include_broken:
    mask = mask & (df["is_broken"] == 0)

filtered_df = df[mask].sort_values("value_score", ascending=False)

# Metric panel
c1, c2, c3 = st.columns(3)
c1.metric("Total Laptops in DB", len(df))
c2.metric("Filtered Laptops", len(filtered_df))
c3.metric("Highest Value Score", f"{filtered_df['value_score'].max():.1f}" if not filtered_df.empty else "0.0")

if filtered_df.empty:
    st.warning("No laptop deals match the selected criteria.")
    st.stop()

# Scatter Plot Section
st.subheader("📊 Price vs Raw Performance Analytics")
fig = px.scatter(
    filtered_df,
    x="price",
    y="tech_pts",
    color="value_score",
    size="ram",
    hover_name="title",
    hover_data=["cpu", "gpu", "price", "year_est"],
    color_continuous_scale="Turbo",
    labels={"tech_pts": "Raw Performance Points", "price": "Price (MDL)"},
    height=550,
    opacity=0.85
)

# Reference Lines
median_price = filtered_df["price"].median()
median_pts = filtered_df["tech_pts"].median()
fig.add_hline(y=median_pts, line_dash="dash", line_color="#3b82f6", annotation_text="Median Perf", annotation_position="top left")
fig.add_vline(x=median_price, line_dash="dash", line_color="#ef4444", annotation_text="Median Price", annotation_position="top right")

fig.update_layout(
    paper_bgcolor="rgba(0,0,0,0)", # Transparent background
    plot_bgcolor="rgba(0,0,0,0)", # Transparent background
    font_color="#333333", # Darker font for light theme
    xaxis=dict(showgrid=True, gridcolor="#e0e0e0"), # Lighter grid lines
    yaxis=dict(showgrid=True, gridcolor="#e0e0e0") # Lighter grid lines
)
st.plotly_chart(fig, use_container_width=True)

# Detail Inspector Section
st.subheader("🔎 Detail & Price History Inspector")
selected_id = st.selectbox(
    "Choose a laptop to inspect detailed specs, description, and price history:",
    options=filtered_df["id"].tolist(),
    format_func=lambda x: f"[{filtered_df[filtered_df['id']==x]['category'].values[0]}] {filtered_df[filtered_df['id']==x]['title'].values[0]} - {int(filtered_df[filtered_df['id']==x]['price'].values[0])} MDL"
)

if selected_id:
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        ad_row = conn.execute("SELECT title, price, description, url, image_url FROM ads WHERE id = ?", (selected_id,)).fetchone()
        history_rows = conn.execute("SELECT price, recorded_at FROM price_history WHERE ad_id = ? ORDER BY recorded_at ASC", (selected_id,)).fetchall()
        analysis_row = conn.execute("SELECT * FROM analysis_cache WHERE id = ?", (selected_id,)).fetchone()

    if ad_row and analysis_row:
        c_detail1, c_detail2 = st.columns([3, 2])

        with c_detail1:
            st.markdown(f"### {ad_row['title']}")
            st.markdown(f"[🔗 View Original Ad on 999.md]({ad_row['url']})")

            st.markdown("#### ⚙️ Technical Specifications")
            col_spec1, col_spec2, col_spec3 = st.columns(3)
            col_spec1.markdown(f"**CPU:** `{analysis_row['cpu']}` (Score: {analysis_row['cpu_score']})")
            col_spec2.markdown(f"**GPU:** `{analysis_row['gpu']}` (Score: {analysis_row['gpu_score']})")
            col_spec3.markdown(f"**RAM:** `{analysis_row['ram']} GB` | **SSD:** `{analysis_row['ssd']} GB`")

            col_spec4, col_spec5, col_spec6 = st.columns(3)
            col_spec4.markdown(f"**Estimated Year:** `{analysis_row['year_est'] or 'Unknown'}`")
            col_spec5.markdown(f"**Category:** `{filtered_df[filtered_df['id']==selected_id]['category'].values[0]}`")
            col_spec6.markdown(f"**Broken/Spare parts:** `{'Yes' if analysis_row['is_broken'] else 'No'}`")

            st.markdown("#### 📝 Description")
            desc_text = BeautifulSoup(ad_row['description'] or "", "html.parser").get_text("\n")
            with st.expander("Show full description", expanded=True):
                st.write(desc_text[:1200] + ("..." if len(desc_text) > 1200 else ""))

        with c_detail2:
            if ad_row["image_url"]:
                st.image(ad_row["image_url"], caption="Listing Image", use_container_width=True)

            st.markdown("#### 📈 Price History")
            if history_rows:
                history_df = pd.DataFrame([dict(r) for r in history_rows])
                history_df["recorded_at"] = pd.to_datetime(history_df["recorded_at"])

                fig_hist = px.line(
                    history_df,
                    x="recorded_at",
                    y="price",
                    markers=True,
                    labels={"price": "Price (MDL)", "recorded_at": "Date"},
                    title="Price Trends for this Ad"
                )
                fig_hist.update_traces(line_color="#6366f1", marker=dict(size=8, color="#4f46e5"))
                fig_hist.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", # Transparent background
                    plot_bgcolor="rgba(0,0,0,0)", # Transparent background
                    font_color="#333333", # Darker font for light theme
                    xaxis=dict(showgrid=False),
                    yaxis=dict(showgrid=True, gridcolor="#e0e0e0") # Lighter grid lines
                )
                st.plotly_chart(fig_hist, use_container_width=True)

                first_p = history_df["price"].iloc[0]
                last_p = history_df["price"].iloc[-1]
                if last_p < first_p:
                    drop = first_p - last_p
                    pct = (drop / first_p) * 100
                    st.success(f"🔥 Price dropped by **{int(drop)} MDL** (-{pct:.1f}%) since first seen!")
                elif last_p > first_p:
                    st.warning(f"📈 Price increased by **{int(last_p - first_p)} MDL**!")
                else:
                    st.info("Price has remained stable since first seen.")
            else:
                st.info("No price history records yet. This ad is fresh!")

# Deals Table Section
st.subheader("📋 Deals Table")

def get_external_info(row):
    key = external_cache_key(row['cpu'], row['gpu'], row['ram'])
    wp = price_cache.get(key, {})
    nbc = nbc_cache.get(key, {})

    vs_pct = "—"
    if wp.get('current_usd'):
        world_mdl = wp['current_usd'] * MDL_USD_RATE # Use MDL_USD_RATE
        diff = (row['price'] - world_mdl) / world_mdl * 100
        vs_pct = f"{diff:+.0f}%"

    score = f"{nbc.get('score')}%" if nbc.get('score') else "—"
    return pd.Series([vs_pct, score], index=["vs World", "NBC Score"])

# Fallback estimation lives in estimation.py (shared with the analyzer and
# the Telegram notifier) so the three surfaces cannot drift apart.

def safe_to_float(val, default=0.0):
    try:
        if pd.isna(val) or val == '' or val == '—':
            return default
        # Remove any non-numeric characters just in case
        val_str = str(val).replace('%', '').replace('+', '').strip()
        return float(val_str)
    except (ValueError, TypeError):
        return default

def is_missing(val):
    return pd.isna(val) or val == '—' or val == ''

ext_df = filtered_df.apply(get_external_info, axis=1)
display_df = pd.concat([filtered_df, ext_df], axis=1)

# Apply fallback logic
for idx, row in display_df.iterrows():
    if is_missing(row.get('NBC Score')):
        fallback_score = estimate_fallback_score(row['cpu'], row['gpu'], safe_to_float(row['ram']))
        display_df.at[idx, 'NBC Score'] = f"{fallback_score}%"

    if is_missing(row.get('vs World')):
        site_price = safe_to_float(row.get('price'), default=0)
        if site_price > 0:
            calc_price_mdl = estimate_fallback_price(
                row['cpu'], row['gpu'],
                safe_to_float(row['ram']), safe_to_float(row['ssd']),
                brand=row.get('brand', ''),
            )
            if calc_price_mdl > 0:
                vs_world_percent = ((site_price - calc_price_mdl) / calc_price_mdl) * 100
                sign = "+" if vs_world_percent > 0 else ""
                display_df.at[idx, 'vs World'] = f"{sign}{int(round(vs_world_percent))}%"

def calculate_risk(row):
    risk = ""
    vs_str = str(row.get('vs World', ''))
    m = re.search(r'([-+]?\d+)', vs_str)
    vs_pct = float(m.group(1)) if m else 0.0

    brand = str(row.get('brand', ''))
    price = safe_to_float(row.get('price', 0))
    year = safe_to_float(row.get('year_est', 0))

    if brand == 'Apple' and vs_pct < -55:
        risk = "⚠️ Высокий (Скам/Блок)"
    elif brand != 'Apple' and vs_pct < -65:
        risk = "⚠️ Подозрительно дешево"
    elif price < 2000 and year > 2019:
        risk = "⚠️ На запчасти?"

    return risk

display_df['Risk'] = display_df.apply(calculate_risk, axis=1)

cols = ["value_score", "price", "vs World", "Risk", "NBC Score", "category", "brand", "cpu", "gpu", "ram", "ssd", "year_est", "url", "title"]
display_df = display_df[cols].copy()

# Apply rounding and type conversion for price
display_df["price"] = display_df["price"].round(0).astype(int)
# Round value_score to 1 decimal place
display_df["value_score"] = display_df["value_score"].round(1)

st.dataframe(
    display_df,
    column_config={
        "url": st.column_config.LinkColumn("Ad Link"),
        "price": st.column_config.NumberColumn("Price (MDL)", format="%d"),
        "value_score": st.column_config.NumberColumn("Value Score", format="%.1f"),
        "year_est": st.column_config.NumberColumn("Year", format="%d"),
        "NBC Score": st.column_config.TextColumn("NBC %"),
    },
    hide_index=True,
    width="stretch",
)

# Exporter Section
st.divider()
st.subheader("📥 Export Filtered Deals")
c_exp1, c_exp2 = st.columns(2)
with c_exp1:
    csv_data = display_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        "Download CSV File",
        data=csv_data,
        file_name="notebookbuy_filtered_deals.csv",
        mime="text/csv"
    )
with c_exp2:
    json_data = display_df.to_json(orient='records', indent=2).encode('utf-8')
    st.download_button(
        "Download JSON File",
        data=json_data,
        file_name="notebookbuy_filtered_deals.json",
        mime="application/json"
    )
