"""Constants for the HAEO Klima Forecast integration."""
from __future__ import annotations

DOMAIN = "haeo_klima_forecast"
PLATFORMS = ["button", "sensor", "switch"]

STORAGE_VERSION = 1
STORAGE_KEY_TEMPLATE = f"{DOMAIN}_weights_{{entry_id}}"
HISTORY_STORAGE_KEY_TEMPLATE = f"{DOMAIN}_history_{{entry_id}}"

# ---- general configuration -----------------------------------------------------
CONF_NAME = "name"
CONF_POWER_SENSOR = "power_sensor"                    # kW, historized, target of the regression
CONF_WEATHER_FORECAST_ENTITY = "weather_forecast_entity"  # forecast-template entity, see weather/forecast_template.py
CONF_INDOOR_TEMP_SOURCE = "indoor_temp_source"        # sensor (thermometer) or climate entity
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_FORECAST_HOURS = "forecast_hours"
CONF_UPDATE_INTERVAL_MIN = "update_interval_minutes"
CONF_TRAINING_DAYS = "training_days"

DEFAULT_FORECAST_HOURS = 72
DEFAULT_UPDATE_INTERVAL_MIN = 30
DEFAULT_TRAINING_DAYS = 365

SERVICE_RECALCULATE_WEIGHTS = "recalculate_weights"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

# Weekly automatic retraining (switch entity), local time
AUTO_RECALCULATE_WEEKDAY = 6  # Sunday (datetime.weekday())
AUTO_RECALCULATE_HOUR = 3
AUTO_RECALCULATE_MINUTE = 30

# Daily HistoryStore backfill/increment job (internal, not user-facing), local time
HISTORY_SYNC_HOUR = 2
HISTORY_SYNC_MINUTE = 30

# Forecast-template attribute names (see weather/forecast_template.py)
FORECAST_ATTR_LIST = "forecast"
FORECAST_ATTR_TIME = "time"
FORECAST_ATTR_TEMPERATURE = "value"
FORECAST_ATTR_HUMIDITY = "humidity"
FORECAST_ATTR_RADIATION = "radiation"
FORECAST_ATTR_WIND_SPEED = "wind_speed"
FORECAST_ATTR_WIND_DIRECTION = "wind_direction"
