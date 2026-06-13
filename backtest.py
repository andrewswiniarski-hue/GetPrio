"""Backtest the emergence score against pro-play ground truth.

Replays the daily emergence ranking *as-of* each past day of a patch — using
only the day-grained champ_daily_stats that would have existed on that day —
then asks of every champion/role that later appeared in pro play on the same
patch: did our top-N flag it *before* its first stage game, and with how much
lead time?

This is the validation the whole project hinges on. It reuses the exact
production scoring (compute_stats.composite_score) so the replay matches the
live report. Read-only against the warehouse; reports to stdout and writes a
per-pick CSV to data/backtest_<patch>.csv.

Run:  python backtest.py [--patch 16.11] [--topn 10 20 30] [--leagues LCK LEC]

KNOWN LIMITATION (v1): "caught_early" currently rewards any pick that ranks
top-N before its stage debut — including perennial strong picks that rank
high from day one purely on win-rate edge (velocity needs 3 days, so the
earliest cutoffs score on WR alone). That inflates the detection rate and
overstates lead time: re-confirming that Ezreal is good is not emergence
detection. A faithful validation of the *thesis* needs a novelty baseline
(was this champ/role already established in the prior patch?) and a precision
side (of the picks we flag, how many actually reach stage?). This v1 measures
recall + lead time only; treat the headline as an upper bound.
"""
import argparse
import csv
import logging
import os
import statistics
from collections import defaultdict

import db
from champions import champ_key
from compute_stats import (MIN_DURATION_S, MIN_GAMES, NOVELTY_PICKRATE,
                           PRIOR_STRENGTH, baseline_pick_rates,
                           composite_score, pick_velocity, prior_patch_of)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def load_daily(cur, patch):
    """(champ_id,pos) -> [(day, games, wins, total_games_day)]; champ_id->name.

    kr-only today, so one platform row per (day,champ,pos); max(total_games_day)
    is that platform's daily denominator. Revisit the denominator when euw1/na1
    come back (pick rate would then sum platform totals)."""
    cur.execute(
        """
        SELECT day, champion_id, max(champion_name), team_position,
               sum(games)::int, sum(wins)::int, max(total_games_day)::int
        FROM champ_daily_stats
        WHERE patch = %s
        GROUP BY day, champion_id, team_position
        ORDER BY day
        """,
        (patch,),
    )
    series = defaultdict(list)
    names = {}
    for day, cid, name, pos, games, wins, tot in cur.fetchall():
        series[(cid, pos)].append((day, games, wins, tot))
        names[cid] = name
    return series, names


def load_pro_soloq(cur, patch):
    """(champ_id,pos) -> [(day, pro_soloq_games)] on this patch."""
    cur.execute(
        """
        SELECT date(m.game_creation), p.champion_id,
               coalesce(p.team_position, 'UNKNOWN'), count(*)
        FROM participants p
        JOIN matches m USING (match_id)
        JOIN pro_accounts pa ON pa.puuid = p.puuid AND pa.active
        WHERE m.patch = %s AND m.game_duration_s >= %s
        GROUP BY 1, 2, 3
        """,
        (patch, MIN_DURATION_S),
    )
    pro = defaultdict(list)
    for day, cid, pos, n in cur.fetchall():
        pro[(cid, pos)].append((day, n))
    return pro


def load_ground_truth(cur, patch, leagues):
    """(champ_key, pos) -> first stage date on this patch."""
    q = ["SELECT champ_key, team_position, min(game_date) FROM pro_picks",
         "WHERE patch = %s AND team_position IS NOT NULL"]
    params = [patch]
    if leagues:
        q.append("AND league = ANY(%s)")
        params.append(leagues)
    q.append("GROUP BY 1, 2")
    cur.execute(" ".join(q), params)
    return {(k, pos): d for k, pos, d in cur.fetchall()}


def score_asof(series, pro, cutoff):
    """(champ_id,pos) -> emergence score, exactly as compute_stats would have
    produced it on `cutoff`, using only day <= cutoff."""
    agg = {}
    tot_g = tot_w = 0
    for key, rows in series.items():
        g = sum(r[1] for r in rows if r[0] <= cutoff)
        w = sum(r[2] for r in rows if r[0] <= cutoff)
        if g == 0:
            continue
        agg[key] = (g, w, rows)
        tot_g += g
        tot_w += w
    prior_mean = tot_w / tot_g if tot_g else 0.5

    scores = {}
    for key, (g, w, rows) in agg.items():
        if g < MIN_GAMES or key[1] == "UNKNOWN":
            continue
        days = [r[0] for r in rows if r[0] <= cutoff]
        rates = [(r[1] / r[3]) if r[3] else 0.0
                 for r in rows if r[0] <= cutoff]
        idx = [(d - days[0]).days for d in days]
        velocity = pick_velocity(idx, rates)
        shrunk = (w + PRIOR_STRENGTH * prior_mean) / (g + PRIOR_STRENGTH)
        pro_n = sum(n for d, n in pro.get(key, []) if d <= cutoff)
        scores[key] = composite_score(shrunk, prior_mean, velocity, pro_n)
    return scores


def run(patch, topns, leagues):
    conn = db.get_conn()
    with conn.cursor() as cur:
        series, names = load_daily(cur, patch)
        pro = load_pro_soloq(cur, patch)
        truth = load_ground_truth(cur, patch, leagues)
        # Novelty baseline: pick share in the prior patch, keyed by champ_key
        # so it joins the ground truth. Empty if no prior patch exists.
        prior = prior_patch_of(cur, patch)
        cur.execute("SELECT DISTINCT champion_id, champion_name "
                    "FROM champ_daily_stats")
        id_key_all = {cid: champ_key(n) for cid, n in cur.fetchall()}
        baseline = {}
        for (cid, pos), share in baseline_pick_rates(cur, prior).items():
            baseline[(id_key_all.get(cid, str(cid)), pos)] = share
    conn.close()

    if not series:
        raise SystemExit(f"No champ_daily_stats for patch {patch}")
    if not truth:
        raise SystemExit(f"No pro_picks for patch {patch}"
                         f"{' leagues=' + ','.join(leagues) if leagues else ''}")

    days = sorted({r[0] for rows in series.values() for r in rows})
    d0, dN = days[0], days[-1]
    stage_dates = sorted(truth.values())
    log.info("Patch %s | soloq days %s..%s (%d) | stage picks %d, first %s "
             "last %s | leagues=%s", patch, d0, dN, len(days), len(truth),
             stage_dates[0], stage_dates[-1], ",".join(leagues) if leagues
             else "all")
    if prior:
        log.info("Novelty baseline: prior patch %s (%d cells)", prior,
                 len(baseline))
    else:
        log.info("Novelty baseline: none (no prior patch in warehouse); "
                 "novel-only metrics will be skipped")

    # champ_id -> champ_key, and (champ_key,pos) -> warehouse cells
    id_key = {cid: champ_key(name) for cid, name in names.items()}
    cells_for = defaultdict(list)
    for (cid, pos) in series:
        cells_for[(id_key[cid], pos)].append((cid, pos))

    # earliest day each cell entered the top-N (days ascending => first wins)
    earliest = {N: {} for N in topns}
    for d in days:
        ranked = sorted(score_asof(series, pro, d).items(),
                        key=lambda kv: kv[1], reverse=True)
        for N in topns:
            for cell, _ in ranked[:N]:
                earliest[N].setdefault(cell, d)

    results = {}
    for N in topns:
        recs = []
        for (k, pos), F in truth.items():
            cells = cells_for.get((k, pos), [])
            flag_days = [earliest[N][c] for c in cells if c in earliest[N]]
            flagged = min(flag_days) if flag_days else None
            runway = F > d0  # did any soloq day precede the stage debut?
            if not cells:
                status, lead = "absent_in_soloq", None
            elif flagged is not None and flagged < F:
                status, lead = "caught_early", (F - flagged).days
            elif flagged is not None:
                status, lead = "caught_not_early", (F - flagged).days
            else:
                status, lead = "missed", None
            # novel = not an established pick in the prior patch (None=unknown)
            novel = (None if not prior
                     else baseline.get((k, pos), 0.0) < NOVELTY_PICKRATE)
            recs.append({"champ": k, "pos": pos, "first_stage": F,
                         "flagged": flagged, "lead_days": lead,
                         "runway": runway, "status": status, "novel": novel})
        results[N] = recs
    return patch, d0, dN, results


def _summary_table(results, subset):
    """Print the detection summary for a subset filter applied to each N."""
    print(f"{'top-N':>6} {'evaluable':>9} {'caught_early':>12} "
          f"{'rate':>6} {'median_lead':>11} {'missed':>6} {'absent':>6}")
    for N in sorted(results):
        recs = [r for r in results[N] if subset(r)]
        # fair denominator: stage picks present in our soloq data with a
        # pre-debut window to be caught in.
        evaluable = [r for r in recs if r["status"] != "absent_in_soloq"
                     and r["runway"]]
        early = [r for r in evaluable if r["status"] == "caught_early"]
        missed = [r for r in evaluable if r["status"] == "missed"]
        absent = [r for r in recs if r["status"] == "absent_in_soloq"]
        leads = [r["lead_days"] for r in early]
        rate = len(early) / len(evaluable) if evaluable else 0.0
        med = f"{statistics.median(leads):.1f}d" if leads else "-"
        print(f"{N:>6} {len(evaluable):>9} {len(early):>12} {rate:>6.0%} "
              f"{med:>11} {len(missed):>6} {len(absent):>6}")


def report(patch, d0, dN, results):
    topns = sorted(results)
    has_novel = any(r["novel"] is not None for r in results[topns[0]])
    print()
    print(f"=== Backtest: patch {patch} (soloq {d0}..{dN}) ===")
    print("ALL stage picks:")
    _summary_table(results, lambda r: True)
    if has_novel:
        print("\nNOVEL picks only (not established in the prior patch) "
              "— the real thesis test:")
        _summary_table(results, lambda r: r["novel"])
    else:
        print("\n(novel-only view skipped: no prior-patch baseline for this "
              "patch yet)")

    # per-pick detail at the middle N
    N = topns[len(topns) // 2]
    recs = sorted([r for r in results[N]
                   if r["status"] in ("caught_early", "caught_not_early",
                                      "missed")],
                  key=lambda r: (r["lead_days"] is None,
                                 -(r["lead_days"] or 0)))
    print(f"\n--- per-pick detail @ top-{N} "
          f"(picks present in our soloq data) ---")
    print(f"{'champ':<14}{'pos':<9}{'nov':<4}{'first_stage':<13}"
          f"{'flagged':<13}{'lead':>6}  status")
    for r in recs:
        lead = f"{r['lead_days']}d" if r["lead_days"] is not None else "-"
        flagged = str(r["flagged"]) if r["flagged"] else "never"
        nov = "NEW" if r["novel"] else ("" if r["novel"] is False else "?")
        print(f"{r['champ']:<14}{r['pos']:<9}{nov:<4}"
              f"{str(r['first_stage']):<13}{flagged:<13}{lead:>6}  "
              f"{r['status']}")


def write_csv(patch, results):
    topns = sorted(results)
    os.makedirs("data", exist_ok=True)
    path = os.path.join("data", f"backtest_{patch}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["top_n", "champ", "pos", "novel", "first_stage", "flagged",
                    "lead_days", "runway", "status"])
        for N in topns:
            for r in results[N]:
                w.writerow([N, r["champ"], r["pos"],
                            "" if r["novel"] is None else r["novel"],
                            r["first_stage"], r["flagged"] or "",
                            r["lead_days"] if r["lead_days"] is not None
                            else "", r["runway"], r["status"]])
    log.info("Wrote per-pick detail to %s", path)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--patch", default=None,
                    help="default: patch with the most soloq days that also "
                         "has pro_picks")
    ap.add_argument("--topn", type=int, nargs="+", default=[10, 20, 30])
    ap.add_argument("--leagues", nargs="+", default=None)
    args = ap.parse_args()

    if not args.patch:
        conn = db.get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.patch, count(DISTINCT c.day) AS days
                FROM champ_daily_stats c
                WHERE c.patch IN (SELECT DISTINCT patch FROM pro_picks)
                GROUP BY c.patch ORDER BY days DESC, c.patch DESC LIMIT 1
                """
            )
            row = cur.fetchone()
        conn.close()
        if not row:
            raise SystemExit("No patch has both soloq stats and pro_picks yet")
        args.patch = row[0]
        log.info("No --patch given; using %s (%d soloq days)", row[0], row[1])

    patch, d0, dN, results = run(args.patch, sorted(args.topn), args.leagues)
    report(patch, d0, dN, results)
    write_csv(patch, results)


if __name__ == "__main__":
    main()
