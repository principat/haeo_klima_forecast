# HAEO Klima Forecast

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-%2341BDF5.svg?style=flat&logo=home-assistant&logoColor=white)](https://www.home-assistant.io)
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=principat&repository=haeo_klima_forecast&category=integration)

Custom HACS integration for Home Assistant: forecasts the power consumption
of an air conditioning system (heating and/or cooling) from a weather
forecast, using a simple "energy signature" (degree-hours) regression
learned from your own historical power and indoor-temperature data.
Intended as a consumption forecast source for HAEO or your own energy
optimization automations.

Full design rationale and the framework-independent specification (in
German) live in [SPECIFICATION.md](SPECIFICATION.md).

## How it works

The model is deliberately simple and needs almost no configuration (see
[SPECIFICATION.md, section 1](SPECIFICATION.md)):

- **Physical idea**: heat demand ≈ insulation value × (indoor − outdoor
  temperature). The regression learns the insulation value itself as a
  coefficient - you never configure it. Since the system can both heat and
  cool, the resulting curve is a "bathtub": flat baseline load in between,
  with two independently-sloped branches for heating and cooling.
- **Three data sources per climate system**: a historized power sensor, a
  weather-forecast entity, and one indoor-temperature source (a thermometer,
  or a climate entity's current-temperature attribute). No indoor-unit list,
  no night-setback/duty-throttle configuration - their effect already shows
  up in the measured indoor temperature.
- **Training** (`weighting.py`, via the `haeo_klima_forecast.recalculate_weights`
  service): a ridge-regularized linear regression over the last *N* days
  (default 365), reading exclusively from this integration's own
  `HistoryStore` (see below) - never live from the recorder or a weather API
  at training time.
- **Forecast** (`forecast.py` + `coordinator.py`): combines the stored
  weights with the weather-forecast entity's `forecast` attribute. For the
  indoor-temperature input, each forecast hour reuses the actual measured
  value from 24 hours earlier (cyclically, over multi-day horizons) - this
  captures a recurring night-setback pattern without modeling it explicitly.
  A load-reactive duty-throttle effect is not projected at all (no reliable
  basis to predict it); since both mechanisms only ever *reduce* consumption,
  omitting them can only make the forecast a little too high, never too low.
- **HistoryStore** (`history_store.py`): the integration's own hourly
  training-data cache, replacing the older "mirror sensor" approach. A
  one-off backfill on setup reads as much power/weather history as is
  available; a daily background job then appends only the newly elapsed
  hours. If the indoor-temperature source is a climate entity (no long-term
  statistics of its own in HA), an hourly sampler writes one value per hour
  directly into the store instead.
- **Historical weather**: fetched directly from the free, key-less
  [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api),
  bounded by the power sensor's own available history.
- **Forecast weather**: this integration does **not** talk to any weather
  service itself. It reads an existing HA entity that already exposes a
  forecast in a small, provider-independent shape:

  ```yaml
  state: 5.3   # current outdoor temperature (°C)
  attributes:
    forecast:
      - time: "2026-09-23T14:00:00+00:00"
        value: 6.1        # outdoor temperature (°C), required
        humidity: 72      # %, optional
        radiation: 210    # W/m², optional
        wind_speed: 3.4   # m/s, optional
        wind_direction: 180  # °, optional
      - ...
  ```

  Turning a concrete weather service (Open-Meteo, DWD, ...) into this shape
  is the job of a separate, provider-specific "mapper-helper" project - not
  part of this integration.

## Installation via HACS

1. Add this repository as a "Custom Repository" (category: Integration) in
   HACS.
2. Install "HAEO Klima Forecast" and restart Home Assistant.
3. Set up (or reuse) a forecast-template weather entity for your location,
   via a mapper-helper project for your preferred weather service.
4. Settings → Devices & Services → Add Integration → "HAEO Klima Forecast"
   → provide a name, the power sensor, the weather-forecast entity, and the
   indoor-temperature source.
5. Once enough history is available (recommended: at least 2-4 weeks,
   better 90 days), press the `button.<system>_recalculate_weights` button
   (or run the `haeo_klima_forecast.recalculate_weights` service).
6. Optionally turn on `switch.<system>_weekly_weight_recalculation` to
   recalculate the weights automatically every Sunday at 03:30 (local time).
   No automation needs to be created for this; the schedule runs inside the
   integration and the switch state survives restarts.

If you need a different schedule, leave the switch off and call the service
from your own automation instead:

```yaml
# Example automation: weekly retraining
alias: Recalculate climate forecast weights
trigger:
  - platform: time
    at: "03:30:00"
condition:
  - condition: time
    weekday: [sun]
action:
  - service: haeo_klima_forecast.recalculate_weights
```

## Entities

- `sensor.<system>_power_forecast`: current state = forecast for the next
  hour (kW), attribute `forecast` = complete hourly series (time, value,
  outdoor_temp, indoor_temp, heating/cooling_degree_hours).
- `sensor.<system>_weights`: diagnostic sensor, state = R² of the last
  regression, attributes contain the individual coefficients and which
  optional features (radiation/wind/humidity/wind direction) were included.
- `button.<system>_recalculate_weights`: recalculates the weights of this
  system immediately.
- `switch.<system>_weekly_weight_recalculation`: weekly automatic
  recalculation (Sunday 03:30) on/off.

## Known limitations / points to refine

- Only one aggregated indoor-temperature reading is used per system, not
  per room or per indoor unit - a deliberate trade-off for minimal
  configuration (see SPECIFICATION.md, section 1.9).
- The regression is piecewise-linear; heat-pump nonlinearities at extreme
  cold (COP drop, defrost cycles) are not modeled separately.
- Wind direction is a speculative, circularly-encoded feature (sin/cos) -
  only included in training if it actually has enough data coverage, and
  its real predictive value has not been validated yet.
- The recorder/statistics APIs (`statistics_during_period`,
  `state_changes_during_period`) are internal HA APIs and their
  signature/behavior can change slightly between HA versions.
- For multiple parallel climate systems, simply set up the integration
  multiple times via "Add Integration", each with its own name/sensors -
  every config entry is an independent system with its own coordinator, its
  own weights, its own `HistoryStore` and its own entities.

## Development

Everything below is for anyone working on this integration itself, not for
consumers of the HACS package.

Install the test dependencies and run the suite:

```bash
pip install -r requirements_test.txt
pytest
```

The suite (`tests/`) runs entirely in-process, with no separate Home
Assistant instance or Docker container required:

- Most tests use [`pytest-homeassistant-custom-component`](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component),
  which spins up a real (in-memory) `HomeAssistant` core object per test -
  real config entries, real entity/service registries, real coordinators -
  just without a UI or network I/O.
- `tests/test_e2e_full_flow.py` goes one step further and is a genuine
  end-to-end test: it drives the actual config-flow steps a user would
  click through, seeds a real (in-memory) `recorder` component with
  long-term statistics for the power/indoor-temperature sensors, presses
  the real `recalculate_weights` button entity, and asserts on the
  resulting weights/forecast sensor entities. The only thing stubbed out is
  the genuine external network boundary (the Open-Meteo historical weather
  API) - everything else is exercised through real HA components. Use this
  test as the template whenever a change should be verified against the
  full "configure → backfill → train → forecast" journey rather than a
  single module in isolation.
- `.devcontainer/` provides a ready-to-use dev container with the test
  dependencies preinstalled.

Keep new tests plausibility-based where the assertions concern the
regression's actual numeric output (R² range, sign/monotonicity of
coefficients, forecast ≥ 0) rather than pinned to exact values, so they
don't need rewriting every time the model itself is tuned - see the
existing tests for the pattern.
