#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Mar  8 13:58:17 2026

@author: adelegarrick
"""

#!/usr/bin/env python3
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
import plotly.graph_objects as go
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from wordcloud import WordCloud

TWFY_API_KEY = st.secrets["TWFY_API_KEY"]
BASE_URL = "https://www.theyworkforyou.com/api/"

st.set_page_config(
    page_title="Parliamentary Debate Analyser",
    page_icon="🏛️",
    layout="wide",
)

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
those would that this which parliament house debate bill clause amendment
order question answer today colleagues
""".split())


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_debates_for_term(search_term, debate_type, progress_cb):
    all_results, page = [], 1
    while True:
        params = {"key": TWFY_API_KEY, "search": search_term, "type": debate_type,
                  "order": "d", "page": page, "num": 100, "output": "json"}
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
    progress_bar = st.progress(0, text="Starting search...")
    status_text = st.empty()

    def update_progress(fetched, total, current_term):
        progress_bar.progress(min(fetched / max(total, 1), 1.0),
                               text=f'Fetching "{current_term}": {fetched} / {total} speeches')

    seen_gids, all_rows = set(), []
    for t in [base_word, base_word + "s"]:
        status_text.text(f'Searching for "{t}"...')
        for row in fetch_debates_for_term(t, debate_type, update_progress):
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
    next_m = (date(end_date.year, end_date.month, 28) + timedelta(days=4)).replace(day=1)
    cutoff_end = next_m - timedelta(days=1)

    # mp_data[name] = {mentions, party, constituency, speeches:[...]}
    # monthly_speakers[month][name] = {mentions, party, constituency, speeches:[...]}
    mp_data = defaultdict(lambda: {"mentions": 0, "party": "", "constituency": "", "speeches": []})
    monthly_speakers = defaultdict(lambda: defaultdict(
        lambda: {"mentions": 0, "party": "", "constituency": "", "speeches": []}))

    for row in all_rows:
        try:
            row_date = datetime.strptime(row.get("hdate", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (cutoff_start <= row_date <= cutoff_end):
            continue
        label = date(row_date.year, row_date.month, 1).strftime("%b %Y")
        body = row.get("body", "")
        count = body.lower().count(base_word)
        if not count:
            continue
        if label in buckets:
            buckets[label] += count
        speaker = row.get("speaker", {})
        name = speaker.get("name", "Unknown")
        if name and name != "Unknown":
            speech_entry = {
                "date": row.get("hdate", ""),
                "debate": row.get("parent", {}).get("body", ""),
                "excerpt": extract_excerpt(body, base_word),
                "url": "https://www.theyworkforyou.com" + row.get("listurl", ""),
                "mentions": count,
            }
            # Overall
            mp_data[name]["mentions"] += count
            mp_data[name]["party"] = speaker.get("party", "")
            mp_data[name]["constituency"] = speaker.get("constituency", "")
            mp_data[name]["speeches"].append(speech_entry)
            # Per month
            monthly_speakers[label][name]["mentions"] += count
            monthly_speakers[label][name]["party"] = speaker.get("party", "")
            monthly_speakers[label][name]["constituency"] = speaker.get("constituency", "")
            monthly_speakers[label][name]["speeches"].append(speech_entry)

    progress_bar.empty()
    status_text.empty()
    return buckets, mp_data, monthly_speakers


# ── Chart ─────────────────────────────────────────────────────────────────────

def make_plotly_chart(buckets, term, debate_type, selected_month=None):
    labels = list(buckets.keys())
    values = list(buckets.values())
    max_val = max(values) if max(values) > 0 else 1
    peak_month = labels[values.index(max_val)] if max_val > 0 else None

    colours = []
    for i, (label, v) in enumerate(zip(labels, values)):
        if label == selected_month:
            colours.append("#f59e0b")          # amber = user-selected
        elif label == peak_month and v > 0:
            colours.append("#38bdf8")          # bright blue = peak
        elif v > 0:
            intensity = int(60 + 160 * (v / max_val))
            colours.append(f"rgb(20,{intensity},{intensity + 40})")
        else:
            colours.append("#1e293b")

    chamber_labels = {"commons": "House of Commons", "lords": "House of Lords",
                      "westminsterhall": "Westminster Hall"}

    fig = go.Figure(go.Bar(
        x=labels,
        y=values,
        marker_color=colours,
        hovertemplate="<b>%{x}</b><br>%{y} mention(s)<extra></extra>",
        text=[str(v) if v > 0 else "" for v in values],
        textposition="outside",
        textfont=dict(color="#94a3b8", size=11),
    ))

    fig.update_layout(
        title=dict(
            text=f'Mentions of "{term}" · {chamber_labels.get(debate_type, debate_type)}<br>'
                 f'<span style="font-size:13px;color:#64748b">{labels[0]} — {labels[-1]}</span>',
            font=dict(color="#f1f5f9", size=16),
        ),
        paper_bgcolor="#0f172a",
        plot_bgcolor="#0f172a",
        font=dict(color="#94a3b8"),
        xaxis=dict(tickangle=-45, gridcolor="#1e293b", linecolor="#1e293b"),
        yaxis=dict(gridcolor="#1e293b", linecolor="#1e293b", tickformat="d"),
        margin=dict(t=80, b=60, l=40, r=20),
        height=400,
        bargap=0.3,
        clickmode="event+select",
    )

    return fig, peak_month


# ── Members list ──────────────────────────────────────────────────────────────

@st.cache_data(show_spinner="Loading members list...")
def fetch_all_members():
    members = []
    resp = requests.get(BASE_URL + "getMPs",
                        params={"key": TWFY_API_KEY, "output": "json"}, timeout=30)
    if resp.ok:
        for m in (resp.json() if isinstance(resp.json(), list) else []):
            name, pid = m.get("name", ""), m.get("person_id", "")
            if name and pid:
                members.append({"label": f"{name} (MP · {m.get('constituency','')})",
                                 "name": name, "person_id": str(pid),
                                 "party": m.get("party", ""),
                                 "constituency": m.get("constituency", "")})
    resp = requests.get(BASE_URL + "getLords",
                        params={"key": TWFY_API_KEY, "output": "json"}, timeout=30)
    if resp.ok:
        for m in (resp.json() if isinstance(resp.json(), list) else []):
            name, pid = m.get("name", ""), m.get("person_id", "")
            if name and pid:
                members.append({"label": f"{name} (Lord · {m.get('party','')})",
                                 "name": name, "person_id": str(pid),
                                 "party": m.get("party", ""), "constituency": ""})
    return sorted(members, key=lambda x: x["name"])


def fetch_speeches_for_person(person_id, debate_type, progress_cb):
    all_results, page = [], 1
    while True:
        params = {"key": TWFY_API_KEY, "person": person_id, "type": debate_type,
                  "order": "d", "page": page, "num": 100, "output": "json"}
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


def filter_speeches_by_date(rows, start_date, end_date):
    cutoff_start = date(start_date.year, start_date.month, 1)
    next_m = (date(end_date.year, end_date.month, 28) + timedelta(days=4)).replace(day=1)
    cutoff_end = next_m - timedelta(days=1)
    return [r for r in rows
            if cutoff_start <= datetime.strptime(r.get("hdate", "1900-01-01"), "%Y-%m-%d").date() <= cutoff_end
            if r.get("hdate")]


def get_top_words(rows, top_n=60):
    word_counts = Counter()
    for row in rows:
        text = clean_html(row.get("body", "")).lower()
        for w in re.findall(r"\b[a-z]{4,}\b", text):
            if w not in STOP_WORDS:
                word_counts[w] += 1
    return word_counts.most_common(top_n)


def make_wordcloud(word_freq):
    wc = WordCloud(width=700, height=400, background_color="#0f172a",
                   colormap="Blues", max_words=80,
                   prefer_horizontal=0.7, min_font_size=10
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
    colours = ["#38bdf8" if c == max_c else
               f"#{int(30+180*(c/max_c)):02x}{int(100+80*(c/max_c)):02x}{int(180+60*(c/max_c)):02x}"
               for c in counts]
    ax.barh(words[::-1], counts[::-1], color=colours[::-1],
            edgecolor="#0f172a", height=0.65)
    ax.set_xlabel("Frequency", fontsize=10, color="#64748b")
    ax.set_title(f"Top topics — {mp_name}", fontsize=11,
                 fontweight="bold", color="#f1f5f9", pad=10)
    ax.spines[["top", "right", "bottom", "left"]].set_visible(False)
    ax.tick_params(colors="#94a3b8", labelsize=9)
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.grid(axis="x", linestyle="--", alpha=0.15, color="#94a3b8")
    plt.tight_layout()
    return fig





# ── Speakers panel (shared between peak auto-highlight and click) ──────────────

def render_speakers_for_month(month_label, monthly_speakers, buckets, term):
    """Render the 'what was happening' panel for a given month."""
    month_data = monthly_speakers.get(month_label, {})
    month_count = buckets.get(month_label, 0)

    st.markdown(f"### 📅 {month_label} — {month_count} mention{'s' if month_count != 1 else ''}")

    if not month_data:
        st.info("No speaker data for this month.")
        return

    # Build debate-centric view:
    # debate_info[title] = {total_mentions, speakers: {name: {mentions, party, constituency, speeches}}}
    from collections import defaultdict as _dd
    debate_info = _dd(lambda: {"total_mentions": 0, "speakers": _dd(
        lambda: {"mentions": 0, "party": "", "constituency": "", "speeches": []})})

    for name, info in month_data.items():
        for speech in info["speeches"]:
            debate = speech["debate"] or "Unknown debate"
            debate_info[debate]["total_mentions"] += speech["mentions"]
            debate_info[debate]["speakers"][name]["mentions"] += speech["mentions"]
            debate_info[debate]["speakers"][name]["party"] = info["party"]
            debate_info[debate]["speakers"][name]["constituency"] = info["constituency"]
            debate_info[debate]["speakers"][name]["speeches"].append(speech)

    st.markdown(
        f"**{len(debate_info)} debate{'s' if len(debate_info) != 1 else ''} · "
        f"{len(month_data)} speaker{'s' if len(month_data) != 1 else ''}**"
    )

    # One expander per debate, sorted by total mentions descending
    for debate_title, dinfo in sorted(debate_info.items(),
                                      key=lambda x: x[1]["total_mentions"], reverse=True):
        total = dinfo["total_mentions"]
        speakers = dinfo["speakers"]
        top_speakers = ", ".join(
            name for name, _ in sorted(speakers.items(),
                                       key=lambda x: x[1]["mentions"], reverse=True)[:3]
        )
        with st.expander(
            f"**{debate_title}** — {total} mention{'s' if total != 1 else ''} · {top_speakers}"
        ):
            for name, sinfo in sorted(speakers.items(),
                                      key=lambda x: x[1]["mentions"], reverse=True):
                st.markdown(
                    f"**{name}** · {sinfo['party']}"
                    + (f" · {sinfo['constituency']}" if sinfo['constituency'] else "")
                    + f" · {sinfo['mentions']} mention{'s' if sinfo['mentions'] != 1 else ''}"
                )
                for speech in sorted(sinfo["speeches"], key=lambda x: x["date"], reverse=True):
                    st.markdown(f"> {speech['excerpt']}")
                    st.markdown(f"[View full debate]({speech['url']})")
                st.markdown("---")


# ── App ───────────────────────────────────────────────────────────────────────

st.title("🏛️ Parliamentary Debate Analyser")
st.caption("Track mentions of topics and explore what individual MPs speak about")
st.divider()

tab1, tab2 = st.tabs(["Term Search", "MP / Member Profile"])

# ════ TAB 1 ══════════════════════════════════════════════════════════════════
with tab1:
    with st.form("search_form"):
        col1, col2, col3, col4 = st.columns([3, 1.5, 1.5, 1.5])
        with col1:
            search_term = st.text_input("Search Term",
                                        placeholder="e.g. stablecoin, cyber, sustainability...")
        with col2:
            debate_type = st.selectbox("Chamber",
                options=["commons", "lords", "westminsterhall"],
                format_func=lambda x: {"commons": "Commons", "lords": "Lords",
                                        "westminsterhall": "Westminster Hall"}[x])
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
                buckets, mp_data, monthly_speakers = run_term_search(
                    search_term.strip(), debate_type, start_date, end_date)

                values = list(buckets.values())
                total = sum(values)
                peak_month = max(buckets, key=buckets.get) if total > 0 else None
                peak_count = max(values) if total > 0 else 0

                # Store results in session state so click events can access them
                st.session_state["buckets"] = buckets
                st.session_state["mp_data"] = mp_data
                st.session_state["monthly_speakers"] = monthly_speakers
                st.session_state["search_term"] = search_term.strip()
                st.session_state["debate_type"] = debate_type
                st.session_state["peak_month"] = peak_month
                st.session_state["selected_month"] = peak_month  # auto-select peak

            except Exception as e:
                st.error(f"Error: {e}")

    # Render results if we have them in session state
    if "buckets" in st.session_state:
        buckets = st.session_state["buckets"]
        mp_data = st.session_state["mp_data"]
        monthly_speakers = st.session_state["monthly_speakers"]
        term = st.session_state["search_term"]
        dtype = st.session_state["debate_type"]
        peak_month = st.session_state["peak_month"]
        selected_month = st.session_state.get("selected_month", peak_month)

        values = list(buckets.values())
        total = sum(values)
        peak_count = max(values) if values else 0

        st.divider()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Mentions", total)
        c2.metric("Peak Month", peak_month or "—")
        c3.metric("Peak Count", peak_count)
        c4.metric("Speakers", len(mp_data))
        st.divider()

        if total == 0:
            st.info(f'No mentions of "{term}" found in this date range.')
        else:
            # Plotly chart
            fig, _ = make_plotly_chart(buckets, term, dtype, selected_month)
            clicked = st.plotly_chart(fig, use_container_width=True,
                                      on_select="rerun", key="bar_chart")

            # Handle click — update selected month
            if clicked and clicked.get("selection", {}).get("points"):
                clicked_label = clicked["selection"]["points"][0]["x"]
                st.session_state["selected_month"] = clicked_label
                selected_month = clicked_label

            st.caption(
                "💡 **Blue bar** = peak month (auto-highlighted). "
                "**Click any bar** to explore who was speaking that month. "
                "**Amber bar** = currently selected."
            )

            st.divider()

            # ── Month detail panel ────────────────────────────────────────────
            if selected_month:
                render_speakers_for_month(
                    selected_month, monthly_speakers, buckets, term)
                st.divider()

            # ── All speakers ranked ───────────────────────────────────────────
            st.subheader("All speakers — ranked by total mentions")
            st.caption("Across the entire selected period.")

            view = st.radio("View by:", ["Overall ranking", "By month"],
                            horizontal=True, key="speaker_view")

            if view == "Overall ranking":
                for name, info in sorted(mp_data.items(),
                                         key=lambda x: x[1]["mentions"], reverse=True):
                    with st.expander(
                        f"**{name}** · {info['party']}"
                        + (f" · {info['constituency']}" if info['constituency'] else "")
                        + f" · {info['mentions']} mention{'s' if info['mentions'] != 1 else ''}"
                    ):
                        for speech in sorted(info["speeches"],
                                             key=lambda x: x["date"], reverse=True):
                            st.markdown(f"**{speech['date']}** — {speech['debate']}")
                            st.markdown(f"> {speech['excerpt']}")
                            st.markdown(f"[View full debate]({speech['url']})")
                            st.markdown("---")

            else:  # By month
                for month_label, month_val in buckets.items():
                    if month_val == 0:
                        continue
                    month_data = monthly_speakers.get(month_label, {})
                    top_speaker = max(month_data.items(),
                                      key=lambda x: x[1]["mentions"])[0] if month_data else "—"
                    with st.expander(
                        f"**{month_label}** — {month_val} mention{'s' if month_val != 1 else ''}"
                        + (f" · Top speaker: {top_speaker}" if top_speaker != "—" else "")
                    ):
                        if not month_data:
                            st.write("No speaker data.")
                        else:
                            for name, info in sorted(month_data.items(),
                                                     key=lambda x: x[1]["mentions"],
                                                     reverse=True):
                                st.markdown(
                                    f"**{name}** · {info['party']}"
                                    + (f" · {info['constituency']}" if info['constituency'] else "")
                                    + f" · {info['mentions']} mention{'s' if info['mentions'] != 1 else ''}"
                                )
                                for speech in sorted(info["speeches"],
                                                     key=lambda x: x["date"], reverse=True):
                                    st.markdown(f"&nbsp;&nbsp;&nbsp;**{speech['date']}** — {speech['debate']}")
                                    st.markdown(f"&nbsp;&nbsp;&nbsp;> {speech['excerpt']}")
                                    st.markdown(f"&nbsp;&nbsp;&nbsp;[View full debate]({speech['url']})")
                                    st.markdown("---")


# ════ TAB 2 ══════════════════════════════════════════════════════════════════
with tab2:
    members = fetch_all_members()
    member_labels = [m["label"] for m in members]
    member_map = {m["label"]: m for m in members}

    with st.form("mp_form"):
        col1, col2, col3, col4 = st.columns([3, 1.5, 1.5, 1.5])
        with col1:
            selected_label = st.selectbox("Select MP or Lord", options=member_labels,
                                          index=0, help="Start typing to search")
        with col2:
            mp_debate_type = st.selectbox("Chamber",
                options=["commons", "lords", "westminsterhall"],
                format_func=lambda x: {"commons": "Commons", "lords": "Lords",
                                        "westminsterhall": "Westminster Hall"}[x],
                key="mp_chamber")
        with col3:
            mp_start = st.date_input("From", value=date.today() - relativedelta(years=2),
                                     key="mp_start")
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

                def mp_progress(fetched, total):
                    progress_bar.progress(min(fetched / max(total, 1), 1.0),
                                          text=f"Fetched {fetched} / {total} speeches")

                rows = fetch_speeches_for_person(selected["person_id"],
                                                 mp_debate_type, mp_progress)
                progress_bar.empty()
                filtered = filter_speeches_by_date(rows, mp_start, mp_end)
                word_freq = get_top_words(filtered)
                total_words = sum(c for _, c in word_freq)

                st.divider()
                c1, c2, c3 = st.columns(3)
                c1.metric("Speeches in range", len(filtered))
                c2.metric("Unique topics (words)", len(word_freq))
                c3.metric("Total word count", total_words)
                st.divider()

                st.subheader(f"Key topics — {selected['name']}")
                st.caption(
                    f"{selected['party']}"
                    + (f" · {selected['constituency']}" if selected['constituency'] else "")
                    + f" · {mp_start.strftime('%b %Y')} to {mp_end.strftime('%b %Y')}"
                )

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
