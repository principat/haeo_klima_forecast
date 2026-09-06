"""Constants for the HAEO Klima Forecast integration."""
from __future__ import annotations

DOMAIN = "haeo_klima_forecast"
PLATFORMS = ["sensor"]

STORAGE_VERSION = 1
STORAGE_KEY_TEMPLATE = f"{DOMAIN}_weights_{{entry_id}}"

# ---- general configuration -----------------------------------------------------
CONF_NAME = "name"
CONF_ENERGY_SENSOR = "energy_sensor"          # kWh, monotonically increasing
CONF_POWER_SENSOR = "power_sensor"            # kW, instantaneous value (also usable for duty throttling)
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_WEATHER_PROVIDER = "weather_provider"
CONF_FORECAST_HOURS = "forecast_hours"
CONF_UPDATE_INTERVAL_MIN = "update_interval_minutes"
CONF_TRAINING_DAYS = "training_days"

WEATHER_PROVIDER_OPENMETEO = "openmeteo"
WEATHER_PROVIDER_DWD = "dwd"
WEATHER_PROVIDERS = [WEATHER_PROVIDER_OPENMETEO, WEATHER_PROVIDER_DWD]

DEFAULT_FORECAST_HOURS = 72
DEFAULT_UPDATE_INTERVAL_MIN = 30
DEFAULT_TRAINING_DAYS = 365

# ---- indoor units ----------------------------------------------------------------
CONF_INDOOR_UNITS = "indoor_units"            # list of dicts, see below
IU_NAME = "name"
IU_CLIMATE_ENTITY = "climate_entity"          # climate.* entity of the indoor unit
IU_SETPOINT_ENTITY = "setpoint_entity"        # optional: dedicated setpoint temperature entity (input_number),
                                               # in case the climate entity itself should not be used

# ---- night setback -----------------------------------------------------------------
CONF_NIGHT_SETBACK_ENABLED = "night_setback_enabled"
CONF_NIGHT_SETBACK_ACTIVE_ENTITY = "night_setback_active_entity"    # binary_sensor/input_boolean, set by an automation
CONF_NIGHT_SETBACK_OFFSET_ENTITY = "night_setback_offset_entity"    # number/input_number, magnitude of the setback/boost
CONF_NIGHT_SETBACK_START = "night_setback_start"                    # HH:MM, only used for the forecast projection
CONF_NIGHT_SETBACK_END = "night_setback_end"                        # HH:MM, only used for the forecast projection

# ---- duty throttling (load-dependent setback) ------------------------------------
CONF_DUTY_THROTTLE_ENABLED = "duty_throttle_enabled"
CONF_DUTY_THROTTLE_LOAD_ENTITY = "duty_throttle_load_entity"        # load sensor (e.g. power_sensor)
CONF_DUTY_THROTTLE_OFFSET_ENTITY = "duty_throttle_offset_entity"    # currently effective offset (number), maintained by existing logic
CONF_DUTY_THROTTLE_THRESHOLD = "duty_throttle_threshold"            # load threshold in W
CONF_DUTY_THROTTLE_MAX_OFFSET = "duty_throttle_max_offset"          # max. setback/boost in °C
CONF_DUTY_THROTTLE_STEP = "duty_throttle_step"                      # step size °C

HVAC_MODE_HEAT = "heat"
HVAC_MODE_COOL = "cool"

SERVICE_RECALCULATE_WEIGHTS = "recalculate_weights"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

SIGNAL_WEIGHTS_UPDATED = f"{DOMAIN}_weights_updated"
