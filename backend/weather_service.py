import logging
import requests

logger = logging.getLogger("asense_weather")

# Default location (can be updated dynamically from chat or settings)
current_location = {
    "city": "Pune",
    "latitude": 18.5204,
    "longitude": 73.8567
}

def set_location(city_name: str):
    """Geocode city name using Open-Meteo free geocoding API and store coordinates."""
    global current_location
    try:
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_name}&count=1&language=en&format=json"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            results = data.get("results")
            if results and len(results) > 0:
                loc = results[0]
                current_location["city"] = loc.get("name", city_name)
                current_location["latitude"] = loc.get("latitude")
                current_location["longitude"] = loc.get("longitude")
                country = loc.get("country", "")
                admin1 = loc.get("admin1", "")
                logger.info(f"Weather location updated to: {current_location['city']}, {admin1}, {country} ({current_location['latitude']}, {current_location['longitude']})")
                return {
                    "status": "success",
                    "city": current_location["city"],
                    "region": admin1,
                    "country": country,
                    "latitude": current_location["latitude"],
                    "longitude": current_location["longitude"]
                }
            else:
                return {"status": "error", "message": f"Could not find coordinates for city '{city_name}'."}
        else:
            return {"status": "error", "message": f"Geocoding API failed with status {res.status_code}"}
    except Exception as e:
        logger.error(f"Error updating weather location: {e}")
        return {"status": "error", "message": str(e)}

def get_current_location():
    return dict(current_location)

def get_weather_forecast():
    """Fetch current conditions + today's forecast & predictions from Open-Meteo."""
    lat = current_location["latitude"]
    lon = current_location["longitude"]
    city = current_location["city"]

    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}&"
            f"current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m&"
            f"hourly=temperature_2m,precipitation_probability,relative_humidity_2m&"
            f"daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max&"
            f"timezone=auto&forecast_days=2"
        )
        res = requests.get(url, timeout=6)
        if res.status_code != 200:
            return {"status": "error", "message": f"Open-Meteo returned status {res.status_code}"}

        data = res.json()
        current = data.get("current", {})
        daily = data.get("daily", {})
        hourly = data.get("hourly", {})

        current_temp = current.get("temperature_2m")
        current_humidity = current.get("relative_humidity_2m")
        apparent_temp = current.get("apparent_temperature")
        weather_code = current.get("weather_code", 0)

        # Weather description based on WMO codes
        condition = "Clear sky"
        if weather_code in [1, 2, 3]:
            condition = "Mainly clear / Partly cloudy"
        elif weather_code in [45, 48]:
            condition = "Foggy"
        elif weather_code in [51, 53, 55, 61, 63, 65, 80, 81, 82]:
            condition = "Rain / Showers"
        elif weather_code in [71, 73, 75]:
            condition = "Snow"
        elif weather_code in [95, 96, 99]:
            condition = "Thunderstorm"

        max_temp_today = daily.get("temperature_2m_max", [None])[0]
        min_temp_today = daily.get("temperature_2m_min", [None])[0]
        rain_prob_today = daily.get("precipitation_probability_max", [None])[0]

        # Formulate smart home recommendations based on predictions
        recommendations = []
        if max_temp_today and max_temp_today >= 34.0:
            recommendations.append(f"High outdoor heat predicted today (peak {max_temp_today}°C). Consider pre-cooling room or keeping fan ready.")
        if rain_prob_today and rain_prob_today >= 50:
            recommendations.append(f"High chance of rain ({rain_prob_today}%). Outdoor humidity will rise; monitor indoor stuffiness.")
        if current_temp and current_temp <= 20.0:
            recommendations.append(f"Cool weather outside ({current_temp}°C). Fan cooling can likely remain OFF.")

        return {
            "status": "success",
            "city": city,
            "current": {
                "temperature_c": current_temp,
                "apparent_temperature_c": apparent_temp,
                "humidity_pct": current_humidity,
                "condition": condition
            },
            "today_prediction": {
                "max_temp_c": max_temp_today,
                "min_temp_c": min_temp_today,
                "rain_probability_pct": rain_prob_today
            },
            "recommendations": recommendations
        }
    except Exception as e:
        logger.error(f"Error fetching weather forecast: {e}")
        return {"status": "error", "message": str(e)}
