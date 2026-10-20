import os
import json
import logging
import requests
from datetime import datetime, timezone
from database import get_db_connection, log_event

logger = logging.getLogger("asense_agent")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")

# Reference to the function that sends physical commands (injected by main.py)
send_command_fn = None

def register_command_executor(fn):
    global send_command_fn
    send_command_fn = fn

# ------------------------------------------------------------------------------
# 1. ALLOW-LISTED TOOL DEFINITIONS (Sent to Ollama)
# ------------------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_room_readings",
            "description": "Get current temperature and humidity in the room, sample time, and data freshness.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_relay_state",
            "description": "Get the current physical state (ON/OFF) and availability of a relay actuator.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "enum": ["relay-01", "relay-02"],
                        "description": "Identifier of the relay ('relay-01' for primary fan/appliance, 'relay-02' for auxiliary)."
                    }
                },
                "required": ["device_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_relay_state",
            "description": "Turn a relay ON or OFF. Use this when the user asks to control or switch an appliance or fan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "enum": ["relay-01", "relay-02"],
                        "description": "The relay device to switch."
                    },
                    "state": {
                        "type": "string",
                        "enum": ["ON", "OFF"],
                        "description": "The target state to switch to."
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief reason for switching the relay."
                    }
                },
                "required": ["device_id", "state"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_at_clock_time",
            "description": "Schedule a device to turn ON or OFF at a specific clock time (e.g. at 5:00 AM, 18:30, 7 PM).",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "enum": ["relay-01", "relay-02"],
                        "description": "Relay identifier ('relay-01' for fan)."
                    },
                    "state": {
                        "type": "string",
                        "enum": ["ON", "OFF"],
                        "description": "Target state to switch to at that time."
                    },
                    "time_str": {
                        "type": "string",
                        "description": "The target clock time, e.g. '05:00', '5am', '18:30', '6:00 pm'."
                    }
                },
                "required": ["device_id", "state", "time_str"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_timed_action",
            "description": "Schedule a device to turn ON or OFF after a specific number of seconds or minutes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "enum": ["relay-01", "relay-02"],
                        "description": "Relay identifier."
                    },
                    "state": {
                        "type": "string",
                        "enum": ["ON", "OFF"],
                        "description": "Target state."
                    },
                    "delay_seconds": {
                        "type": "number",
                        "description": "Delay in seconds before triggering (e.g. 60 for 1 minute)."
                    }
                },
                "required": ["device_id", "state", "delay_seconds"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_relay_cycle",
            "description": "Schedule a repeating on-and-off cycle for a relay (e.g. on and off every 2 seconds for 5 cycles).",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "enum": ["relay-01", "relay-02"],
                        "description": "Relay identifier."
                    },
                    "interval_seconds": {
                        "type": "number",
                        "description": "Interval between switches in seconds (minimum 2s)."
                    },
                    "cycles": {
                        "type": "integer",
                        "description": "Number of cycles to perform (maximum 10)."
                    }
                },
                "required": ["device_id", "interval_seconds", "cycles"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_temperature_rule",
            "description": "Set an automation rule based on user preference to trigger a device state when room temperature crosses a threshold. For example: 'turn on fan when temp is above 35C' or 'turn off fan when temp drops below 25C'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "enum": ["relay-01", "relay-02"],
                        "description": "Relay identifier to control ('relay-01' for fan/primary appliance)."
                    },
                    "condition": {
                        "type": "string",
                        "enum": ["above", "below"],
                        "description": "'above' if device should trigger when temp rises above threshold; 'below' if when it drops below."
                    },
                    "threshold": {
                        "type": "number",
                        "description": "Target temperature threshold in Celsius (e.g. 35.0 or 25.0)."
                    },
                    "target_state": {
                        "type": "string",
                        "enum": ["ON", "OFF"],
                        "description": "The state to apply ('ON' or 'OFF')."
                    }
                },
                "required": ["device_id", "condition", "threshold", "target_state"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "Schedule a reminder message to be sent to the user at a specific clock time or after a delay. E.g. 'remind me to purchase toilet paper at 9am'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The reminder content (e.g. 'Purchase toilet paper')."
                    },
                    "time_str": {
                        "type": "string",
                        "description": "The clock time to trigger the reminder, e.g. '9am', '09:00', '21:30', '9:00 pm'."
                    }
                },
                "required": ["message", "time_str"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_auto_mode",
            "description": "Enable or disable Autonomous Auto-Mode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "true to enable auto-mode, false to disable it."
                    }
                },
                "required": ["enabled"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather_forecast",
            "description": "Get current outdoor weather, predictions (max/min temp, rain probability), and proactive cooling recommendations for the user's city via Open-Meteo.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_weather_city",
            "description": "Set or change the user's city preference for weather forecasts (e.g. Pune, Mumbai, London, New York).",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "Name of the city or town to set."
                    }
                },
                "required": ["city"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_events",
            "description": "Get recent system events, warnings, or device actions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of recent events to retrieve (default 5, max 20)."
                    }
                },
                "required": []
            }
        }
    }
]

# ------------------------------------------------------------------------------
# 2. DETERMINISTIC TOOL EXECUTORS
# ------------------------------------------------------------------------------
def execute_get_room_readings():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM sensor_readings ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()

    if not row:
        return {"status": "error", "message": "No sensor readings available yet."}

    rx_time = row["received_at"]
    freshness_sec = None
    try:
        t = datetime.fromisoformat(rx_time)
        freshness_sec = round((datetime.now(timezone.utc) - t).total_seconds(), 1)
    except Exception:
        pass

    is_stale = (freshness_sec is None or freshness_sec > 60)

    return {
        "device_id": row["device_id"],
        "temperature_c": row["temperature_c"],
        "humidity_pct": row["humidity_pct"],
        "received_at": rx_time,
        "freshness_seconds_ago": freshness_sec,
        "is_stale": is_stale
    }

def execute_get_relay_state(device_id: str):
    if device_id not in ["relay-01", "relay-02"]:
        return {"status": "error", "message": f"Unknown relay {device_id}"}

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM device_state WHERE device_id = ?", (device_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return {"device_id": device_id, "state": "UNKNOWN", "online": False}

    return {
        "device_id": device_id,
        "state": row["reported_state"],
        "online": bool(row["online"]),
        "last_seen_at": row["last_seen_at"]
    }

def execute_set_relay_state(device_id: str, state: str, reason: str = ""):
    if device_id not in ["relay-01", "relay-02"]:
        return {"status": "error", "message": f"Unknown relay {device_id}"}
    if state not in ["ON", "OFF"]:
        return {"status": "error", "message": "State must be ON or OFF"}

    if not send_command_fn:
        return {"status": "error", "message": "Device command executor not attached."}

    try:
        result = send_command_fn(device_id, state)
        log_event("AGENT_ACTION", "INFO", f"Agent switched {device_id} to {state}", 
                  {"reason": reason, "result": result})
        return {
            "status": "success",
            "device_id": device_id,
            "confirmed_state": result.get("confirmed_state", state),
            "reason": reason
        }
    except Exception as e:
        logger.error(f"Agent failed to execute set_relay_state: {e}")
        return {"status": "failed", "error": str(e)}

def execute_get_recent_events(limit: int = 5):
    limit = min(max(1, limit), 20)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT occurred_at, event_type, summary FROM events ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"events": rows}

import heartbeat
import re

def dispatch_tool(tool_name: str, arguments: dict):
    logger.info(f"Dispatching tool '{tool_name}' with args {arguments}")
    if tool_name == "get_room_readings":
        return execute_get_room_readings()
    elif tool_name == "get_relay_state":
        return execute_get_relay_state(arguments.get("device_id", "relay-01"))
    elif tool_name == "set_relay_state":
        return execute_set_relay_state(
            arguments.get("device_id", "relay-01"),
            arguments.get("state", "OFF"),
            arguments.get("reason", "")
        )
    elif tool_name == "schedule_at_clock_time":
        tid = heartbeat.schedule_at_clock_time(
            arguments.get("device_id", "relay-01"),
            arguments.get("state", "OFF"),
            arguments.get("time_str", "05:00")
        )
        return {"status": "scheduled", "task_id": tid, "time": arguments.get("time_str")}
    elif tool_name == "schedule_timed_action":
        tid = heartbeat.schedule_timed_action(
            arguments.get("device_id", "relay-01"),
            arguments.get("state", "OFF"),
            float(arguments.get("delay_seconds", 10))
        )
        return {"status": "scheduled", "task_id": tid}
    elif tool_name == "schedule_relay_cycle":
        tid = heartbeat.schedule_relay_cycle(
            arguments.get("device_id", "relay-01"),
            float(arguments.get("interval_seconds", 2)),
            int(arguments.get("cycles", 3))
        )
        return {"status": "scheduled", "task_id": tid, "message": "Cycle loop is running safely in the background."}
    elif tool_name == "set_temperature_rule":
        rule = heartbeat.set_temperature_rule(
            arguments.get("device_id", "relay-01"),
            arguments.get("condition", "above"),
            float(arguments.get("threshold", 32.0)),
            arguments.get("target_state", "ON"),
            True
        )
        return {"status": "success", "rule": rule}
    elif tool_name == "set_reminder":
        tid = heartbeat.schedule_reminder_at_clock_time(
            arguments.get("message", "Reminder"),
            arguments.get("time_str", "09:00")
        )
        return {"status": "scheduled", "task_id": tid, "message": arguments.get("message"), "time": arguments.get("time_str")}
    elif tool_name == "set_auto_mode":
        is_en = bool(arguments.get("enabled", False))
        res_mode = heartbeat.set_auto_mode(is_en)
        return {"status": "success", "auto_mode": res_mode}
    elif tool_name == "get_weather_forecast":
        import weather_service
        return weather_service.get_weather_forecast()
    elif tool_name == "set_weather_city":
        import weather_service
        city = arguments.get("city", "Pune")
        return weather_service.set_location(city)
    elif tool_name == "get_recent_events":
        return execute_get_recent_events(arguments.get("limit", 5))
    else:
        return {"status": "error", "message": f"Tool '{tool_name}' is not recognized."}

# ------------------------------------------------------------------------------
# 3. CONVERSATION MEMORY & AGENT CHAT LOOP
# ------------------------------------------------------------------------------
conversation_history = []

def get_system_prompt():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M (%I:%M %p)")
    return f"""You are A-SENSE, an intelligent smart home assistant running locally on a Raspberry Pi.
You have direct tool access to control physical appliances and set smart automation rules:
- "relay-01": Primary fan / appliance.
- "relay-02": Auxiliary appliance.

CURRENT TIME: {now_str}

CRITICAL RULES:
1. When the user asks to schedule an action at a specific time (e.g. "turn off fan at 5am", "turn on relay 2 at 18:30"):
   -> Call `schedule_at_clock_time(device_id="relay-01", state="OFF", time_str="05:00")`.
2. When the user asks for reminders (e.g. "remind me to purchase toilet paper at 9am"):
   -> Call `set_reminder(message="purchase toilet paper", time_str="09:00")`.
3. When the user asks to enable or disable auto mode ("turn on auto mode", "disable auto mode"):
   -> Call `set_auto_mode(enabled=True/False)`.
4. When the user gives a temperature-based rule (e.g. "turn on fan when it is 35C"):
   -> Call `set_temperature_rule`.
5. When the user asks for immediate control ("turn on relay 1", "turn off all"):
   -> Call `set_relay_state`.
6. When the user asks for cycles ("toggle every 2s for 5 cycles"):
   -> Call `schedule_relay_cycle`.
7. When the user asks about outdoor weather, forecasts, or predictions:
   -> Call `get_weather_forecast`.
8. When the user asks to change or set their city/location (e.g. "set city to Mumbai", "my location is Pune"):
   -> Call `set_weather_city(city="...")`.
9. When the user asks about indoor room conditions:
   -> Call `get_room_readings`.

RULE: Follow user intent strictly. If user asks to turn OFF, state must be OFF! Keep responses concise (1-2 sentences).
"""

def chat_with_agent(user_message: str):
    global conversation_history

    msg_lower = user_message.lower().strip()

    # 0. Auto-Mode Control & Chat Guard Intent:
    # If user wants to enable/disable auto mode:
    if "auto mode" in msg_lower or "automode" in msg_lower or "auto-mode" in msg_lower:
        if any(w in msg_lower for w in ["disable", "turn off", "deactivate", "stop"]):
            heartbeat.set_auto_mode(False)
            reply_text = "Autonomous Auto-Mode is now **DISABLED** (Semi-Autonomous). Chat is unlocked and you can issue commands freely."
            conversation_history.append({"role": "user", "content": user_message})
            conversation_history.append({"role": "assistant", "content": reply_text})
            return {"reply": reply_text, "actions": [{"tool": "set_auto_mode", "arguments": {"enabled": False}, "result": {"auto_mode": False}}]}
        elif any(w in msg_lower for w in ["enable", "turn on", "activate", "start"]):
            heartbeat.set_auto_mode(True)
            reply_text = "Autonomous Auto-Mode is now **ENABLED**. The system will autonomously manage cooling and safety. If you need to chat or send manual commands, please disable auto-mode first."
            conversation_history.append({"role": "user", "content": user_message})
            conversation_history.append({"role": "assistant", "content": reply_text})
            return {"reply": reply_text, "actions": [{"tool": "set_auto_mode", "arguments": {"enabled": True}, "result": {"auto_mode": True}}]}

    # Guard: If Auto Mode is ACTIVE, prevent chatting and ask user to disable it first!
    if heartbeat.get_auto_mode():
        if any(w in msg_lower for w in ["disable", "turn off", "deactivate", "stop auto"]):
            heartbeat.set_auto_mode(False)
            reply_text = "Autonomous Auto-Mode has been **DISABLED**. Chat is now available."
            conversation_history.append({"role": "user", "content": user_message})
            conversation_history.append({"role": "assistant", "content": reply_text})
            return {"reply": reply_text, "actions": [{"tool": "set_auto_mode", "arguments": {"enabled": False}, "result": {"auto_mode": False}}]}
        else:
            guard_reply = "⚠️ **Auto-Mode is currently ACTIVE.** Direct chatting and manual commands are locked while the system operates autonomously. To chat or issue commands, please disable auto-mode first (e.g. say *'disable auto mode'* or toggle it off)."
            return {"reply": guard_reply, "actions": []}

    # 0.1 Reminder Intent (e.g. "msg me to purchase toilet paper at 9 am", "remind me to buy milk at 5pm"):
    if any(k in msg_lower for k in ["remind", "remeder", "remineder", "reminder", "msg me to", "message me to", "notify me to"]):
        time_match = re.search(r"(?:at|by)\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", msg_lower)
        clock_str = time_match.group(1).strip() if time_match else "09:00"
        
        # Clean reminder text
        content = user_message
        for prefix in ["msg me to", "message me to", "notify me to", "remind me to", "remind me", "remeder to", "reminder to"]:
            idx = content.lower().find(prefix)
            if idx != -1:
                content = content[idx + len(prefix):].strip()
                break
        
        # Remove trailing time part from reminder message
        content = re.sub(r"(?i)\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?.*$", "", content).strip()
        if not content:
            content = "Reminder"

        tid = heartbeat.schedule_reminder_at_clock_time(content, clock_str)
        reply_text = f"Got it! I will send you a reminder to *'{content}'* at {clock_str}."
        actions_taken = [{"tool": "set_reminder", "arguments": {"message": content, "time_str": clock_str}, "result": {"task_id": tid}}]
        conversation_history.append({"role": "user", "content": user_message})
        conversation_history.append({"role": "assistant", "content": reply_text})
        return {"reply": reply_text, "actions": actions_taken}

    # 0.2 Weather City Configuration Intent (e.g. "set city to Mumbai", "my city is Pune", "change location to Delhi")
    if any(k in msg_lower for k in ["set city", "change city", "set location", "change location", "my city is"]):
        city_match = re.search(r"(?:set city to|change city to|set location to|change location to|my city is)\s+([a-zA-Z\s]+)", msg_lower)
        if city_match:
            new_city = city_match.group(1).strip()
            import weather_service
            res = weather_service.set_location(new_city)
            if res.get("status") == "success":
                reply_text = f"📍 Location updated to **{res.get('city')}** ({res.get('region', '')}, {res.get('country', '')}). Weather forecasts will now use this city."
            else:
                reply_text = f"Could not locate '{new_city}'. Please check the city name."
            actions_taken = [{"tool": "set_weather_city", "arguments": {"city": new_city}, "result": res}]
            conversation_history.append({"role": "user", "content": user_message})
            conversation_history.append({"role": "assistant", "content": reply_text})
            return {"reply": reply_text, "actions": actions_taken}

    # 0.3 Weather Forecast & Prediction Query Intent (e.g. "what is the weather", "weather forecast", "will it rain today"):
    if any(k in msg_lower for k in ["weather", "forecast", "prediction", "rain today", "will it rain", "outside temp"]):
        import weather_service
        w = weather_service.get_weather_forecast()
        if w.get("status") == "success":
            curr = w.get("current", {})
            pred = w.get("today_prediction", {})
            recs = w.get("recommendations", [])

            recs_text = ""
            if recs:
                recs_text = "\n\n💡 **Smart Recommendations:**\n" + "\n".join([f"- {r}" for r in recs])

            reply_text = (
                f"🌤 **Weather in {w.get('city')}:** {curr.get('condition')}\n"
                f"- Outside Temp: **{curr.get('temperature_c')}°C** (Feels like {curr.get('apparent_temperature_c')}°C)\n"
                f"- Humidity: **{curr.get('humidity_pct')}%**\n"
                f"- Today's Forecast: High of **{pred.get('max_temp_c')}°C** | Low of **{pred.get('min_temp_c')}°C**\n"
                f"- Rain Probability: **{pred.get('rain_probability_pct')}%**"
                f"{recs_text}"
            )
        else:
            reply_text = f"Could not retrieve weather data: {w.get('message')}"

        actions_taken = [{"tool": "get_weather_forecast", "arguments": {}, "result": w}]
        conversation_history.append({"role": "user", "content": user_message})
        conversation_history.append({"role": "assistant", "content": reply_text})
        return {"reply": reply_text, "actions": actions_taken}

    # Dynamic Temperature Automation Rule Parser (User preference):
    # e.g.: "turn on fan when it is 35c", "turn off fan when it is 25c", "keep fan on till it come below 30"
    if ("when" in msg_lower or "till" in msg_lower or "until" in msg_lower or "if" in msg_lower or "reach" in msg_lower or "drop" in msg_lower) and any(w in msg_lower for w in ["temp", "c", "degree", "fan", "relay"]):
        # Extract threshold number
        temp_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:c|degree|°)?", msg_lower)
        if temp_match:
            threshold_val = float(temp_match.group(1))
            
            # Determine target state (ON or OFF)
            target_st = "OFF" if ("off" in msg_lower or "stop" in msg_lower) else "ON"
            
            # Determine condition (above or below)
            if any(k in msg_lower for k in ["below", "drops", "less", "under", "till", "until"]):
                cond = "below"
            else:
                cond = "above" if target_st == "ON" else "below"

            dev_id = "relay-02" if ("relay 2" in msg_lower or "relay-02" in msg_lower) else "relay-01"

            logger.info(f"Setting dynamic user rule: {dev_id} -> {target_st} when temp {cond} {threshold_val}°C")
            rule = heartbeat.set_temperature_rule(dev_id, cond, threshold_val, target_st, True)
            
            # If user said "keep fan on until below X", turn fan ON immediately
            extra_action = None
            if ("keep" in msg_lower or "till" in msg_lower or "until" in msg_lower) and cond == "below":
                dispatch_tool("set_relay_state", {"device_id": dev_id, "state": "ON", "reason": "Pre-activation for threshold cooling"})
                extra_action = {"tool": "set_relay_state", "arguments": {"device_id": dev_id, "state": "ON"}, "result": {"status": "success"}}

            actions_taken = [{"tool": "set_temperature_rule", "arguments": {"device_id": dev_id, "condition": cond, "threshold": threshold_val, "target_state": target_st}, "result": rule}]
            if extra_action:
                actions_taken.append(extra_action)

            reply_text = f"Preference saved! I have configured an automation rule: {dev_id} will switch {target_st} whenever room temperature is {cond} {threshold_val}°C."
            conversation_history.append({"role": "user", "content": user_message})
            conversation_history.append({"role": "assistant", "content": reply_text})
            return {"reply": reply_text, "actions": actions_taken}

    # 1. Proactive Comfort & Temperature Intent
    if any(kw in msg_lower for kw in ["hot", "warm", "sweat", "cool down", "cooling", "stuffy"]):
        logger.info("Comfort intent detected: User is hot -> Turning ON fan (relay-01)")
        r = dispatch_tool("set_relay_state", {"device_id": "relay-01", "state": "ON", "reason": "User requested cooling"})
        actions_taken = [{"tool": "set_relay_state", "arguments": {"device_id": "relay-01", "state": "ON"}, "result": r}]
        reply_text = "I noticed you're feeling warm. I have turned ON the fan (relay-01) to help cool down the room."
        conversation_history.append({"role": "user", "content": user_message})
        conversation_history.append({"role": "assistant", "content": reply_text})
        return {"reply": reply_text, "actions": actions_taken}

    elif any(kw in msg_lower for kw in ["cold", "chilly", "freezing"]):
        logger.info("Comfort intent detected: User is cold -> Turning OFF fan (relay-01)")
        r = dispatch_tool("set_relay_state", {"device_id": "relay-01", "state": "OFF", "reason": "User is cold"})
        actions_taken = [{"tool": "set_relay_state", "arguments": {"device_id": "relay-01", "state": "OFF"}, "result": r}]
        reply_text = "I have turned OFF the fan (relay-01) so you stay comfortable."
        conversation_history.append({"role": "user", "content": user_message})
        conversation_history.append({"role": "assistant", "content": reply_text})
        return {"reply": reply_text, "actions": actions_taken}

    # 2. Cycle / Loop Status vs Schedule:
    # If user is asking if cycle is running or status:
    if ("cycle" in msg_lower or "automation" in msg_lower or "task" in msg_lower) and any(q in msg_lower for q in ["is ", "check", "status", "running", "working"]):
        tasks = heartbeat.get_active_tasks()
        if not tasks:
            reply_text = "There are currently no active cycle or timer tasks running."
        else:
            task_descriptions = [f"{t['task_id']} ({t['description']}, next switch in {t['seconds_until_next_run']}s)" for t in tasks]
            reply_text = f"Yes, active tasks: {'; '.join(task_descriptions)}."
        conversation_history.append({"role": "user", "content": user_message})
        conversation_history.append({"role": "assistant", "content": reply_text})
        return {"reply": reply_text, "actions": []}

    # If user is scheduling a cycle (e.g. "turn on fan for 5 sec then off for 5 sec repeat 2 times"):
    if any(kw in msg_lower for kw in ["cycle", "repeat", "toggle"]) or ("then off" in msg_lower and "for" in msg_lower):
        # Extract seconds (default: 2)
        sec_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:sec|second)", msg_lower)
        interval_sec = float(sec_match.group(1)) if sec_match else 2.0

        # Extract cycle count (default: 3, max: 10)
        cyc_match = re.search(r"(\d+)\s*(?:cycle|time)", msg_lower)
        cycles = int(cyc_match.group(1)) if cyc_match else 2

        target_dev = "relay-02" if ("relay 2" in msg_lower or "relay-02" in msg_lower) else "relay-01"

        tid = heartbeat.schedule_relay_cycle(target_dev, interval_sec, cycles)
        reply_text = f"I have scheduled a background cycle task ({tid}): {target_dev} will toggle every {interval_sec} seconds for {cycles} cycles."
        actions_taken = [{"tool": "schedule_relay_cycle", "arguments": {"device_id": target_dev, "interval_seconds": interval_sec, "cycles": cycles}, "result": {"task_id": tid}}]
        conversation_history.append({"role": "user", "content": user_message})
        conversation_history.append({"role": "assistant", "content": reply_text})
        return {"reply": reply_text, "actions": actions_taken}

    # 3. Clock Time Schedule Intent Handling (e.g. "turn off fan at 5am so I will wakeup", "turn on relay 1 at 18:30")
    if " at " in msg_lower or msg_lower.startswith("at "):
        time_match = re.search(r"at\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", msg_lower)
        if time_match:
            clock_str = time_match.group(1).strip()
            target_st = "OFF" if any(w in msg_lower for w in ["off", "stop", "shut"]) else "ON"
            target_dev = "relay-02" if ("relay 2" in msg_lower or "relay-02" in msg_lower) else "relay-01"

            tid = heartbeat.schedule_at_clock_time(target_dev, target_st, clock_str)
            reply_text = f"Scheduled! I will turn {target_st} {target_dev} at {clock_str}."
            actions_taken = [{"tool": "schedule_at_clock_time", "arguments": {"device_id": target_dev, "state": target_st, "time_str": clock_str}, "result": {"task_id": tid}}]
            conversation_history.append({"role": "user", "content": user_message})
            conversation_history.append({"role": "assistant", "content": reply_text})
            return {"reply": reply_text, "actions": actions_taken}

    # 4. Delayed Relative Action Intent Handling (e.g. "turn off relay 1 after 30 seconds")
    if "after" in msg_lower or "in " in msg_lower:
        delay_match = re.search(r"after\s*(\d+)\s*(sec|second|min|minute)", msg_lower)
        if delay_match:
            val = int(delay_match.group(1))
            unit = delay_match.group(2)
            delay_sec = val * 60 if "min" in unit else val

            target_st = "OFF" if "off" in msg_lower else "ON"
            target_dev = "relay-02" if ("relay 2" in msg_lower or "relay-02" in msg_lower) else "relay-01"

            tid = heartbeat.schedule_timed_action(target_dev, target_st, delay_sec)
            reply_text = f"Scheduled! I will turn {target_dev} {target_st} in {delay_sec} seconds."
            actions_taken = [{"tool": "schedule_timed_action", "arguments": {"device_id": target_dev, "state": target_st, "delay_seconds": delay_sec}, "result": {"task_id": tid}}]
            conversation_history.append({"role": "user", "content": user_message})
            conversation_history.append({"role": "assistant", "content": reply_text})
            return {"reply": reply_text, "actions": actions_taken}

    # Determine desired action: ON or OFF
    target_state = None
    if any(kw in msg_lower for kw in ["turn off", "switch off", "power off", "shut down", "turn it off", "switch it off", "off it", "kill"]):
        target_state = "OFF"
    elif any(kw in msg_lower for kw in ["turn on", "switch on", "power on", "turn it on", "switch it on", "on it", "start"]):
        target_state = "ON"

    if target_state:
        # Check if targeting ALL devices
        if any(kw in msg_lower for kw in ["all", "everything", "both"]):
            logger.info(f"Fast-path detected: switching ALL relays to {target_state}")
            r1 = dispatch_tool("set_relay_state", {"device_id": "relay-01", "state": target_state, "reason": "All devices command"})
            r2 = dispatch_tool("set_relay_state", {"device_id": "relay-02", "state": target_state, "reason": "All devices command"})
            actions_taken = [
                {"tool": "set_relay_state", "arguments": {"device_id": "relay-01", "state": target_state}, "result": r1},
                {"tool": "set_relay_state", "arguments": {"device_id": "relay-02", "state": target_state}, "result": r2}
            ]
            
            # Check if user ALSO asked for temperature or conditions in the same sentence:
            temp_addon = ""
            if any(tk in msg_lower for tk in ["temp", "humidity", "reading", "condition", "room", "weather"]):
                readings = dispatch_tool("get_room_readings", {})
                actions_taken.append({"tool": "get_room_readings", "arguments": {}, "result": readings})
                if readings.get("status") != "error":
                    temp_addon = f" The current room temperature is {readings['temperature_c']}°C with {readings['humidity_pct']}% humidity."

            reply_text = f"Done! I have turned {target_state} all appliances (relay-01 and relay-02).{temp_addon}"
            conversation_history.append({"role": "user", "content": user_message})
            conversation_history.append({"role": "assistant", "content": reply_text})
            return {"reply": reply_text, "actions": actions_taken}

        # Check specific device
        target_device = None
        if "relay 2" in msg_lower or "relay-02" in msg_lower or "auxiliary" in msg_lower:
            target_device = "relay-02"
        elif "relay 1" in msg_lower or "relay-01" in msg_lower or "fan" in msg_lower or "appliance" in msg_lower:
            target_device = "relay-01"
        elif "it" in msg_lower or msg_lower.endswith("on") or msg_lower.endswith("off"):
            # Check conversation context for last mentioned relay
            target_device = "relay-01" # Default primary appliance

        if target_device:
            logger.info(f"Fast-path detected: switching {target_device} to {target_state}")
            result = dispatch_tool("set_relay_state", {"device_id": target_device, "state": target_state, "reason": "Direct user request"})
            actions_taken = [{"tool": "set_relay_state", "arguments": {"device_id": target_device, "state": target_state}, "result": result}]
            
            reply_text = f"Done! I have turned {target_device} {target_state}."
            conversation_history.append({"role": "user", "content": user_message})
            conversation_history.append({"role": "assistant", "content": reply_text})
            return {"reply": reply_text, "actions": actions_taken}

    # Otherwise, proceed to Ollama LLM Reasoning & Tool Calling
    conversation_history.append({"role": "user", "content": user_message})
    if len(conversation_history) > 6:
        conversation_history = conversation_history[-6:]

    messages = [{"role": "system", "content": get_system_prompt()}] + list(conversation_history)
    actions_taken = []

    # Bounded agent loop (max 3 tool cycles)
    for _ in range(3):
        payload = {
            "model": MODEL_NAME,
            "messages": messages,
            "tools": TOOLS,
            "stream": False,
            "keep_alive": "15m",
            "think": False,
            "options": {
                "num_ctx": 1024,      # Smaller context is much faster on Pi CPU
                "num_predict": 128,    # Keep output short to minimize CPU generation time
                "num_thread": 4,       # Use all 4 Cortex-A76 cores on Pi 5
                "temperature": 0.1
            }
        }

        try:
            res = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=60)
            res.raise_for_status()
            res_data = res.json()
        except Exception as e:
            logger.error(f"Error calling Ollama API: {e}")
            return {
                "reply": f"Sorry, error communicating with local model: {e}",
                "actions": actions_taken
            }

        response_msg = res_data.get("message", {})
        tool_calls = response_msg.get("tool_calls", [])

        if not tool_calls:
            # Model responded directly with text
            reply_text = response_msg.get("content", "").strip()
            conversation_history.append({"role": "assistant", "content": reply_text})
            return {
                "reply": reply_text,
                "actions": actions_taken
            }

        # Model requested one or more tool calls
        messages.append(response_msg)

        last_tool_result = None
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}

            last_tool_result = dispatch_tool(name, args)
            actions_taken.append({"tool": name, "arguments": args, "result": last_tool_result})

            # Append tool result to messages for Ollama to formulate final answer
            messages.append({
                "role": "tool",
                "name": name,
                "content": json.dumps(last_tool_result)
            })

        # Ask Ollama to synthesize the final answer WITHOUT tools to prevent infinite tool-call looping!
        try:
            summary_payload = {
                "model": MODEL_NAME,
                "messages": messages,
                "stream": False,
                "keep_alive": "15m",
                "think": False,
                "options": {
                    "num_ctx": 1024,
                    "num_predict": 96,
                    "num_thread": 4,
                    "temperature": 0.2
                }
            }
            s_res = requests.post(f"{OLLAMA_HOST}/api/chat", json=summary_payload, timeout=30)
            if s_res.status_code == 200:
                final_text = s_res.json().get("message", {}).get("content", "").strip()
                if final_text:
                    conversation_history.append({"role": "assistant", "content": final_text})
                    return {"reply": final_text, "actions": actions_taken}
        except Exception as se:
            logger.warning(f"Error getting final summary: {se}")

        # Reliable Fallback if model struggled to summarize
        fallback_reply = "Action completed."
        if last_tool_result and "temperature_c" in last_tool_result:
            fallback_reply = f"Current room temperature is {last_tool_result['temperature_c']}°C with {last_tool_result.get('humidity_pct', '')}% humidity."
        elif last_tool_result and "confirmed_state" in last_tool_result:
            fallback_reply = f"Device {last_tool_result.get('device_id')} is now {last_tool_result['confirmed_state']}."

        conversation_history.append({"role": "assistant", "content": fallback_reply})
        return {"reply": fallback_reply, "actions": actions_taken}

    return {
        "reply": "Request processed successfully.",
        "actions": actions_taken
    }
