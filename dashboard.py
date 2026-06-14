"""GetPrio — coach dashboard over the emergence report.

A busy-coach view: the key evidence for every emerging pick is visible without
clicking (badges, win rate, which pros are on it, stage status); one expander
per pick opens the full briefing (pick-rate curve, pro game log, stage detail).

Run:  streamlit run dashboard.py
Reads DATABASE_URL from env or the gitignored .database_url file.
"""
import altair as alt
import pandas as pd
import streamlit as st

import dashboard_data as dd

st.set_page_config(page_title="GetPrio — Draft Radar", page_icon="🎯",
                   layout="wide")


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


def stage_label(stage, key, pos):
    s = stage.get((key, pos))
    if not s:
        return "🔴 Not yet on stage", "Has not appeared in LCK/LEC in 2026 — " \
            "a genuinely pre-pro pick."
    leagues = "/".join(s["leagues"])
    return (f"🟢 On stage since {s['first']}",
            f"{s['games']} stage games ({leagues}), through {s['last']}.")


def briefing(row, logs, curves, stage):
    cid, pos = row["champion_id"], row["team_position"]
    left, right = st.columns([3, 2])

    with left:
        st.caption("Pick-rate trend (share of games, this patch)")
        series = curves.get((cid, pos), [])
        if len(series) >= 2:
            df = pd.DataFrame(series, columns=["day", "pick_rate"])
            chart = (alt.Chart(df).mark_line(point=True)
                     .encode(x=alt.X("day:T", title=None),
                             y=alt.Y("pick_rate:Q", title=None,
                                     axis=alt.Axis(format="%")),
                             tooltip=["day:T",
                                      alt.Tooltip("pick_rate:Q", format=".2%")])
                     .properties(height=200))
            st.altair_chart(chart, use_container_width=True)
        else:
            st.info("Not enough days on this patch yet to plot a trend.")
        c1, c2, c3 = st.columns(3)
        c1.metric("Shrunken WR", pct(row["shrunk_wr"]))
        c2.metric("WR floor (95%)", pct(row["wr_lcb"]),
                  help="Wilson lower bound — the WR we're confident it clears.")
        c3.metric("Games (soloq)", f"{row['games']:,}")

    with right:
        head, sub = stage_label(stage, row["champ_key"], pos)
        st.caption("Pro stage status (2026)")
        st.markdown(f"**{head}**  \n{sub}")
        st.caption("Pros practicing it on soloq (this patch)")
        log = logs.get((cid, pos), [])
        if log:
            ldf = pd.DataFrame(log)
            ldf["result"] = ldf["win"].map({True: "W", False: "L"})
            ldf = ldf[["pro", "team", "date", "result"]]
            ldf.columns = ["Pro", "Team", "Date", "R"]
            st.dataframe(ldf, hide_index=True, use_container_width=True,
                         height=min(35 + 35 * len(ldf), 250))
        else:
            st.write("— No tracked pros on this pick yet.")


def main():
    patch, last_day, rows, logs, curves, stage = load_all()

    st.title("🎯 GetPrio — Draft Radar")
    st.caption("Emerging champion picks in KR Master+ soloq — *before* they "
               "reach the stage. Win-rate edge × pick-rate velocity × pros "
               "labbing it.")

    if not patch:
        st.warning("No emergence report yet. Run the daily pipeline first.")
        return

    novel_rows = [r for r in rows if r["novel"]]
    pro_rows = [r for r in rows if (r["pro_soloq_games"] or 0) > 0]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Patch", patch)
    k2.metric("Novel emerging picks", len(novel_rows))
    k3.metric("Picks pros are labbing", len(pro_rows))
    k4.metric("Data through", str(last_day))

    # ---- controls ----
    f1, f2, f3 = st.columns([2, 2, 3])
    novel_only = f1.toggle("Novel only (coach view)", value=True,
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
        st.subheader("0 picks on the radar")
        st.info("Nothing matches these filters. Loosen them above.")
        return

    MAX_CARDS = 25
    shown = view[:MAX_CARDS]
    extra = len(view) - len(shown)
    st.subheader(f"Top {len(shown)} on the radar"
                 + (f"  ·  {extra} more below the cut" if extra else ""))
    st.caption("Ranked by emergence score. Each row is glanceable; open "
               "**Briefing** for the trend, the pro game log, and stage detail.")

    for rank, r in enumerate(shown, 1):
        with st.container(border=True):
            head, sub = stage_label(stage, r["champ_key"], r["team_position"])
            top = st.columns([0.5, 3, 2.2, 2.3, 2])
            top[0].markdown(f"### {rank}")
            badges = []
            if r["novel"]:
                badges.append("🆕 NEW")
            if (r["pick_velocity"] or 0) > 0:
                badges.append("📈 rising")
            top[1].markdown(f"### {r['champion_name']}  \n"
                            f"**{r['role']}** &nbsp; {' · '.join(badges)}")

            log = logs.get((r["champion_id"], r["team_position"]), [])
            pro_names = []
            seen = set()
            for e in log:
                if e["pro"] not in seen:
                    seen.add(e["pro"])
                    pro_names.append(e["pro"])
            if pro_names:
                shown = ", ".join(pro_names[:3])
                more = f" +{len(pro_names) - 3}" if len(pro_names) > 3 else ""
                top[2].markdown(f"👤 **{len(pro_names)} pro"
                                f"{'s' if len(pro_names) != 1 else ''}**  \n"
                                f"{shown}{more}")
            else:
                top[2].markdown("👤 —  \nno tracked pros")

            top[3].markdown(f"**WR** {pct(r['shrunk_wr'])} "
                            f"({r['games']:,}g)  \n{head}")
            top[4].markdown(f"**score**  \n### {r['score']:.0f}")

            with st.expander("Briefing"):
                briefing(r, logs, curves, stage)

    st.divider()
    st.caption("Scores use crude starting weights pending backtest validation "
               "— read the *evidence* (trend, pros, sample), not just the "
               "number. Novel = not an established pick last patch.")


main()
