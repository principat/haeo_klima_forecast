# CHANGELOG


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
