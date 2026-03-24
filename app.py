import streamlit as st
import asyncio
import nest_asyncio
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from statistics import mean, stdev
from datetime import datetime
from playwright.async_api import async_playwright
import httpx
from googlenewsdecoder import new_decoderv1
from openai import OpenAI

# Initialization for async environments
nest_asyncio.apply()

# ── Streamlit UI Setup ────────────────────────────────────────────────────────
st.set_page_config(page_title="Sporting Director Intel", page_icon="⚽", layout="wide")
st.title("⚽ Sporting Director Intelligence Engine")
st.caption("US Sourcing Specialist: Google Trends → RSS → GPT-5 Analysis")

# Sidebar Configuration
with st.sidebar:
    st.header("🔑 Configuration")
    api_key = st.text_input("OpenAI API Key", type="password", help="Get this from platform.openai.com")
    velocity_bypass = st.number_input("Velocity Bypass (Searches/hr)", value=30000, help="Trends faster than this skip the 1-hour age gate.")
    run_button = st.button("🚀 Launch Intelligence Pipeline")
    st.divider()
    st.info("Treating minutes as proportions of hours (e.g., 30 mins = 0.5 hours).")

# ── Logic Components ──────────────────────────────────────────────────────────

def parse_volume(volume_str):
    if not volume_str: return 0
    clean = volume_str.replace('+', '').replace(',', '').upper().strip()
    try:
        if 'K' in clean: return int(float(clean.replace('K', '')) * 1000)
        if 'M' in clean: return int(float(clean.replace('M', '')) * 1000000)
        return int(clean)
    except: return 0

def parse_hours_precision(time_str):
    if not time_str: return 1.0
    t = time_str.lower()
    numbers = re.findall(r'\d+', t)
    val = int(numbers[0]) if numbers else 1
    if 'day' in t: return float(val * 24)
    if 'min' in t: return round(val / 60, 4)
    return float(val)

def is_over_one_hour_old(time_str):
    if not time_str: return False
    t = time_str.lower()
    if 'min' in t: return False
    if 'day' in t: return True
    numbers = re.findall(r'\d+', t)
    val = int(numbers[0]) if numbers else 0
    return val > 1

def get_article_links(query):
    encoded_query = urllib.parse.quote(f"{query} when:1d")
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US%3Aen"
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(rss_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            root = ET.fromstring(resp.read().decode('utf-8'))
        links = []
        for item in root.findall(".//item")[:4]:
            link = item.find("link").text
            try:
                decoded = new_decoderv1(link, interval=1)
                links.append(decoded.get("decoded_url") if decoded.get("status") else link)
            except: links.append(link)
        return links
    except: return []

def fetch_article_text(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        with httpx.Client(timeout=10, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            text = re.sub(r'<(script|style)[^>]*>.*?</(script|style)>', '', resp.text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            return re.sub(r'\s+', ' ', text).strip()[:3000]
    except: return ""

async def scrape_trends(context):
    page = await context.new_page()
    data = []
    try:
        await page.goto("https://trends.google.com/trending?&category=17&geo=US", wait_until="networkidle", timeout=60000)
        rows = await page.query_selector_all("tr")
        for row in rows:
            title_el = await row.query_selector("div.mZ3RIc")
            if not title_el: continue
            title = (await title_el.inner_text()).strip()
            vol_el = await row.query_selector("td:nth-child(3) div div")
            time_el = await row.query_selector("td:nth-child(4) div:first-child")
            vol_str = (await vol_el.inner_text()).strip() if vol_el else "0"
            time_str = (await time_el.inner_text()).strip() if time_el else "1h ago"
            vol = parse_volume(vol_str)
            hrs = max(parse_hours_precision(time_str), 0.01)
            vel = round(vol / hrs, 2)
            if is_over_one_hour_old(time_str) or vel > velocity_bypass:
                data.append({"topic": title, "velocity": vel, "volume": vol_str, "elapsed": time_str})
    finally:
        await page.close()
    return data

# ── Main Execution Pipeline ───────────────────────────────────────────────────

if run_button:
    if not api_key:
        st.error("Missing OpenAI API Key.")
    else:
        client = OpenAI(api_key=api_key)
        prog_container = st.container()
        progress_bar = prog_container.progress(0)
        status_text = prog_container.empty()
        
        async def main_flow():
            status_text.write("[→] Initializing Browser... [10%]")
            progress_bar.progress(10)
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(user_agent="Mozilla/5.0")
                
                status_text.write("[→] Scraping US Sport Trends... [30%]")
                progress_bar.progress(30)
                raw_data = await scrape_trends(context)
                await browser.close()

            if not raw_data:
                st.warning("No trends qualified at this moment.")
                return

            v_mean = mean([d['velocity'] for d in raw_data])
            v_std = stdev([d['velocity'] for d in raw_data]) if len(raw_data) > 1 else 0
            threshold = v_mean + v_std
            qualified = [d for d in raw_data if d['velocity'] > 1000 and d['velocity'] > threshold]
            
            status_text.write(f"[→] Found {len(qualified)} high-velocity breakouts. Running Analysis... [50%]")
            progress_bar.progress(50)

            for i, item in enumerate(qualified):
                step_p = int(50 + ((i + 1) / len(qualified) * 50))
                status_text.write(f"[→] {item['topic']} ({step_p}%)")
                progress_bar.progress(step_p)
                
                links = get_article_links(item['topic'])
                article_text = "\n".join([fetch_article_text(l) for l in links])
                
                # Logic for Sporting Director analysis
                prompt = f"Analyze for a Sporting Director: {item['topic']}\nContext: {article_text}"
                try:
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[{"role": "system", "content": "You are a football strategic expert."},
                                  {"role": "user", "content": prompt}]
                    )
                    item['analysis'] = response.choices[0].message.content
                except Exception as e:
                    item['analysis'] = f"Error in analysis: {e}"

            status_text.success("[✓] Intelligence Audit Complete [100%]")
            
            # Display results
            for res in qualified:
                with st.expander(f"TOPIC: {res['topic']} | Velocity: {res['velocity']:,}/hr"):
                    st.write(res['analysis'])
                    st.caption(f"Based on Volume: {res['volume']} | Elapsed: {res['elapsed']}")

        asyncio.run(main_flow())
