#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Mar 13 07:57:48 2026

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
ANTHROPIC_API_KEY = st.secrets["ANTHROPIC_API_KEY"]
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

def fetch_debates_for_term(search_term, debate_type, progress_cb,
                           cutoff_start=None, max_results=500):
    """Fetch debates for a term, stopping early at the date cutoff or result cap."""
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
        total = int(data.get("info", {}).get("total_results", 0))
        # Early exit once results go past start date
        if cutoff_start:
            in_range, past_range = [], False
            for row in rows:
                try:
                    row_date = datetime.strptime(row.get("hdate", ""), "%Y-%m-%d").date()
                    if row_date >= cutoff_start:
                        in_range.append(row)
                    else:
                        past_range = True
                except ValueError:
                    in_range.append(row)
            all_results.extend(in_range)
            progress_cb(len(all_results), total, search_term)
            if past_range:
                break
        else:
            all_results.extend(rows)
            progress_cb(len(all_results), total, search_term)
        # Hard cap to avoid runaway fetching
        if len(all_results) >= max_results or len(all_results) >= total:
            break
        page += 1
    return all_results



def run_term_search(term, debate_type, start_date, end_date):
    base_word = term.strip().lower().rstrip("s")
    plural = base_word + "s"
    # Only search both forms if the plural differs meaningfully from the original term
    original = term.strip().lower()
    search_terms = [original]
    if original == base_word and plural != original:
        # Term was already a base form — also search plural
        search_terms = [base_word, plural]
    elif original == plural:
        # Term was entered as plural — also search singular
        search_terms = [base_word, plural]
    # Deduplicate (e.g. "data" base and plural are identical)
    search_terms = list(dict.fromkeys(search_terms))

    cutoff_start = date(start_date.year, start_date.month, 1)
    progress_bar = st.progress(0, text="Starting search...")
    status_text = st.empty()

    def update_progress(fetched, total, current_term):
        progress_bar.progress(min(fetched / max(total, 1), 1.0),
                               text=f'Fetching "{current_term}": {fetched} / {total} speeches')

    seen_gids, all_rows = set(), []
    for t in search_terms:
        status_text.text(f'Searching for "{t}"...')
        for row in fetch_debates_for_term(t, debate_type, update_progress,
                                          cutoff_start=cutoff_start):
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


def fetch_speeches_for_person(person_id, debate_type, progress_cb, cutoff_start=None):
    """Fetch speeches, stopping early once results go past cutoff_start date."""
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
        # Early exit — API returns newest first, so once we see dates before
        # our start date we can stop fetching further pages
        if cutoff_start:
            in_range = []
            past_range = False
            for row in rows:
                try:
                    row_date = datetime.strptime(row.get("hdate", ""), "%Y-%m-%d").date()
                    if row_date >= cutoff_start:
                        in_range.append(row)
                    else:
                        past_range = True
                except ValueError:
                    in_range.append(row)
            all_results.extend(in_range)
            progress_cb(len(all_results), int(data.get("info", {}).get("total_results", 0)))
            if past_range:
                break
        else:
            all_results.extend(rows)
            progress_cb(len(all_results), int(data.get("info", {}).get("total_results", 0)))
        total = int(data.get("info", {}).get("total_results", 0))
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


DEBATE_TYPE_LABELS = {
    "debates": "Commons Chamber",
    "westminsterhall": "Westminster Hall",
    "lords": "House of Lords",
    "lordswrans": "Lords Written Answers",
    "wrans": "Written Answers",
    "wms": "Written Ministerial Statements",
    "questions": "Oral Questions",
}

def get_debate_type_label(htype):
    return DEBATE_TYPE_LABELS.get(htype, htype or "Parliament")

def identify_led_debates(rows):
    """
    Return a set of parent debate titles where this MP was the lead/opening speaker.
    Lead speaker = first speech in that debate section (lowest subsection_id or first encountered).
    """
    debate_first_seen = {}  # parent_title -> first subsection_id seen
    for row in sorted(rows, key=lambda x: (x.get("hdate",""), x.get("subsection_id", 999))):
        parent = row.get("parent", {}).get("body", "")
        if not parent:
            continue
        sub_id = row.get("subsection_id", 999)
        if parent not in debate_first_seen:
            debate_first_seen[parent] = sub_id
    # A debate is "led" by this MP if their first sub_id matches the lowest seen
    led = set()
    for row in rows:
        parent = row.get("parent", {}).get("body", "")
        sub_id = row.get("subsection_id", 999)
        if parent and debate_first_seen.get(parent) == sub_id:
            led.add(parent)
    return led


@st.cache_data(show_spinner=False)
def fetch_person_profile(person_id):
    """Fetch profile details for an MP/Lord via TWFY getPerson."""
    try:
        resp = requests.get(BASE_URL + "getPerson",
                            params={"key": TWFY_API_KEY, "id": person_id, "output": "json"},
                            timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
    except Exception:
        pass
    return {}



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





@st.cache_data(show_spinner=False)
def analyse_term_landscape(term, debate_type, start_str, end_str, speaker_summary):
    """Ask Claude for a thematic landscape summary of who's talking about a term and why."""
    import json

    prompt = f"""You are a political analyst reviewing UK parliamentary debate data.

The term "{term}" was mentioned in {debate_type} debates between {start_str} and {end_str}.

Below is a summary of the key speakers and how many times they mentioned the term, along with excerpts from their speeches.

Write a single concise paragraph (4-6 sentences) summarising the overall landscape: who is driving discussion of this topic, what angles or contexts they are raising it in, and whether there appear to be any notable dividing lines (e.g. party, government vs opposition, scrutiny vs advocacy).

Do not use bullet points. Write in plain prose suitable for a policy professional.

Speaker data:
{speaker_summary}"""

    headers = {
        "content-type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}]
    }
    resp = requests.post("https://api.anthropic.com/v1/messages",
                         headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()["content"][0]["text"].strip()



# ── Claude AI analysis ───────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def analyse_with_claude(person_id, mp_name, start_str, end_str, speech_text):
    """Call Claude to identify top policy themes. Cached by person + date range."""
    import json

    prompt = f"""You are a political analyst. Below are parliamentary speeches by {mp_name}
between {start_str} and {end_str}.

Identify the top 8 policy themes or topics this person focuses on most.
Rank them from most to least prominent.

Return ONLY a JSON array with no preamble or markdown, in this exact format:
[
  {{"rank": 1, "theme": "Theme name", "explanation": "One sentence explanation of their position or focus."}},
  ...
]

Speeches:
{speech_text}"""

    headers = {
        "content-type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": prompt}]
    }

    resp = requests.post("https://api.anthropic.com/v1/messages",
                         headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    raw = result["content"][0]["text"].strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
    return json.loads(raw)


@st.cache_data(show_spinner=False)
def extract_search_terms(question):
    """Ask Claude to extract the best search terms from a natural language question."""
    import json
    prompt = f"""You are a parliamentary research assistant. A user has asked the following question about UK parliamentary debates:

"{question}"

Identify 1-3 short search terms (single words or short phrases) that would best retrieve relevant debates from a parliamentary search engine. Focus on the most specific and distinctive terms.

Return ONLY a JSON array of strings, e.g. ["sustainability", "net zero"] with no preamble."""

    headers = {
        "content-type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": prompt}]
    }
    resp = requests.post("https://api.anthropic.com/v1/messages",
                         headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    raw = resp.json()["content"][0]["text"].strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
    return json.loads(raw)


@st.cache_data(show_spinner=False)
def answer_question(question, debates_summary):
    """Ask Claude to answer the question based on debate evidence."""
    prompt = f"""You are a parliamentary research assistant. A user has asked:

"{question}"

Below is a summary of relevant UK parliamentary debates retrieved for this question. 
Write a response of 4-6 sentences directly answering the question based on this evidence. 
Then list the most relevant specific debates. Write in plain prose suitable for a policy professional.
Do not make up or infer anything not present in the debate data.

Debate data:
{debates_summary}"""

    headers = {
        "content-type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 800,
        "messages": [{"role": "user", "content": prompt}]
    }
    resp = requests.post("https://api.anthropic.com/v1/messages",
                         headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()["content"][0]["text"].strip()



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

st.title("🏛️ Parliamentary Policy Engagement Dashboard")
st.caption("Track mentions of topics and explore what individual MPs speak about")
st.divider()

tab1, tab2, tab3 = st.tabs(["Term Search", "MP / Member Profile", "Ask a Question"])

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

            # ── Claude thematic landscape ─────────────────────────────────────
            st.markdown("#### 🤖 Thematic Landscape")
            with st.spinner("Analysing who's talking and why..."):
                try:
                    # Build a compact speaker summary to send to Claude
                    top_speakers = sorted(mp_data.items(),
                                          key=lambda x: x[1]["mentions"], reverse=True)[:15]
                    lines = []
                    for name, info in top_speakers:
                        excerpts = " | ".join(
                            s["excerpt"][:200] for s in info["speeches"][:2]
                        )
                        lines.append(
                            f"{name} ({info['party']}): {info['mentions']} mention(s). {excerpts}"
                        )
                    speaker_summary = "\n".join(lines)[:6000]
                    landscape = analyse_term_landscape(
                        term, dtype,
                        list(buckets.keys())[0],
                        list(buckets.keys())[-1],
                        speaker_summary
                    )
                    st.markdown(landscape)
                except Exception as ai_err:
                    st.warning(f"AI analysis unavailable: {ai_err}")

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

                cutoff_start = date(mp_start.year, mp_start.month, 1)
                rows = fetch_speeches_for_person(selected["person_id"],
                                                 mp_debate_type, mp_progress,
                                                 cutoff_start=cutoff_start)
                progress_bar.empty()
                filtered = filter_speeches_by_date(rows, mp_start, mp_end)
                word_freq = get_top_words(filtered)
                total_words = sum(c for _, c in word_freq)
                led_debates = identify_led_debates(filtered)

                st.divider()
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Speeches in range", len(filtered))
                c2.metric("Unique topics (words)", len(word_freq))
                c3.metric("Total word count", total_words)
                c4.metric("Debates led", len(led_debates))
                st.divider()

                st.subheader(f"Key topics — {selected['name']}")
                st.caption(
                    f"{selected['party']}"
                    + (f" · {selected['constituency']}" if selected['constituency'] else "")
                    + f" · {mp_start.strftime('%b %Y')} to {mp_end.strftime('%b %Y')}"
                )

                # ── MP profile details ────────────────────────────────────────
                profile = fetch_person_profile(selected["person_id"])
                if profile:
                    with st.expander("📋 Profile details", expanded=True):
                        pcol1, pcol2 = st.columns(2)
                        with pcol1:
                            if profile.get("email"):
                                st.markdown(f"**Email:** {profile['email']}")
                            if profile.get("constituency"):
                                st.markdown(f"**Constituency:** {profile['constituency']}")
                            if profile.get("party"):
                                st.markdown(f"**Party:** {profile['party']}")
                            if profile.get("office"):
                                for office in profile["office"]:
                                    st.markdown(f"**Role:** {office.get('position','')}"
                                                + (f" · {office.get('dept','')}" if office.get('dept') else ""))
                        with pcol2:
                            committees = [o for o in profile.get("office", [])
                                          if "committee" in o.get("dept", "").lower()
                                          or "committee" in o.get("position", "").lower()]
                            if committees:
                                st.markdown("**Committees:**")
                                for c in committees:
                                    st.markdown(f"- {c.get('position','')} · {c.get('dept','')}")
                            if profile.get("url"):
                                st.markdown(f"[TheyWorkForYou profile]({profile['url']})")
                            if profile.get("twitter_username"):
                                st.markdown(f"**Twitter:** @{profile['twitter_username']}")

                # ── Debates led ──────────────────────────────────────────
                if led_debates:
                    with st.expander(f"🎙️ Debates led ({len(led_debates)})", expanded=False):
                        st.caption("Debates where this member was the opening speaker.")
                        for row in sorted(filtered,
                                          key=lambda x: x.get("hdate", ""), reverse=True):
                            parent = row.get("parent", {}).get("body", "")
                            if parent not in led_debates:
                                continue
                            htype = row.get("htype", "")
                            label = get_debate_type_label(htype)
                            url = "https://www.theyworkforyou.com" + row.get("listurl", "")
                            st.markdown(
                                f"**{row.get('hdate','')}** · {label} · "
                                f"[{parent}]({url})"
                            )
                            led_debates.discard(parent)  # show each once

                if not word_freq:
                    st.info("No speeches found for this member in the selected date range.")
                else:
                    # ── Claude AI theme analysis ─────────────────────────────
                    st.markdown("#### 🤖 AI Policy Theme Analysis")
                    with st.spinner("Analysing speeches with Claude..."):
                        try:
                            # Trim to 8,000 chars — enough context, much faster
                            speech_text = " ".join(
                                clean_html(s.get("body", "")) for s in filtered
                            )[:8000]
                            themes = analyse_with_claude(
                                selected["person_id"],
                                selected["name"],
                                mp_start.strftime("%b %Y"),
                                mp_end.strftime("%b %Y"),
                                speech_text
                            )
                            for t in themes:
                                st.markdown(f"**{t['rank']}. {t['theme']}**  \n{t['explanation']}")
                        except Exception as ai_err:
                            st.warning(f"AI analysis unavailable: {ai_err}")

                    st.divider()

                    # ── Word cloud + frequency bar ────────────────────────────
                    st.markdown("#### 📊 Word Frequency")
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

# ════ TAB 3 ══════════════════════════════════════════════════════════════════
with tab3:
    st.markdown("### 💬 Ask a Question")
    st.caption(
        "Ask a general question about what MPs have been discussing. "
        "The dashboard will use AI search parliamentary debates and summarise the findings."
    )

    with st.form("question_form"):
        col1, col2, col3 = st.columns([4, 1.5, 1.5])
        with col1:
            user_question = st.text_input(
                "Your question",
                placeholder="e.g. What are the main issues on sustainability MPs have been discussing?"
            )
        with col2:
            q_debate_type = st.selectbox("Chamber",
                options=["commons", "lords", "westminsterhall"],
                format_func=lambda x: {"commons": "Commons", "lords": "Lords",
                                        "westminsterhall": "Westminster Hall"}[x],
                key="q_chamber")
        with col3:
            q_months = st.selectbox("Time period",
                options=[3, 6, 12, 24],
                format_func=lambda x: f"Last {x} months",
                index=1,
                key="q_months")
        q_submitted = st.form_submit_button("Ask", use_container_width=True)

    if q_submitted:
        if not user_question.strip():
            st.warning("Please enter a question.")
        else:
            try:
                q_end = date.today()
                q_start = q_end - relativedelta(months=q_months)

                # Step 1 — extract search terms
                with st.spinner("Identifying search terms..."):
                    search_terms = extract_search_terms(user_question.strip())
                    st.caption(f"🔍 Searching for: {', '.join(search_terms)}")

                # Step 2 — fetch debates for each term
                all_debates = {}  # debate_title -> {url, date, speakers, excerpts}
                cutoff_start = date(q_start.year, q_start.month, 1)

                progress_bar = st.progress(0, text="Fetching debates...")

                def q_progress(fetched, total, term):
                    progress_bar.progress(
                        min(fetched / max(total, 1), 1.0),
                        text=f'Fetching "{term}": {fetched} / {total}'
                    )

                seen_gids = set()
                for term in search_terms:
                    base = term.strip().lower().rstrip("s")
                    for t in list(dict.fromkeys([base, base + "s"])):
                        rows = fetch_debates_for_term(
                            t, q_debate_type, q_progress,
                            cutoff_start=cutoff_start, max_results=200
                        )
                        for row in rows:
                            gid = row.get("gid", "")
                            if not gid or gid in seen_gids:
                                continue
                            try:
                                row_date = datetime.strptime(
                                    row.get("hdate", ""), "%Y-%m-%d").date()
                            except ValueError:
                                continue
                            if not (cutoff_start <= row_date <= q_end):
                                continue
                            seen_gids.add(gid)
                            debate_title = row.get("parent", {}).get("body", "Unknown debate")
                            url = "https://www.theyworkforyou.com" + row.get("listurl", "")
                            speaker = row.get("speaker", {})
                            key = debate_title
                            if key not in all_debates:
                                all_debates[key] = {
                                    "url": url,
                                    "date": row.get("hdate", ""),
                                    "speakers": set(),
                                    "excerpts": []
                                }
                            name = speaker.get("name", "")
                            if name:
                                all_debates[key]["speakers"].add(name)
                            body = clean_html(row.get("body", ""))
                            if body:
                                all_debates[key]["excerpts"].append(body[:300])

                progress_bar.empty()

                if not all_debates:
                    st.info("No relevant debates found. Try rephrasing your question.")
                else:
                    # Step 3 — build summary for Claude
                    lines = []
                    for title, info in sorted(
                        all_debates.items(),
                        key=lambda x: x[1]["date"], reverse=True
                    )[:30]:
                        speakers_str = ", ".join(list(info["speakers"])[:5])
                        excerpt = info["excerpts"][0] if info["excerpts"] else ""
                        lines.append(
                            f"Debate: {title} ({info['date']})\n"
                            f"Speakers: {speakers_str}\n"
                            f"Extract: {excerpt}\n"
                            f"URL: {info['url']}"
                        )
                    debates_summary = "\n\n".join(lines)[:8000]

                    # Step 4 — get Claude's answer
                    with st.spinner("Analysing findings..."):
                        answer = answer_question(user_question.strip(), debates_summary)

                    st.divider()
                    st.markdown("#### 📋 Summary")
                    st.markdown(answer)

                    st.divider()
                    st.markdown("#### 📄 Most relevant debates")
                    for title, info in sorted(
                        all_debates.items(),
                        key=lambda x: x[1]["date"], reverse=True
                    )[:15]:
                        speakers_str = ", ".join(list(info["speakers"])[:4])
                        st.markdown(
                            f"**{info['date']}** — [{title}]({info['url']})"
                            + (f"  \n_{speakers_str}_" if speakers_str else "")
                        )

            except Exception as e:
                st.error(f"Error: {e}")
