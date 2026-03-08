#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Mar  8 13:58:17 2026

@author: adelegarrick
"""

"""
Parliamentary Debate Analyser — Streamlit Dashboard
Run with: streamlit run parliament_dashboard.py
"""

import requests
import re
import streamlit as st
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict, Counter
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from wordcloud import WordCloud

API_KEY = "BqRJoXFidAbGDih2fzFsnFJ6"
BASE_URL = "https://www.theyworkforyou.com/api/"

st.set_page_config(
    page_title="Parliamentary Debate Analyser",
    page_icon="🏛️",
    layout="wide",
)

# ── Stop words ────────────────────────────────────────────────────────────────
STOP_WORDS = set("""
a about above after again against all also am an and any are aren't as at be
because been before being below between both but by can't cannot could couldn't
did didn't do does doesn't doing don't down during each few for from further get
got had hadn't has hasn't have haven't having he he'd he'll he's her here here's
hers herself him himself his how how's i i'd i'll i'm i've if in into is isn't
it it's its itself let's me more most mustn't my myself no nor not of off on once
only or other ought our ours ourselves out over own same shan't she she'd she'll
she's should shouldn't so some such than that that's the their theirs them
themselves then there there's these they they'd they'll they're they've this
those through to too under until up very was wasn't we we'd we'll we're we've
were weren't what what's when when's where where's which while who who's whom
why why's will with won't would wouldn't you you'd you'll you're you've your
yours yourself yourselves
hon friend right honourable gentleman lady member minister secretary government
will would like make people think know said need also must could should may
want just going well good now see one two three look come years year time
many much more most very really quite think said although however therefore
indeed perhaps rather simply actually already often still even just back
across already been made take given said comes through since within without
those would that this which
""".split())


# ── Shared fetch helpers ──────────────────────────────────────────────────────

def clean_html(text):
    return re.sub(r"<[^>]+>", " ", text).strip()


def extract_excerpt(body, base_word, context_chars=300):
    clean = clean_html(body)
    idx = clean.lower().find(base_word)
    if idx == -1:
        return clean[:context_chars] + "..."
    start = max(0, idx - context_chars // 2)
    end = min(len(clean), idx + context_chars // 2)
    excerpt = clean[start:end].strip()
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(clean):
        excerpt = excerpt + "..."
    return excerpt


# ── Tab 1: Term search ────────────────────────────────────────────────────────

def fetch_debates_for_term(search_term, debate_type, progress_cb):
    all_results = []
    page = 1
    while True:
        params = {
            "key": API_KEY, "search": search_term,
            "type": debate_type, "order": "d",
            "page": page, "num": 100, "output": "json",
        }
        resp = requests.get(BASE_URL + "getDebates", params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) or "error" in data:
            break
        rows = data.get("rows", [])
        if not rows:
            break
        all_results.extend(rows)
        total = int(data.get("info", {}).get("total_results", 0))
        progress_cb(len(all_results), total, search_term)
        page += 1
        if len(all_results) >= total:
            break
    return all_results


def run_term_search(term, debate_type, start_date, end_date):
    base_word = term.strip().lower().rstrip("s")
    search_terms = [base_word, base_word + "s"]
    progress_bar = st.progress(0, text="Starting search...")
    status_text = st.empty()

    def update_progress(fetched, total, current_term):
        pct = min(fetched / max(total, 1), 1.0)
        progress_bar.progress(pct, text=f'Fetching "{current_term}": {fetched} / {total} speeches')

    seen_gids = set()
    all_rows = []
    for t in search_terms:
        status_text.text(f'Searching for "{t}"...')
        rows = fetch_debates_for_term(t, debate_type, update_progress)
        for row in rows:
            gid = row.get("gid")
            if gid and gid not in seen_gids:
                seen_gids.add(gid)
                all_rows.append(row)

    status_text.text(f"Processing {len(all_rows)} unique speeches...")

    # Month buckets
    buckets = {}
    cur = date(start_date.year, start_date.month, 1)
    end_m = date(end_date.year, end_date.month, 1)
    while cur <= end_m:
        buckets[cur.strftime("%b %Y")] = 0
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)

    cutoff_start = date(start_date.year, start_date.month, 1)
    last_m = date(end_date.year, end_date.month, 1)
    next_m = (last_m.replace(day=28) + timedelta(days=4)).replace(day=1)
    cutoff_end = next_m - timedelta(days=1)

    mp_data = defaultdict(lambda: {"mentions": 0, "party": "", "constituency": "", "speeches": []})

    for row in all_rows:
        try:
            row_date = datetime.strptime(row.get("hdate", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if row_date < cutoff_start or row_date > cutoff_end:
            continue
        label = date(row_date.year, row_date.month, 1).strftime("%b %Y")
        body = row.get("body", "")
        count = body.lower().count(base_word)
        if count == 0:
            continue
        if label in buckets:
            buckets[label] += count
        speaker = row.get("speaker", {})
        name = speaker.get("name", "Unknown")
        if name and name != "Unknown":
            mp_data[name]["mentions"] += count
            mp_data[name]["party"] = speaker.get("party", "")
            mp_data[name]["constituency"] = speaker.get("constituency", "")
            mp_data[name]["speeches"].append({
                "date": row.get("hdate", ""),
                "debate": row.get("parent", {}).get("body", ""),
                "excerpt": extract_excerpt(body, base_word),
                "url": "https://www.theyworkforyou.com" + row.get("listurl", ""),
            })

    progress_bar.empty()
    status_text.empty()
    return buckets, mp_data


def make_bar_chart(buckets, term, debate_type):
    labels = list(buckets.keys())
    values = list(buckets.values())
    max_val = max(values) if max(values) > 0 else 1
    chamber_labels = {"commons": "House of Commons", "lords": "House of Lords", "westminsterhall": "Westminster Hall"}
    fig, ax = plt.subplots(figsize=(14, 5))
    fig.patch.set_facecolor("#0f172a")
    ax.set_facecolor("#0f172a")
    colours = []
    for v in values:
        if v == max_val and v > 0:
            colours.append("#38bdf8")
        elif v > 0:
            intensity = 0.3 + 0.6 * (v / max_val)
            colours.append((0.1, intensity * 0.7, intensity))
        else:
            colours.append("#1e293b")
    bars = ax.bar(labels, values, color=colours, edgecolor="#0f172a", linewidth=1.2, zorder=3, width=0.65)
    for bar, val in zip(bars, values):
        if val > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.08,
                    str(val), ha="center", va="bottom", fontsize=9, fontweight="bold", color="#94a3b8")
    ax.set_xlabel("Month", fontsize=11, color="#64748b", labelpad=10)
    ax.set_ylabel("Mentions", fontsize=11, color="#64748b", labelpad=10)
    ax.set_title(f'Mentions of "{term}" · {chamber_labels.get(debate_type, debate_type)}\n{labels[0]} — {labels[-1]}',
                 fontsize=13, fontweight="bold", color="#f1f5f9", pad=14)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.set_ylim(0, max_val * 1.25 + 1)
    ax.grid(axis="y", linestyle="--", alpha=0.15, color="#94a3b8", zorder=0)
    ax.spines[["top", "right", "left", "bottom"]].set_visible(False)
    ax.tick_params(colors="#64748b", labelsize=9)
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    return fig


# ── Tab 2: MP profile ─────────────────────────────────────────────────────────

@st.cache_data(show_spinner="Loading members list...")
def fetch_all_members():
    """Fetch all MPs and Lords into a combined list."""
    members = []

    # MPs
    resp = requests.get(BASE_URL + "getMPs", params={"key": API_KEY, "output": "json"}, timeout=30)
    if resp.ok:
        data = resp.json()
        for m in (data if isinstance(data, list) else []):
            name = m.get("name", "")
            pid = m.get("person_id", "")
            party = m.get("party", "")
            constituency = m.get("constituency", "")
            if name and pid:
                members.append({
                    "label": f"{name} (MP · {constituency})",
                    "name": name,
                    "person_id": str(pid),
                    "house": "commons",
                    "party": party,
                    "constituency": constituency,
                })

    # Lords
    resp = requests.get(BASE_URL + "getLords", params={"key": API_KEY, "output": "json"}, timeout=30)
    if resp.ok:
        data = resp.json()
        for m in (data if isinstance(data, list) else []):
            name = m.get("name", "")
            pid = m.get("person_id", "")
            party = m.get("party", "")
            if name and pid:
                members.append({
                    "label": f"{name} (Lord · {party})",
                    "name": name,
                    "person_id": str(pid),
                    "house": "lords",
                    "party": party,
                    "constituency": "",
                })

    return sorted(members, key=lambda x: x["name"])


def fetch_speeches_for_person(person_id, debate_type, progress_cb):
    """Fetch all speeches by a person ID."""
    all_results = []
    page = 1
    while True:
        params = {
            "key": API_KEY, "person": person_id,
            "type": debate_type, "order": "d",
            "page": page, "num": 100, "output": "json",
        }
        resp = requests.get(BASE_URL + "getDebates", params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) or "error" in data:
            break
        rows = data.get("rows", [])
        if not rows:
            break
        all_results.extend(rows)
        total = int(data.get("info", {}).get("total_results", 0))
        progress_cb(len(all_results), total)
        page += 1
        if len(all_results) >= total:
            break
    return all_results


def get_top_words(rows, start_date, end_date, top_n=60):
    """Count meaningful word frequencies across all speeches in date range."""
    cutoff_start = date(start_date.year, start_date.month, 1)
    last_m = date(end_date.year, end_date.month, 1)
    next_m = (last_m.replace(day=28) + timedelta(days=4)).replace(day=1)
    cutoff_end = next_m - timedelta(days=1)

    word_counts = Counter()
    speeches_in_range = 0

    for row in rows:
        try:
            row_date = datetime.strptime(row.get("hdate", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if row_date < cutoff_start or row_date > cutoff_end:
            continue
        speeches_in_range += 1
        text = clean_html(row.get("body", "")).lower()
        words = re.findall(r"\b[a-z]{4,}\b", text)
        for w in words:
            if w not in STOP_WORDS:
                word_counts[w] += 1

    return word_counts.most_common(top_n), speeches_in_range


def make_wordcloud(word_freq):
    wc = WordCloud(
        width=700, height=400,
        background_color="#0f172a",
        colormap="Blues",
        max_words=80,
        prefer_horizontal=0.7,
        min_font_size=10,
    ).generate_from_frequencies(dict(word_freq))
    fig, ax = plt.subplots(figsize=(7, 4))
    fig.patch.set_facecolor("#0f172a")
    ax.imshow(wc, interpolation="bilinear")
    ax.axis("off")
    plt.tight_layout(pad=0)
    return fig


def make_topic_bar(word_freq, mp_name):
    words = [w for w, _ in word_freq[:20]]
    counts = [c for _, c in word_freq[:20]]
    max_c = max(counts) if counts else 1

    fig, ax = plt.subplots(figsize=(7, 4))
    fig.patch.set_facecolor("#0f172a")
    ax.set_facecolor("#0f172a")

    colours = ["#38bdf8" if c == max_c else f"#{int(30 + 180*(c/max_c)):02x}{int(100 + 80*(c/max_c)):02x}{int(180 + 60*(c/max_c)):02x}" for c in counts]
    bars = ax.barh(words[::-1], counts[::-1], color=colours[::-1], edgecolor="#0f172a", height=0.65)

    ax.set_xlabel("Frequency", fontsize=10, color="#64748b")
    ax.set_title(f"Top topics — {mp_name}", fontsize=11, fontweight="bold", color="#f1f5f9", pad=10)
    ax.spines[["top", "right", "bottom", "left"]].set_visible(False)
    ax.tick_params(colors="#94a3b8", labelsize=9)
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.grid(axis="x", linestyle="--", alpha=0.15, color="#94a3b8")
    plt.tight_layout()
    return fig


# ── App layout ────────────────────────────────────────────────────────────────

st.title("🏛️ Parliamentary Debate Analyser")
st.caption("Track mentions of topics and explore what individual MPs speak about")
st.divider()

tab1, tab2 = st.tabs(["Term Search", "MP / Member Profile"])

# ════════════════════════════════════════════════════════════════════
# TAB 1 — Term search
# ════════════════════════════════════════════════════════════════════
with tab1:
    with st.form("search_form"):
        col1, col2, col3, col4 = st.columns([3, 1.5, 1.5, 1.5])
        with col1:
            search_term = st.text_input("Search Term", value="stablecoin", placeholder="e.g. stablecoin, NHS, Brexit...")
        with col2:
            debate_type = st.selectbox("Chamber", options=["commons", "lords", "westminsterhall"],
                format_func=lambda x: {"commons": "Commons", "lords": "Lords", "westminsterhall": "Westminster Hall"}[x])
        with col3:
            start_date = st.date_input("From", value=date.today() - relativedelta(months=12))
        with col4:
            end_date = st.date_input("To", value=date.today())
        submitted = st.form_submit_button("Search", use_container_width=True)

    if submitted:
        if not search_term.strip():
            st.warning("Please enter a search term.")
        elif start_date > end_date:
            st.warning("Start date must be before end date.")
        else:
            try:
                buckets, mp_data = run_term_search(search_term.strip(), debate_type, start_date, end_date)
                values = list(buckets.values())
                total = sum(values)
                peak_month = max(buckets, key=buckets.get) if total > 0 else "—"
                peak_count = max(values) if total > 0 else 0

                st.divider()
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Total Mentions", total)
                c2.metric("Peak Month", peak_month)
                c3.metric("Peak Count", peak_count)
                c4.metric("Speakers", len(mp_data))
                st.divider()

                if total == 0:
                    st.info(f'No mentions of "{search_term}" found in this date range.')
                else:
                    fig = make_bar_chart(buckets, search_term.strip(), debate_type)
                    st.pyplot(fig)
                    plt.close(fig)
                    st.divider()

                    st.subheader("Speakers")
                    st.caption(f"All speakers who mentioned '{search_term}', ranked by mentions.")
                    sorted_mps = sorted(mp_data.items(), key=lambda x: x[1]["mentions"], reverse=True)
                    for mp_name, info in sorted_mps:
                        with st.expander(
                            f"**{mp_name}** · {info['party']}"
                            + (f" · {info['constituency']}" if info['constituency'] else "")
                            + f" · {info['mentions']} mention{'s' if info['mentions'] != 1 else ''}"
                        ):
                            for speech in sorted(info["speeches"], key=lambda x: x["date"], reverse=True):
                                st.markdown(f"**{speech['date']}** — {speech['debate']}")
                                st.markdown(f"> {speech['excerpt']}")
                                st.markdown(f"[View full debate]({speech['url']})")
                                st.markdown("---")
            except Exception as e:
                st.error(f"Error: {e}")


# ════════════════════════════════════════════════════════════════════
# TAB 2 — MP / Member profile
# ════════════════════════════════════════════════════════════════════
with tab2:
    members = fetch_all_members()
    member_labels = [m["label"] for m in members]
    member_map = {m["label"]: m for m in members}

    with st.form("mp_form"):
        col1, col2, col3, col4 = st.columns([3, 1.5, 1.5, 1.5])
        with col1:
            selected_label = st.selectbox(
                "Select MP or Lord",
                options=member_labels,
                index=0,
                help="Start typing to search"
            )
        with col2:
            mp_debate_type = st.selectbox("Chamber", options=["commons", "lords", "westminsterhall"],
                format_func=lambda x: {"commons": "Commons", "lords": "Lords", "westminsterhall": "Westminster Hall"}[x],
                key="mp_chamber")
        with col3:
            mp_start = st.date_input("From", value=date.today() - relativedelta(years=2), key="mp_start")
        with col4:
            mp_end = st.date_input("To", value=date.today(), key="mp_end")
        mp_submitted = st.form_submit_button("Analyse", use_container_width=True)

    if mp_submitted:
        selected = member_map.get(selected_label)
        if not selected:
            st.warning("Please select a member.")
        elif mp_start > mp_end:
            st.warning("Start date must be before end date.")
        else:
            try:
                progress_bar = st.progress(0, text="Fetching speeches...")
                status_text = st.empty()

                def mp_progress(fetched, total):
                    pct = min(fetched / max(total, 1), 1.0)
                    progress_bar.progress(pct, text=f"Fetched {fetched} / {total} speeches")

                rows = fetch_speeches_for_person(selected["person_id"], mp_debate_type, mp_progress)
                progress_bar.empty()
                status_text.empty()

                word_freq, speeches_in_range = get_top_words(rows, mp_start, mp_end)
                total_words = sum(c for _, c in word_freq)

                st.divider()
                c1, c2, c3 = st.columns(3)
                c1.metric("Speeches in range", speeches_in_range)
                c2.metric("Unique topics (words)", len(word_freq))
                c3.metric("Total word count", total_words)
                st.divider()

                st.subheader(f"Key topics — {selected['name']}")
                st.caption(f"{selected['party']}"
                           + (f" · {selected['constituency']}" if selected['constituency'] else "")
                           + f" · {mp_start.strftime('%b %Y')} to {mp_end.strftime('%b %Y')}")

                if not word_freq:
                    st.info("No speeches found for this member in the selected date range.")
                else:
                    col_wc, col_bar = st.columns(2)
                    with col_wc:
                        st.markdown("**Word Cloud**")
                        fig_wc = make_wordcloud(word_freq)
                        st.pyplot(fig_wc)
                        plt.close(fig_wc)
                    with col_bar:
                        st.markdown("**Top 20 Topics**")
                        fig_bar = make_topic_bar(word_freq, selected["name"])
                        st.pyplot(fig_bar)
                        plt.close(fig_bar)

            except Exception as e:
                st.error(f"Error: {e}")