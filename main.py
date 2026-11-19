#!/usr/bin/env python3
"""Forecast Exophase earn rates for GTA VI (PS5) trophies at a case target.

Model: an earn rate is a ratio, so forecast its two parts separately.
  rate_i = earners_i / players_tracked
Earners are integers hiding inside the printed rates: a rate printed to 2
decimals at P tracked players recovers its earner count by rounding, and
the case's trophies_earned total confirms the recovery. Players and
earners are then projected to the moment the answering page was refreshed
(Exophase shows the state of its last refresh, not of the target), and the
five quantiles are a logit-normal spread around the projected ratio.

Two regimes:
  * Several distinct page snapshots in the history: fit the growth of
    players and of total earns across the snapshots, extrapolate the
    refresh cadence, and project both parts to the last refresh at or
    before the target.
  * One frozen snapshot (the launch-night practice): the page is stale
    and a refresh lands before the target. Players grow at an
    early-launch rate from the snapshot until that refresh; earners are
    carried across the refresh unchanged (newly tracked players have not
    registered earns yet), except a trophy every tracked player holds,
    whose earners track the player count itself.
"""

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
Z = (-1.6448536269514722, -0.6744897501960817, 0.0,
     0.6744897501960817, 1.6448536269514722)

# Launch-refresh prior (single frozen snapshot): minutes between Exophase
# refreshes of a hot launch page, and the hourly growth of the tracked
# pool in the first hours after release, decaying with the game's age.
PRIOR_CADENCE_MIN = 280.0
PRIOR_GROWTH_PER_HOUR = 0.386
PRIOR_GROWTH_DECAY_HOURS = 48.0

SIGMA_STALE = 0.02
SIGMA_FRESH = 0.05
SIGMA_PER_SQRT_DAY = 0.10


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def clamp_rate(x):
    return min(99.99, max(0.01, x))


def logit_of_rate(x):
    p = clamp_rate(x) / 100.0
    return math.log(p / (1.0 - p))


def rate_of_logit(v):
    if v >= 0:
        return 100.0 / (1.0 + math.exp(-v))
    e = math.exp(v)
    return 100.0 * e / (1.0 + e)


def recover_earners(rates, players, total_earned):
    """Undo the page's rounding: earners_i ~= rate_i/100 * players."""
    raw = [None if r is None else r / 100.0 * players for r in rates]
    est = [None if v is None else int(math.floor(v + 0.5)) for v in raw]
    if total_earned is not None:
        known = [i for i, v in enumerate(est) if v is not None]
        diff = total_earned - sum(est[i] for i in known)
        if known and 0 < abs(diff) <= len(known):
            frac = sorted(known, key=lambda i: raw[i] - math.floor(raw[i]),
                          reverse=diff > 0)
            step = 1 if diff > 0 else -1
            for i in frac[:abs(diff)]:
                if est[i] + step >= 0:
                    est[i] += step
    return est


class Snapshot:
    """One distinct state of the page, seen over a run of reads."""

    def __init__(self, read):
        self.first = parse_time(read["at"])
        self.last = self.first
        self.players = read["players_tracked"]
        self.earned_total = read["trophies_earned"]
        self.rates = list(read["rates"])
        self.earners = recover_earners(self.rates, self.players,
                                       self.earned_total)

    def same_page(self, read):
        return (read["players_tracked"] == self.players
                and list(read["rates"]) == self.rates)


def snapshots_of(history):
    snaps = []
    for read in history:
        if snaps and snaps[-1].same_page(read):
            snaps[-1].last = parse_time(read["at"])
        else:
            snaps.append(Snapshot(read))
    return snaps


def quantile_row(median_rate, sigma):
    centre = logit_of_rate(median_rate)
    row = []
    for z in Z:
        q = rate_of_logit(centre + z * sigma)
        row.append(round(min(100.0, max(0.0, q)), 4))
    for i in range(1, len(row)):
        if row[i] < row[i - 1]:
            row[i] = row[i - 1]
    return row


def forecast_stale(snaps, case, at, target, release):
    """One frozen snapshot: project the refresh the target's page shows."""
    snap = snaps[0]
    anchor = snap.first
    cadence = timedelta(minutes=PRIOR_CADENCE_MIN)
    refresh_at = anchor + cadence
    if refresh_at <= at:
        # A refresh was already due before the checkpoint and the page
        # did not change: the cadence prior does not govern this page
        # (it has stopped updating, or was never on that clock). Carry
        # the page as it stands, with a wider spread for the doubt.
        medians = [r if r is not None else 0.01 for r in snap.rates]
        return medians, SIGMA_FRESH
    if refresh_at > target + timedelta(minutes=20.0):
        # No refresh before the target: the answer is this very page.
        medians = [r if r is not None else 0.01 for r in snap.rates]
        return medians, SIGMA_FRESH
    age_hours = max(0.0, (anchor - release).total_seconds() / 3600.0)
    growth = PRIOR_GROWTH_PER_HOUR * math.exp(-age_hours
                                              / PRIOR_GROWTH_DECAY_HOURS)
    hours = (refresh_at - anchor).total_seconds() / 3600.0
    players_hat = snap.players * min(50.0, math.exp(growth * hours))
    medians = []
    for earners, rate in zip(snap.earners, snap.rates):
        if rate is None:
            medians.append(0.01)
        elif earners is not None and earners >= snap.players:
            medians.append(100.0)
        else:
            medians.append(100.0 * (earners or 0) / players_hat)
    return medians, SIGMA_STALE


def forecast_general(snaps, case, at, target, release):
    """Several snapshots: fit growth, project to the answering refresh."""
    # Fit on the recent snapshots only: launch-phase growth decays, and
    # the oldest refreshes say little about the coming days.
    snaps = snaps[-6:]
    last = snaps[-1]
    times = [(s.first - snaps[0].first).total_seconds() / 3600.0
             for s in snaps]
    # Log-linear fit of the tracked pool over snapshot times.
    n = len(snaps)
    lp = [math.log(max(1, s.players)) for s in snaps]
    mt = sum(times) / n
    mp = sum(lp) / n
    var = sum((t - mt) ** 2 for t in times)
    g_players = (sum((t - mt) * (v - mp) for t, v in zip(times, lp)) / var
                 if var > 0 else 0.0)
    g_players = max(0.0, min(0.05, g_players))
    # Refresh cadence from the gaps between distinct snapshots.
    gaps = [(snaps[i + 1].first - snaps[i].first).total_seconds() / 60.0
            for i in range(n - 1)]
    gaps.sort()
    cadence = timedelta(minutes=gaps[len(gaps) // 2]) if gaps \
        else timedelta(minutes=PRIOR_CADENCE_MIN)
    refresh_at = last.first
    while refresh_at + cadence <= target:
        refresh_at += cadence
    hours = max(0.0, (refresh_at - last.first).total_seconds() / 3600.0)
    players_hat = last.players * math.exp(g_players * hours)
    # Earners: scale each count by the growth of total earns, fitted the
    # same way; a trophy held by everyone tracks the pool itself.
    le = [math.log(max(1, s.earned_total or 1)) for s in snaps]
    me = sum(le) / n
    g_earned = (sum((t - mt) * (v - me) for t, v in zip(times, le)) / var
                if var > 0 else 0.0)
    g_earned = max(0.0, min(0.05, g_earned))
    earned_scale = math.exp(g_earned * hours)
    medians = []
    for earners, rate in zip(last.earners, last.rates):
        if rate is None:
            medians.append(0.01)
        elif earners is not None and earners >= last.players:
            medians.append(100.0)
        else:
            e_hat = (earners or 0) * earned_scale
            medians.append(100.0 * min(e_hat, players_hat) / players_hat)
    days = max(0.0, (refresh_at - last.first).total_seconds() / 86400.0)
    sigma = SIGMA_FRESH + SIGMA_PER_SQRT_DAY * math.sqrt(days)
    return medians, sigma


def forecast_case(case):
    at = parse_time(case["at"])
    target = parse_time(case["target"])
    release = parse_time(case["game"]["release_date"] + "T00:00:00Z")
    snaps = snapshots_of(case["history"])
    if len(snaps) <= 1:
        medians, sigma = forecast_stale(snaps, case, at, target, release)
    else:
        medians, sigma = forecast_general(snaps, case, at, target, release)
    rows = []
    for trophy, median in zip(case["trophies"], medians):
        # The page prints 2 decimals and the printed value is what is
        # scored, so centre the forecast on the printed grid.
        median = round(min(100.0, max(0.0, median)), 2)
        rows.append({"id": trophy["id"], "q": quantile_row(median, sigma)})
    return {"trophies": rows}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        result = forecast_case(case)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(result, separators=(",", ":")) + "\n",
            encoding="utf-8")


if __name__ == "__main__":
    main()
