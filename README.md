# GTA VI Trophy Rarity Forecaster

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/kizuna99/gta6-trophy-forecast/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/kizuna99/gta6-trophy-forecast/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

A deterministic forecaster for the [make.fun](https://make.fun/bounty/gta6-trophy-rarity-forecast-01) bounty "Forecast how many GTA VI players earn each trophy in week one" (gta6-trophy-rarity-forecast-01, $250 in USDC). For every PlayStation 5 trophy of Grand Theft Auto VI it predicts five quantiles (5%, 25%, 50%, 75%, 95%) of the trophy's earn rate on Exophase at the case target.

How it works: an earn rate is a ratio, so the program forecasts its two parts separately. The integer earner count behind each printed rate is recovered exactly (the case's trophies-earned total confirms the recovery), the tracked-player pool and the earners are projected to the moment the answering page was refreshed — Exophase shows the state of its last refresh, not of the target — and the quantiles are a logit-scale spread around the projected ratio, centred on the 2-decimal grid the page prints. A trophy every tracked player holds is pinned to the pool itself. One file, `main.py`, Python 3.13 standard library only, no network.

Run it: `MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py` — it reads every `*.in` case in the input directory and writes one `<id>.out` forecast per case.

Join make.fun with my referral link: https://make.fun/r/qe33fjhv
