# HAEO Klima Forecast

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-%2341BDF5.svg?style=flat&logo=home-assistant&logoColor=white)](https://www.home-assistant.io)
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=principat&repository=haeo_klima_forecast&category=integration)

Custom HACS integration for Home Assistant: forecasts the power consumption
of an air conditioning system (one outdoor unit, multiple indoor units,
heating + cooling) based on weather forecasts (Open-Meteo or DWD/Bright Sky)
as well as historical consumption and weather data. Intended as a
consumption forecast source for HAEO or your own energy optimization
automations.

## How it works

1. **Training** (`weighting.py`): via the `haeo_klima_forecast.recalculate_weights`
   service, a regression is computed over the last *N* days (default: 90 days).
   Input variables: historical outdoor temperature/radiation/wind (from the
   chosen weather service), historical setpoints & activity per indoor unit,
   and - if configured - the historical values of your night setback and
   duty throttling. Target variable: hourly consumption from your kWh meter
   (HA long-term statistics). Result: a set of weights (coefficients),
   persisted via `homeassistant.helpers.storage.Store`.
2. **Forecast** (`forecast.py`): a pure, simple function that combines the
   stored weights with the weather forecast data for the upcoming hours.
   Runs automatically every `update_interval_minutes` via the coordinator.
3. **Weather abstraction** (`weather/`): a `WeatherProvider` interface with
   implementations for Open-Meteo (`openmeteo.py`) and DWD via Bright Sky
   (`dwd.py`). Additional services can be added without touching the
   training/forecast logic.

## Handling the two special adjustments

- **Night setback**: time-based, and therefore well predictable for future
  hours. You configure the start/end time as well as the entity that
  supplies the offset amount; the active entity continues to be set/used by
  your own automation.
- **Duty throttling**: load-dependent/reactive, and therefore NOT reliably
  predictable for the future. The historical effect is included in
  training; for the forecast it is currently conservatively assumed to be a
  0 °C offset (`special_adjustments.projected_duty_throttle_offset`). If you
  want, you can use a configured empirical value there instead of 0.

## Installation via HACS

1. Add this repository as a "Custom Repository" (category: Integration) in
   HACS.
2. Install "HAEO Klima Forecast" and restart Home Assistant.
3. Settings → Devices & Services → Add Integration → "HAEO Klima Forecast"
   → follow the wizard (name, energy meter, power sensor, weather service,
   number of indoor units, the climate entity for each indoor unit).
4. Optionally, via "Configure" on the integration: set up night setback and
   duty throttling parameters, adjust indoor units later.
5. Once enough history is available (recommended: at least 2-4 weeks,
   better 90 days), run the `haeo_klima_forecast.recalculate_weights`
   service once (e.g. via Developer Tools → Services, or directly from an
   automation that repeats it e.g. weekly at night).

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
  hour (kW), attribute `forecast` = complete hourly series.
- `sensor.<system>_weights`: diagnostic sensor, state = R² of the last
  regression, attributes contain the individual coefficients.

## Known limitations / points to refine

- The recorder/statistics APIs (`statistics_during_period`,
  `state_changes_during_period`) are internal HA APIs and their
  signature/behavior can change slightly between HA versions. Please check
  the logs once after installation and adjust `weighting.py` to your HA
  version if necessary.
- For multiple parallel climate systems, simply set up the integration
  multiple times via "Add Integration", each with its own name/sensors -
  every config entry is an independent system with its own coordinator, its
  own weights and its own entities.
- Night setback is currently configured per system (not per indoor unit),
  since usually a shared time window applies to all indoor units. If you
  need different windows per indoor unit, this can be extended in
  `config_flow.py`/`const.py` (move the offset/window into the indoor unit
  configuration instead of the general one).
- The DWD integration uses Bright Sky as an open wrapper around DWD open
  data, since the DWD itself does not offer a simple lat/lon JSON API. If
  you prefer a different DWD source, only `weather/dwd.py` needs to be
  adapted.
- The "active indoor units" are read from the current `climate` state; for
  the forecast projection it is assumed that the current hvac_mode/setpoint
  remains constant over the horizon (no calendar/presence forecast of the
  setpoints themselves).
