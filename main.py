#!/usr/bin/env python3
"""Forecast Exophase earn rates for GTA VI (PS5) trophies at a case target.

Model: an earn rate is a ratio, so forecast its two parts separately.
  rate_i = earners_i / players_tracked
Earners are integers hiding inside the printed rates: a rate printed to 2
decimals at P tracked players recovers its earner count by rounding, and
the case's trophies_earned total confirms the recovery.

Two regimes, one model (growth of counts against time, then a ratio):

  * Several distinct page snapshots: fit pool and earners counts as
    straight lines against log-time (a launch curve is close to linear
    in ln(1 + t/tau); fitting log-counts instead bends the extrapolation
    the wrong way and was v2's measured bias). Each trophy's count slope
    is shrunk toward its share of the total-earns slope, the projected
    change is damped by how far beyond the observed span it reaches,
    and the spread comes from the fits' own residuals.

  * One frozen snapshot (the launch-night practice): the page is stale
    and a refresh lands before the target. The pool grows at the
    early-launch rate evidenced by this game's first refresh (638 ->
    3,868 tracked players over one refresh span). Earners grow by a
    hurdle-Poisson law fitted to that same refresh: with probability
    pi a trophy's count does not move at all across the refresh, and
    otherwise it gains Poisson(mu) earners with mu growing superlinearly
    in its current count (mu = A * e^gamma) -- across the observed
    refresh, growth concentrated on the trophies players had already
    earned most, and a large share did not move. The forecast's five
    quantiles are read off that predictive distribution trophy by
    trophy, widened slightly for pool and law uncertainty, so the
    spread is the model's own rather than a symmetric band.
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
# 0.386/h over the 280-minute span reproduces the one observed refresh
# of this page (638 -> ~3,868 players).
PRIOR_CADENCE_MIN = 280.0
PRIOR_GROWTH_PER_HOUR = 0.386
PRIOR_GROWTH_DECAY_HOURS = 48.0

# Earners growth law across one refresh of a frozen page, fitted to the
# game's observed first refresh (see the module docstring): a trophy's
# count stays put with probability HURDLE_PI, else gains Poisson(mu)
# with mu = GROWTH_A * earners**GROWTH_GAMMA over LAW_SPAN_HOURS,
# scaling linearly with the span to the answering refresh.
GROWTH_A = 0.016
GROWTH_GAMMA = 2.5
HURDLE_PI = 0.10
LAW_SPAN_HOURS = PRIOR_CADENCE_MIN / 60.0

# Extra logit spread on the stale path's distribution quantiles, for
# pool and law uncertainty the fitted distribution does not carry. The
# lower side is wider: after a refresh the dominant risk is dilution
# (the pool jumping further than the prior, pushing every rate down),
# while earners growth is bounded by how far players can progress.
SIGMA_EXTRA_STALE_LO = 0.16
SIGMA_EXTRA_STALE_HI = 0.08

SIGMA_FRESH = 0.05

# General path: log-time basis (hours) for the count fits, the
# pseudo-count at which a trophy's own slope is trusted as much as its
# share of the total-earns slope, and the spread model: a floor plus
# the pooled fit residual, growing slowly with the projection span.
LOGTIME_TAU_HOURS = 8.0
SHRINK_COUNTS = 25.0
SIGMA_GENERAL_FLOOR = 0.04
SIGMA_GENERAL_PER_SQRT_DAY = 0.03


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


def _poisson_pmf(mu):
    """Poisson pmf over its effective support, as a {k: p} dict.

    Mode-centred so large mu does not underflow.
    """
    if mu <= 0:
        return {0: 1.0}
    m = int(mu)
    logp = -mu + m * math.log(mu) - math.lgamma(m + 1)
    pmf = {m: math.exp(logp)}
    p = pmf[m]
    k = m
    while k > 0:
        p = p * k / mu
        k -= 1
        pmf[k] = p
    p = pmf[m]
    k = m
    while p > 1e-13:
        p = p * mu / (k + 1)
        k += 1
        pmf[k] = p
    s = sum(pmf.values())
    return {k: v / s for k, v in pmf.items()}


def _growth_mu(earners, span_hours, players_hat, players0):
    """Mean earners gain of one trophy across a frozen-page refresh.

    The fitted law, with two extrapolation guards for counts far above
    the fitted range: the gain cannot push the count past the projected
    pool, and a trophy's count cannot outgrow the pool's own growth by
    more than half again.
    """
    if earners <= 0:
        return 0.0
    mu = GROWTH_A * (earners ** GROWTH_GAMMA) * (span_hours / LAW_SPAN_HOURS)
    pool_factor = players_hat / max(1, players0)
    mu = min(mu, earners * (1.5 * pool_factor - 1.0))
    mu = min(mu, max(0.0, players_hat - earners))
    return max(0.0, mu)


def _distribution_row(earners, span_hours, players_hat, players0):
    """Five quantiles of one trophy's rate at the answering refresh,
    read off its hurdle-Poisson predictive distribution."""
    if earners >= players0:
        return [100.0] * 5
    mu = _growth_mu(earners, span_hours, players_hat, players0)
    dist = {}
    for d, p in _poisson_pmf(mu).items():
        E = min(earners + d, players_hat)
        dist[E] = dist.get(E, 0.0) + (1.0 - HURDLE_PI) * p
    y0 = 100.0 * earners / players_hat
    dist[earners] = dist.get(earners, 0.0) + HURDLE_PI
    # CDF over rates, interpolated linearly in logit space.
    pts = sorted((100.0 * E / players_hat, p) for E, p in dist.items())
    cdf = []
    run = 0.0
    for y, p in pts:
        run += p
        cdf.append(run)
    row = []
    for tau, z in zip(LEVELS, Z):
        if tau <= cdf[0]:
            q = pts[0][0]
        elif tau >= cdf[-1]:
            q = pts[-1][0]
        else:
            q = pts[-1][0]
            for i in range(1, len(pts)):
                if cdf[i] >= tau:
                    y_lo, y_hi = pts[i - 1][0], pts[i][0]
                    c_lo, c_hi = cdf[i - 1], cdf[i]
                    if y_hi <= y_lo or c_hi <= c_lo:
                        q = y_hi
                    else:
                        f = (tau - c_lo) / (c_hi - c_lo)
                        q = rate_of_logit(logit_of_rate(y_lo)
                                          + f * (logit_of_rate(y_hi)
                                                 - logit_of_rate(y_lo)))
                    break
        sig = SIGMA_EXTRA_STALE_LO if z < 0 else SIGMA_EXTRA_STALE_HI
        q = rate_of_logit(logit_of_rate(q) + z * sig)
        row.append(round(min(100.0, max(0.0, q)), 4))
    for i in range(1, len(row)):
        if row[i] < row[i - 1]:
            row[i] = row[i - 1]
    return row


def forecast_stale(snaps, case, at, target, release):
    """One frozen snapshot: project the refresh the target's page shows.

    Returns one quantile row per trophy.
    """
    snap = snaps[0]
    anchor = snap.first
    cadence = timedelta(minutes=PRIOR_CADENCE_MIN)
    refresh_at = anchor + cadence
    if refresh_at <= at or refresh_at > target + timedelta(minutes=20.0):
        # A refresh was already due before the checkpoint and the page
        # did not change, or none lands before the target: the answer
        # is this very page, carried as it stands.
        return [quantile_row(r if r is not None else 0.01, SIGMA_FRESH)
                for r in snap.rates]
    age_hours = max(0.0, (anchor - release).total_seconds() / 3600.0)
    growth = PRIOR_GROWTH_PER_HOUR * math.exp(-age_hours
                                              / PRIOR_GROWTH_DECAY_HOURS)
    hours = (refresh_at - anchor).total_seconds() / 3600.0
    players_hat = snap.players * min(50.0, math.exp(growth * hours))
    rows = []
    for earners, rate in zip(snap.earners, snap.rates):
        if rate is None or earners is None:
            rows.append(quantile_row(0.01, SIGMA_FRESH))
        else:
            rows.append(_distribution_row(earners, hours, players_hat,
                                          snap.players))
    return rows


def _logtime(hours):
    return math.log(1.0 + hours / LOGTIME_TAU_HOURS)


def _linfit(xs, ys):
    """Least squares y ~ a + b x; returns (a, b, residual sd)."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var <= 0:
        return my, 0.0, 0.0
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    a = my - b * mx
    resid = math.sqrt(sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys)) / n)
    return a, b, resid


def forecast_general(snaps, case, at, target, release):
    """Several snapshots: fit count growth, project to the refresh.

    Pool and total earns are fitted as counts linear in log-time. Each
    trophy's count slope is shrunk toward its share of the total slope,
    and the projected change is damped by the share of the total span
    the data covers: a projection as far beyond the data as the data
    itself spans is trusted half, a far one far less. When the observed
    counts barely move, every slope is near zero and the forecast
    collapses onto the last page on its own.
    """
    snaps = snaps[-12:]
    last = snaps[-1]
    hours = [(s.first - snaps[0].first).total_seconds() / 3600.0
             for s in snaps]
    xs = [_logtime(h) for h in hours]
    gaps = [(snaps[i + 1].first - snaps[i].first).total_seconds() / 60.0
            for i in range(len(snaps) - 1)]
    gaps.sort()
    cadence = timedelta(minutes=gaps[len(gaps) // 2]) if gaps \
        else timedelta(minutes=PRIOR_CADENCE_MIN)
    refresh_at = last.first
    while refresh_at + cadence <= target:
        refresh_at += cadence
    dx = _logtime((refresh_at - snaps[0].first).total_seconds() / 3600.0) \
        - xs[-1]
    span = xs[-1] - xs[0]
    damp = span / (span + dx) if span + dx > 0 else 1.0
    # Pool and total-earns count fits.
    _, pool_slope, pool_resid = _linfit(xs, [float(s.players)
                                             for s in snaps])
    pool_slope = max(0.0, pool_slope)
    players_hat = max(float(last.players),
                      last.players + pool_slope * dx * damp)
    totals = [float(s.earned_total or 0) for s in snaps]
    _, total_slope, total_resid = _linfit(xs, totals)
    total_slope = max(0.0, total_slope)
    mean_total = sum(totals) / len(totals)
    # Spread: the fits' relative residuals, pooled, on the logit scale,
    # growing slowly with the projection span.
    rel = 0.0
    if mean_total > 0:
        rel = total_resid / mean_total
    if last.players > 0:
        rel = max(rel, pool_resid / last.players)
    days = max(0.0, (refresh_at - last.first).total_seconds() / 86400.0)
    sigma = math.sqrt(SIGMA_GENERAL_FLOOR ** 2 + rel ** 2
                      + (SIGMA_GENERAL_PER_SQRT_DAY * math.sqrt(days)) ** 2)
    sigma = min(0.35, sigma)
    rows = []
    for j, (earners, rate) in enumerate(zip(last.earners, last.rates)):
        if rate is None or earners is None:
            rows.append(quantile_row(0.01, sigma))
        elif earners >= last.players:
            rows.append(quantile_row(100.0, sigma))
        else:
            pts = [(x, float(s.earners[j])) for x, s in zip(xs, snaps)
                   if s.earners[j] is not None]
            if len(pts) >= 2:
                _, slope_i, _ = _linfit([p[0] for p in pts],
                                        [p[1] for p in pts])
                mean_e = sum(p[1] for p in pts) / len(pts)
            else:
                slope_i, mean_e = None, float(earners)
            share = (mean_e / mean_total) if mean_total > 0 else 0.0
            target_slope = total_slope * share
            if slope_i is None:
                slope = target_slope
            else:
                w = mean_e / (mean_e + SHRINK_COUNTS)
                slope = w * slope_i + (1.0 - w) * target_slope
            slope = max(0.0, slope)
            e_hat = earners + slope * dx * damp
            e_hat = min(e_hat, players_hat)
            median = round(100.0 * e_hat / players_hat, 2)
            rows.append(quantile_row(median, sigma))
    return rows


def forecast_case(case):
    at = parse_time(case["at"])
    target = parse_time(case["target"])
    release = parse_time(case["game"]["release_date"] + "T00:00:00Z")
    snaps = snapshots_of(case["history"])
    if len(snaps) <= 1:
        rows_q = forecast_stale(snaps, case, at, target, release)
    else:
        rows_q = forecast_general(snaps, case, at, target, release)
    return {"trophies": [{"id": t["id"], "q": q}
                          for t, q in zip(case["trophies"], rows_q)]}


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
