"""GetPrio — coach dashboard over the emergence report.

A busy-coach view styled as "dark academia": warm near-black, brass accents,
serif display type. Every emerging pick shows its key evidence without a click
(typographic badges, win rate + sample, which pros are on it, stage status);
one Briefing expander per pick opens the pick-rate curve, pro game log, and
stage detail.

Run:  streamlit run dashboard.py
Reads DATABASE_URL from env or the gitignored .database_url file.
"""
import altair as alt
import pandas as pd
import streamlit as st

import dashboard_data as dd

# ---- palette (dark academia: warm ink, brass, oxblood, sage) ----
INK = "#16120F"
BRASS = "#C9A86A"
PARCH = "#E7DCC9"
MUTED = "#9C8A74"
SAGE = "#8C9A6A"
OXBLOOD = "#B06A5C"

st.set_page_config(page_title="GetPrio — Draft Radar", page_icon="📜",
                   layout="wide")

THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=EB+Garamond:ital,wght@0,400;0,500;0,600;1,400&display=swap');

/* hide Streamlit chrome for a clean custom canvas */
#MainMenu, [data-testid="stToolbar"], [data-testid="stStatusWidget"],
[data-testid="stDecoration"], header[data-testid="stHeader"], footer,
[data-testid="stHeaderActionElements"] { display: none !important; }

/* warm, candlelit-library background with a soft vignette */
[data-testid="stAppViewContainer"] {
  background:
    radial-gradient(1100px 520px at 50% -8%, #271E16 0%, #18130F 55%, #120D0A 100%);
}
.block-container { max-width: 1180px; padding-top: 2.2rem; padding-bottom: 4rem; }

html, body, [data-testid="stAppViewContainer"] * {
  font-family: 'EB Garamond', Georgia, serif;
  color: #E7DCC9;
}
h1, h2, h3, .gp-name, .gp-scoreval {
  font-family: 'Cormorant Garamond', Georgia, serif;
  letter-spacing: .3px; color: #F2E9D6; font-weight: 600;
}

/* ---- masthead ---- */
.gp-mast { margin: .2rem 0 .2rem; }
.gp-title {
  font-family: 'Cormorant Garamond', serif; font-weight: 700;
  font-size: 3.1rem; line-height: 1; color: #F4ECD9; letter-spacing: .5px;
}
.gp-title .dot { color: #C9A86A; }
.gp-sub {
  color: #A7977F; font-size: 1.06rem; font-style: italic; margin-top: .35rem;
}
.gp-rule {
  height: 1px; margin: 1.1rem 0 1.4rem;
  background: linear-gradient(90deg, #C9A86A55, #C9A86A18 40%, transparent 80%);
}

/* ---- KPI strip ---- */
.gp-kpis { display: grid; grid-template-columns: repeat(4, 1fr);
  border: 1px solid #382E24; border-radius: 12px; overflow: hidden;
  background: linear-gradient(180deg, rgba(42,33,26,.55), rgba(28,22,18,.4)); }
.gp-kpi { padding: 14px 20px; border-right: 1px solid #2C241C; }
.gp-kpi:last-child { border-right: 0; }
.gp-kpi .k { font-size: .72rem; text-transform: uppercase; letter-spacing: .18em;
  color: #998772; }
.gp-kpi .v { font-family: 'Cormorant Garamond', serif; font-size: 2.1rem;
  font-weight: 600; color: #F2E9D6; line-height: 1.1; }
.gp-kpi .v.accent { color: #C9A86A; }

/* ---- cards (the bordered container) ---- */
[data-testid="stVerticalBlockBorderWrapper"] {
  background: linear-gradient(180deg, rgba(43,34,27,.58), rgba(28,22,18,.5));
  border: 1px solid #362C22 !important; border-radius: 14px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.025), 0 12px 30px rgba(0,0,0,.28);
  transition: border-color .3s ease, box-shadow .3s ease, transform .3s ease;
  margin-bottom: 2px;
}
[data-testid="stVerticalBlockBorderWrapper"]:hover {
  border-color: rgba(201,168,106,.45) !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.04), 0 14px 34px rgba(0,0,0,.34);
}

/* ---- card summary row ---- */
.gp-row { display: grid; align-items: center; gap: 16px;
  grid-template-columns: 42px 1.65fr 1.4fr 1fr 1.45fr 74px; }
.gp-rank { font-family: 'Cormorant Garamond', serif; font-size: 1.5rem;
  color: #6E5E49; text-align: center; }
.gp-name { font-size: 1.62rem; line-height: 1; }
.gp-role { color: #A7977F; font-size: .82rem; text-transform: uppercase;
  letter-spacing: .16em; margin-left: .5rem; }
.gp-badges { margin-top: .5rem; display: flex; gap: 7px; }
.gp-tag { font-size: .66rem; text-transform: uppercase; letter-spacing: .16em;
  padding: 3px 9px; border-radius: 999px; border: 1px solid; }
.gp-new { color: #E7C988; border-color: #C9A86A66; background: #C9A86A14; }
.gp-rise { color: #A9B486; border-color: #8C9A6A55; background: #8C9A6A12; }

.gp-cell .k { font-size: .66rem; text-transform: uppercase; letter-spacing: .16em;
  color: #8E7D68; margin-bottom: 3px; }
.gp-cell .v { font-size: 1.02rem; color: #E7DCC9; }
.gp-cell .v .sub { color: #94836E; font-size: .85rem; }
.gp-names { color: #DCCfb6; }
.gp-empty { color: #6E5E49; font-style: italic; }

.gp-dot { display: inline-block; width: 8px; height: 8px; border-radius: 999px;
  margin-right: 7px; vertical-align: middle; }
.gp-on { background: #8C9A6A; box-shadow: 0 0 8px #8C9A6A88; }
.gp-off { background: #B06A5C; box-shadow: 0 0 8px #B06A5C88; }

.gp-score { text-align: right; }
.gp-score .k { font-size: .62rem; text-transform: uppercase; letter-spacing: .16em;
  color: #8E7D68; }
.gp-scoreval { font-size: 2.05rem; color: #C9A86A; line-height: 1; }

/* ---- expander (Briefing) ---- */
[data-testid="stExpander"] { border: 0 !important; background: transparent !important;
  box-shadow: none !important; margin-top: 6px; }
[data-testid="stExpander"] summary { padding: 8px 2px !important;
  border-top: 1px solid #2C241C; }
[data-testid="stExpander"] summary svg,
[data-testid="stExpander"] summary [data-testid="stIconMaterial"] {
  display: none !important; }
[data-testid="stExpander"] summary p {
  font-size: .72rem !important; text-transform: uppercase; letter-spacing: .2em;
  color: #A7977F !important; }
[data-testid="stExpander"] summary p::after { content: " ›"; color: #6E5E49; }
[data-testid="stExpander"] summary:hover p { color: #C9A86A !important; }

/* briefing internals */
.gp-bk { font-size: .68rem; text-transform: uppercase; letter-spacing: .18em;
  color: #998772; margin-bottom: .35rem; }
.gp-stagehead { font-size: 1.05rem; color: #F2E9D6; }
.gp-stagesub { color: #A7977F; font-size: .92rem; margin-top: .15rem; }

/* mini pro-log table */
.gp-log { width: 100%; border-collapse: collapse; font-size: .95rem; }
.gp-log th { text-align: left; font-size: .64rem; text-transform: uppercase;
  letter-spacing: .14em; color: #8E7D68; font-weight: 500;
  border-bottom: 1px solid #38301F; padding: 4px 8px 6px; }
.gp-log td { padding: 5px 8px; border-bottom: 1px solid #261F18; color: #DCCFB6; }
.gp-w { color: #A9B486; font-weight: 600; }
.gp-l { color: #B06A5C; font-weight: 600; }

/* metrics inside briefing */
[data-testid="stMetricValue"] { font-family: 'Cormorant Garamond', serif;
  color: #F2E9D6; }
[data-testid="stMetricLabel"] p { font-size: .68rem !important;
  text-transform: uppercase; letter-spacing: .14em; color: #998772 !important; }

/* filter widgets */
[data-testid="stWidgetLabel"] p { font-size: .7rem !important;
  text-transform: uppercase; letter-spacing: .16em; color: #998772 !important; }

.gp-foot { color: #7C6C58; font-size: .86rem; font-style: italic;
  border-top: 1px solid #2C241C; padding-top: 1rem; margin-top: 1.5rem; }
</style>
"""


@st.cache_resource
def get_conn():
    return dd.connect()


@st.cache_data(ttl=300)
def load_all():
    conn = get_conn()
    conn.rollback()  # release any aborted txn from a prior cached call
    with conn.cursor() as cur:
        patch, last_day = dd.current_patch_and_freshness(cur)
        rows = dd.watchlist(cur)
        logs = dd.pro_logs(cur, patch) if patch else {}
        curves = dd.pickrate_curves(cur, patch) if patch else {}
        stage = dd.stage_status(cur)
    return patch, last_day, rows, logs, curves, stage


def pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def stage_bits(stage, key, pos):
    """(dot_class, short_label, long_detail) for a champ/role's stage status."""
    s = stage.get((key, pos))
    if not s:
        return ("gp-off", "Not yet on stage",
                "Has not appeared in LCK/LEC in 2026 — a genuinely pre-pro pick.")
    leagues = "/".join(s["leagues"])
    return ("gp-on", f"On stage · {s['first']}",
            f"{s['games']} stage games ({leagues}), through {s['last']}.")


def pro_names_for(logs, cid, pos):
    seen, names = set(), []
    for e in logs.get((cid, pos), []):
        if e["pro"] not in seen:
            seen.add(e["pro"])
            names.append(e["pro"])
    return names


def card_summary_html(rank, r, names, stage):
    dot, short, _ = stage_bits(stage, r["champ_key"], r["team_position"])
    badges = ""
    if r["novel"]:
        badges += '<span class="gp-tag gp-new">New</span>'
    if (r["pick_velocity"] or 0) > 0:
        badges += '<span class="gp-tag gp-rise">Rising</span>'

    if names:
        shown = ", ".join(names[:3])
        if len(names) > 3:
            shown += f" +{len(names) - 3}"
        pros = (f'<span class="gp-names">{shown}</span>'
                f'<span class="sub"> · {len(names)}</span>')
    else:
        pros = '<span class="gp-empty">none tracked</span>'

    return f"""<div class="gp-row">
  <div class="gp-rank">{rank:02d}</div>
  <div>
    <span class="gp-name">{r['champion_name']}</span>
    <span class="gp-role">{r['role']}</span>
    <div class="gp-badges">{badges}</div>
  </div>
  <div class="gp-cell"><div class="k">Pros labbing</div><div class="v">{pros}</div></div>
  <div class="gp-cell"><div class="k">Win rate</div>
    <div class="v">{pct(r['shrunk_wr'])} <span class="sub">{r['games']:,}g</span></div></div>
  <div class="gp-cell"><div class="k">Pro stage</div>
    <div class="v"><span class="gp-dot {dot}"></span>{short}</div></div>
  <div class="gp-score"><div class="k">Score</div>
    <div class="gp-scoreval">{r['score']:.0f}</div></div>
</div>"""


def pro_log_html(log):
    rows = ""
    for e in log:
        rc = "gp-w" if e["win"] else "gp-l"
        res = "W" if e["win"] else "L"
        rows += (f"<tr><td>{e['pro']}</td><td>{e['team'] or ''}</td>"
                 f"<td>{e['date']}</td><td class='{rc}'>{res}</td></tr>")
    return ("<table class='gp-log'><tr><th>Pro</th><th>Team</th><th>Date</th>"
            f"<th>R</th></tr>{rows}</table>")


def trend_chart(series):
    df = pd.DataFrame(series, columns=["day", "pick_rate"])
    grad = alt.Gradient(gradient="linear",
                        stops=[alt.GradientStop(color="#C9A86A00", offset=0),
                               alt.GradientStop(color="#C9A86A38", offset=1)],
                        x1=1, x2=1, y1=1, y2=0)
    return (alt.Chart(df).mark_area(
                line={"color": BRASS, "strokeWidth": 2.2},
                color=grad, point={"color": BRASS, "size": 42})
            .encode(
                x=alt.X("day:T", title=None,
                        axis=alt.Axis(format="%b %d", tickCount=5)),
                y=alt.Y("pick_rate:Q", title=None, axis=alt.Axis(format="%")),
                tooltip=["day:T", alt.Tooltip("pick_rate:Q", format=".2%")])
            .properties(height=210, background="transparent")
            .configure_axis(labelColor=MUTED, gridColor="#FFFFFF0D",
                            domainColor="#FFFFFF12", tickColor="#FFFFFF12",
                            labelFont="EB Garamond", labelFontSize=12)
            .configure_view(strokeWidth=0))


def briefing(r, logs, curves, stage):
    cid, pos = r["champion_id"], r["team_position"]
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown('<div class="gp-bk">Pick-rate trend · share of games, '
                    'this patch</div>', unsafe_allow_html=True)
        series = curves.get((cid, pos), [])
        if len(series) >= 2:
            st.altair_chart(trend_chart(series), use_container_width=True)
        else:
            st.caption("Not enough days on this patch yet to plot a trend.")
        m1, m2, m3 = st.columns(3)
        m1.metric("Shrunken WR", pct(r["shrunk_wr"]))
        m2.metric("WR floor (95%)", pct(r["wr_lcb"]))
        m3.metric("Soloq games", f"{r['games']:,}")
    with right:
        dot, _, detail = stage_bits(stage, r["champ_key"], pos)
        st.markdown('<div class="gp-bk">Pro stage status · 2026</div>',
                    unsafe_allow_html=True)
        head = "On stage" if dot == "gp-on" else "Not yet on stage"
        st.markdown(f'<div class="gp-stagehead"><span class="gp-dot {dot}">'
                    f'</span>{head}</div><div class="gp-stagesub">{detail}'
                    f'</div>', unsafe_allow_html=True)
        st.markdown('<div class="gp-bk" style="margin-top:1.1rem">Pros '
                    'practicing it · this patch</div>', unsafe_allow_html=True)
        log = logs.get((cid, pos), [])
        if log:
            st.markdown(pro_log_html(log), unsafe_allow_html=True)
        else:
            st.markdown('<div class="gp-empty">No tracked pros on this pick '
                        'yet.</div>', unsafe_allow_html=True)


def main():
    st.markdown(THEME_CSS, unsafe_allow_html=True)
    patch, last_day, rows, logs, curves, stage = load_all()

    st.markdown(
        '<div class="gp-mast"><div class="gp-title">GetPrio<span class="dot">.'
        '</span></div><div class="gp-sub">Draft Radar — emerging picks in KR '
        'Master+ soloq, before they reach the stage</div></div>'
        '<div class="gp-rule"></div>', unsafe_allow_html=True)

    if not patch:
        st.warning("No emergence report yet. Run the daily pipeline first.")
        return

    novel_n = sum(1 for r in rows if r["novel"])
    pro_n = sum(1 for r in rows if (r["pro_soloq_games"] or 0) > 0)
    st.markdown(
        f'<div class="gp-kpis">'
        f'<div class="gp-kpi"><div class="k">Patch</div>'
        f'<div class="v accent">{patch}</div></div>'
        f'<div class="gp-kpi"><div class="k">Novel emerging</div>'
        f'<div class="v">{novel_n}</div></div>'
        f'<div class="gp-kpi"><div class="k">Pros labbing</div>'
        f'<div class="v">{pro_n}</div></div>'
        f'<div class="gp-kpi"><div class="k">Data through</div>'
        f'<div class="v">{last_day.strftime("%b %d") if last_day else "—"}'
        f'</div></div></div>',
        unsafe_allow_html=True)

    st.write("")
    f1, f2, f3 = st.columns([2, 2, 3])
    novel_only = f1.toggle("Novel only · coach view", value=True,
                           help="Hide picks that were already meta last patch.")
    pros_only = f2.toggle("Pros labbing only", value=False)
    role = f3.radio("Role", ["All", *dd.ROLE_ORDER], horizontal=True)

    view = rows
    if novel_only:
        view = [r for r in view if r["novel"]]
    if pros_only:
        view = [r for r in view if (r["pro_soloq_games"] or 0) > 0]
    if role != "All":
        view = [r for r in view if r["role"] == role]

    if not view:
        st.info("Nothing matches these filters. Loosen them above.")
        return

    MAX_CARDS = 25
    shown = view[:MAX_CARDS]
    extra = len(view) - len(shown)
    st.markdown(
        f'<h3 style="margin:1.4rem 0 .2rem">On the radar '
        f'<span style="color:#8E7D68;font-size:1rem">— top {len(shown)}'
        f'{f" of {len(view)}" if extra else ""} by emergence score</span>'
        f'</h3>', unsafe_allow_html=True)

    for rank, r in enumerate(shown, 1):
        with st.container(border=True):
            names = pro_names_for(logs, r["champion_id"], r["team_position"])
            st.markdown(card_summary_html(rank, r, names, stage),
                        unsafe_allow_html=True)
            with st.expander("Briefing"):
                briefing(r, logs, curves, stage)

    st.markdown(
        '<div class="gp-foot">Scores use crude starting weights pending '
        'backtest validation — read the evidence (trend, pros, sample), not '
        'just the number. “Novel” = not an established pick last patch.</div>',
        unsafe_allow_html=True)


main()
