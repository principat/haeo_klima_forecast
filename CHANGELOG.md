# CHANGELOG


## v1.4.0 (2026-10-04)

### Features

- Download link for the history export
  ([`494ac5f`](https://github.com/principat/haeo_klima_forecast/commit/494ac5fb920f363202315ff812a06dc05053e2a9))

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>


## v1.3.0 (2026-10-04)

### Features

- Export stored history as CSV (service and button), update specification
  ([`43e4ba1`](https://github.com/principat/haeo_klima_forecast/commit/43e4ba1bf29061dced90c45ff2828977ff4947f0))

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

- Time-of-day and piecewise-linear model, estimate missing indoor temperature
  ([`f8bc0ea`](https://github.com/principat/haeo_klima_forecast/commit/f8bc0eada6c36f2f4ec3cceeaf04e1252b5a3f8c))

Add local time of day (sin/cos) and outdoor-temperature breakpoints to the regression so the unit's
  schedule and modulation band are covered. Hours without a measured indoor temperature use the mean
  of the measured ones for training only. Training runs in the executor and reports the daily energy
  error as a quality figure. tmp/ is ignored.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>


## v1.2.1 (2026-10-04)

### Bug Fixes

- Retry hours without weather and use forecast API for the last days
  ([`87d7957`](https://github.com/principat/haeo_klima_forecast/commit/87d79579dd0130f3f6b736c8a934d55c42fc8080))

Open-Meteo's archive lags 5 days; those hours were marked synced without outdoor temperature and
  never retried, leaving too few training rows. Now the forecast API fills the recent days,
  incomplete hours are re-synced, and the manual recalculation syncs the history first.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>


## v1.2.0 (2026-10-03)

### Bug Fixes

- Fill indoor temperature gaps from the state history
  ([`795aec7`](https://github.com/principat/haeo_klima_forecast/commit/795aec7c6a8525ba53b5f43065fc845e06275e59))

Carry the last known climate state forward over hours without a state change and backfill missing
  indoor_temp of stored hours, on every sync and before recalculating weights.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

### Documentation

- Update specification for setup wait, indoor gap fill and trained-at sensor
  ([`8490119`](https://github.com/principat/haeo_klima_forecast/commit/84901197323e8a2bb39bf0cef2742e2f258e46aa))

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

### Features

- Add diagnostic sensor for the last weight calculation time
  ([`a07aa6f`](https://github.com/principat/haeo_klima_forecast/commit/a07aa6f1e293279e02f73deb1595056905390663))

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>


## v1.1.1 (2026-10-03)

### Bug Fixes

- Wait for weather forecast before loading the config entry
  ([`1f14750`](https://github.com/principat/haeo_klima_forecast/commit/1f147500fcb27f37956699348a51361d0af61d42))

Raise ConfigEntryNotReady in setup while the weather entity provides no forecast, so HA retries
  instead of loading with empty sensors. Raise UpdateFailed in the coordinator on an empty forecast.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

### Chores

- Add devcontainer for tests, ignore local Claude settings
  ([`0123907`](https://github.com/principat/haeo_klima_forecast/commit/0123907b01c8a4a165fa9a1e30ebdd62035519f0))

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>


## v1.1.0 (2026-09-30)

### Features

- Read weather.* via get_forecasts, optional max power cap, drop radiation
  ([`87793da`](https://github.com/principat/haeo_klima_forecast/commit/87793da93faa6b9ea85d2754fe1cb9e6fc509cb8))

- Read hourly forecast of weather.* entities through weather.get_forecasts (no forecast attribute
  since HA 2024.3); template sensors still use the attribute path - Add optional max_power_kw hard
  cap; without it, cap the forecast at the highest trained power * 1.1 (max_observed_kw stored with
  the weights) - Remove shortwave radiation feature entirely - Exclude the large forecast attribute
  from the recorder (16 KB limit) - Raise a clear UpdateFailed for entries missing
  weather_forecast_entity - Update spec and README

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>


## v1.0.0 (2026-09-24)

### Features

- Add recalculate-weights button and weekly auto-recalculation switch
  ([`b5cd0c4`](https://github.com/principat/haeo_klima_forecast/commit/b5cd0c4c34538ff76d544814712edc4bbdbb1ffb))

The button retrains the weights of a climate system on demand. The switch schedules an internal
  weekly run (Sunday 03:30 local time) instead of creating a Home Assistant automation, and restores
  its state after restarts.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

- Rebuild on an energy-signature model, drop indoor-unit config
  ([`820a584`](https://github.com/principat/haeo_klima_forecast/commit/820a5843928d085f15d4b83f9b68c273f55f5984))

Replaces the per-indoor-unit setpoint/night-setback/duty-throttle model with a minimal-configuration
  energy-signature (degree-hours) regression:

- Three data sources per system instead of an indoor-unit list: a power sensor, a weather-forecast
  entity (provider-independent "forecast template" format), and one indoor-temperature source. Night
  setback and duty throttling are no longer modeled separately - their effect already shows up in
  the measured indoor temperature. - New HistoryStore (history_store.py): the integration's own
  hourly training-data cache, backfilled once and updated daily, replacing the old mirror-sensor
  workaround and the per-training-run weather refetch. - Historical weather now comes fixed from the
  Open-Meteo Historical Weather API; forecast weather is read from an already-existing HA entity
  instead of this integration calling a weather API itself (DWD/Bright Sky support moves to a
  separate mapper-helper project). - weighting.py/forecast.py rewritten around indoor-temp-based
  degree-hours, with optional radiation/wind/humidity/wind-direction features included only when the
  data actually covers them. - config_flow.py reduced to a single step; special_adjustments.py and
  mirror.py removed.

Adds SPECIFICATION.md, a from-scratch, framework-independent spec of the project (fachliche vs.
  technische Anforderungen) that documents the target design and the reasoning behind it.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Testing

- Add real end-to-end coverage via in-process recorder + config flow
  ([`d03c420`](https://github.com/principat/haeo_klima_forecast/commit/d03c420171a959a4e9971b3d5d51352f9691e7d6))

Drives the actual config-flow UI steps, a real (in-memory) recorder component with imported
  statistics, and the real recalculate_weights button to verify the full configure -> backfill ->
  train -> forecast journey, plus a Development section in the README documenting how to run and
  extend it.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>


## v0.2.0 (2026-09-08)

### Features

- Record climate/weather history via recorder-mirror sensors
  ([`48de3b9`](https://github.com/principat/haeo_klima_forecast/commit/48de3b90e050754001e3df57ffdc8022fdd92a56))

climate and weather entities never get HA long-term statistics (their state is not a numeric
  measurement), so their history was only kept for recorder.purge_keep_days - far short of the
  configured training_days. Adds internal sensor entities that mirror the relevant numeric values
  (setpoint, current temperature, active/hvac state, outdoor conditions) with state_class:
  measurement, so HA statistics keep them indefinitely. weighting.py now trains from these first,
  falling back to raw state history / the weather provider's archive API only for gaps.

Also adds a pytest-homeassistant-custom-component based test suite.


## v0.1.0 (2026-09-06)

### Features

- Inital public version
  ([`81a1956`](https://github.com/principat/haeo_klima_forecast/commit/81a1956ee142ccf8f7b524c5758342077776c857))
