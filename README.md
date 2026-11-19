# GTA VI Trophy Rarity Forecaster

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/kizuna99/gta6-trophy-forecast/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/kizuna99/gta6-trophy-forecast/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

A deterministic forecaster for the [make.fun](https://make.fun/bounty/gta6-trophy-rarity-forecast-01) bounty "Forecast how many GTA VI players earn each trophy in week one" (gta6-trophy-rarity-forecast-01, $250 in USDC). For every PlayStation 5 trophy of Grand Theft Auto VI it predicts five quantiles (5%, 25%, 50%, 75%, 95%) of the trophy's earn rate on Exophase at the case target.

How it works: an earn rate is a ratio, so the program forecasts its two parts separately. The integer earner count behind each printed rate is recovered exactly (the case's trophies-earned total confirms the recovery), counts are projected to the moment the answering page was refreshed — Exophase shows the state of its last refresh, not of the target — and the forecast is the projected ratio with a spread around it. A trophy every tracked player holds is pinned to the pool itself.

With several distinct page snapshots, pool and earners are fitted as counts linear in log-time (a launch curve is close to linear in ln(1 + t/τ); fitting log-counts instead bends the extrapolation and measurably biases the forecast). Each trophy's count slope is shrunk toward its share of the total-earns slope, the projected change is damped by how far beyond the observed span it reaches, and the spread comes from the fits' own residuals — so when the observed counts barely move, the forecast collapses onto the last page on its own.

With a single frozen snapshot (the launch-night practice shape), the pool grows at the early-launch rate evidenced by this game's first refresh (638 → 3,868 tracked players over one refresh span), and earners grow by a hurdle-Poisson law fitted to that same refresh: with probability π a trophy's count does not move across the refresh at all, and otherwise it gains Poisson(μ) earners with μ = A·e^γ growing superlinearly in its current count — across the observed refresh, growth concentrated on the trophies players had already earned most. The five quantiles are read off that predictive distribution trophy by trophy, widened slightly (more on the downside, where post-refresh dilution risk sits) for pool and law uncertainty. One file, `main.py`, Python 3.13 standard library only, no network.

Run it: `MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py` — it reads every `*.in` case in the input directory and writes one `<id>.out` forecast per case.

Join make.fun with my referral link: https://make.fun/r/qe33fjhv
