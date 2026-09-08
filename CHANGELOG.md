# CHANGELOG


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
