import os
import json
import time
import threading
import logging
from datetime import datetime, timezone
from database import get_db_connection, log_event

logger = logging.getLogger("asense_heartbeat")

# Direct executor function
def trigger_physical_switch(device_id: str, state: str):
    try:
        import agent
        if agent.send_command_fn:
            res = agent.send_command_fn(device_id, state)
            logger.info(f"[PHYSICAL RELAY SWITCH] {device_id} switched to {state} -> Confirmed: {res.get('confirmed_state')}")
            return res
        else:
            logger.error("[PHYSICAL RELAY ERROR] agent.send_command_fn is None!")
    except Exception as e:
        logger.error(f"[PHYSICAL RELAY ERROR] Failed to switch {device_id} to {state}: {e}")

command_executor_fn = trigger_physical_switch

def register_command_executor(fn):
    pass

# Active in-memory scheduled cron/interval tasks:
active_tasks = {}
tasks_lock = threading.Lock()
running = False

# Auto Mode Setting (True = automatically act; False = ask first via Telegram/Dashboard)
auto_mode_enabled = False

def set_auto_mode(enabled: bool):
    global auto_mode_enabled
    auto_mode_enabled = enabled
    logger.info(f"Auto Mode is now: {'ENABLED (Autonomous)' if enabled else 'DISABLED (Semi-Autonomous)'}")
    return auto_mode_enabled

def get_auto_mode():
    return auto_mode_enabled

# Generalized Temperature Automation Rule
# e.g.:
# {"enabled": True, "condition": "above" | "below", "threshold": 35.0, "device_id": "relay-01", "target_state": "ON" | "OFF"}
temp_automation = {
    "enabled": False,
    "condition": "above",     # "above" or "below"
    "threshold": 32.0,
    "device_id": "relay-01",
    "target_state": "ON"      # What state to apply when condition is met
}

def set_temperature_rule(device_id: str, condition: str, threshold: float, target_state: str, enabled: bool = True):
    """
    Generalized rule: 'turn ON relay-01 when temp is above 35C'
    or 'turn OFF relay-01 when temp is below 25C'
    """
    global temp_automation
    with tasks_lock:
        temp_automation = {
            "enabled": enabled,
            "condition": condition.lower(), # "above" or "below"
            "threshold": float(threshold),
            "device_id": device_id,
            "target_state": target_state.upper()
        }
    logger.info(f"Heartbeat rule set: {device_id} -> {target_state} when temp is {condition} {threshold}°C")
    log_event("AUTOMATION_CONFIG", "INFO", f"Rule set: {device_id} -> {target_state} when temp {condition} {threshold}°C", temp_automation)
    return temp_automation

def get_temperature_automation():
    with tasks_lock:
        return dict(temp_automation)

def schedule_timed_action(device_id: str, target_state: str, delay_seconds: float, description: str = ""):
    """Schedule a one-off future action (e.g. 'turn off relay 1 after 30 seconds')."""
    task_id = f"task-{int(time.time()*1000)}"
    now = time.time()
    trigger_at = now + delay_seconds

    with tasks_lock:
        active_tasks[task_id] = {
            "task_id": task_id,
            "type": "one_off",
            "device_id": device_id,
            "target_state": target_state,
            "trigger_at": trigger_at,
            "description": description or f"Switch {device_id} to {target_state} in {delay_seconds}s",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
    return task_id

def schedule_reminder(message: str, delay_seconds: float):
    """Schedule a reminder message to be sent via Telegram / Dashboard."""
    task_id = f"remind-{int(time.time()*1000)}"
    now = time.time()
    trigger_at = now + delay_seconds

    with tasks_lock:
        active_tasks[task_id] = {
            "task_id": task_id,
            "type": "reminder",
            "message": message,
            "trigger_at": trigger_at,
            "description": f"Reminder: '{message}' in {int(delay_seconds)}s",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
    logger.info(f"Heartbeat: Scheduled reminder {task_id}: '{message}' in {int(delay_seconds)}s")
    return task_id

def schedule_reminder_at_clock_time(message: str, time_str: str):
    """Schedule a reminder message at a specific clock time or relative duration (e.g. '5min', '9am', '09:00', '10 sec')."""
    from datetime import datetime, timedelta
    import re
    now_dt = datetime.now()

    cleaned = str(time_str).lower().strip()

    # Check for relative durations like '5min', '5 min', '10 sec', '1 hour'
    rel_match = re.search(r"(\d+(?:\.\d+)?)\s*(s|sec|second|m|min|minute|h|hr|hour)s?", cleaned)
    if rel_match:
        val = float(rel_match.group(1))
        unit = rel_match.group(2)
        if unit.startswith("h"):
            delay_seconds = val * 3600
        elif unit.startswith("m"):
            delay_seconds = val * 60
        else:
            delay_seconds = val
        return schedule_reminder(message, delay_seconds)

    is_pm = "pm" in cleaned
    is_am = "am" in cleaned
    cleaned = cleaned.replace("am", "").replace("pm", "").strip()

    try:
        parts = cleaned.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0

        if is_pm and hour < 12:
            hour += 12
        elif is_am and hour == 12:
            hour = 0

        target_dt = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target_dt <= now_dt:
            target_dt += timedelta(days=1)

        delay_seconds = (target_dt - now_dt).total_seconds()
        return schedule_reminder(message, delay_seconds)
    except Exception as ex:
        logger.warning(f"Could not parse clock time '{time_str}', defaulting to 60s reminder: {ex}")
        return schedule_reminder(message, 60.0)

def schedule_at_clock_time(device_id: str, target_state: str, time_str: str, description: str = ""):
    """
    Schedule an action at a specific clock time (e.g. '05:00', '5:00 am', '17:30', '5pm')
    or relative duration (e.g. '5min', '10s').
    Computes exact seconds until that time.
    """
    from datetime import datetime, timedelta
    import re
    now_dt = datetime.now()

    cleaned = str(time_str).lower().strip()

    # Check for relative durations like '5min', '10s', '1h'
    rel_match = re.search(r"(\d+(?:\.\d+)?)\s*(s|sec|second|m|min|minute|h|hr|hour)s?", cleaned)
    if rel_match:
        val = float(rel_match.group(1))
        unit = rel_match.group(2)
        if unit.startswith("h"):
            delay_seconds = val * 3600
        elif unit.startswith("m"):
            delay_seconds = val * 60
        else:
            delay_seconds = val
        desc = description or f"Switch {device_id} to {target_state} in {int(delay_seconds)}s"
        return schedule_timed_action(device_id, target_state, delay_seconds, desc)

    is_pm = "pm" in cleaned
    is_am = "am" in cleaned
    cleaned = cleaned.replace("am", "").replace("pm", "").strip()

    try:
        parts = cleaned.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0

        if is_pm and hour < 12:
            hour += 12
        elif is_am and hour == 12:
            hour = 0

        target_dt = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # If the target time already passed today, schedule for tomorrow
        if target_dt <= now_dt:
            target_dt += timedelta(days=1)

        delay_seconds = (target_dt - now_dt).total_seconds()
        desc = description or f"Switch {device_id} to {target_state} at {time_str} (in {round(delay_seconds/3600, 1)}h)"
        
        logger.info(f"Heartbeat: Clock schedule set -> {device_id} to {target_state} at {target_dt.strftime('%Y-%m-%d %H:%M')} (delay: {int(delay_seconds)}s)")
        return schedule_timed_action(device_id, target_state, delay_seconds, desc)
    except Exception as ex:
        logger.warning(f"Could not parse clock time '{time_str}', defaulting to 60s delay: {ex}")
        return schedule_timed_action(device_id, target_state, 60.0, description or f"Switch {device_id} to {target_state} in 60s")

def schedule_relay_cycle(device_id: str, interval_seconds: float, cycles: int):
    """
    Safely schedule a repeating cycle:
    e.g. turn on and off after 2 sec each for 5 cycles.
    Bounded to safe rates (min interval 1.5s, max 10 cycles).
    """
    interval_seconds = max(1.5, interval_seconds) # prevent rapid contact chattering
    cycles = min(10, max(1, cycles))

    task_id = f"cycle-{int(time.time()*1000)}"
    now = time.time()

    with tasks_lock:
        active_tasks[task_id] = {
            "task_id": task_id,
            "type": "cycle",
            "device_id": device_id,
            "current_state": "ON",
            "trigger_at": now, # Start immediately
            "interval_sec": interval_seconds,
            "remaining_switches": cycles * 2, # Each cycle has 1 ON and 1 OFF
            "description": f"Cycle {device_id} every {interval_seconds}s ({cycles} cycles)",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
    logger.info(f"Heartbeat: Registered cycle {task_id} for {device_id}: {cycles} cycles at {interval_seconds}s")
    
    # Trigger first ON step immediately so user sees and hears instant action!
    if command_executor_fn:
        try:
            command_executor_fn(device_id, "ON")
            with tasks_lock:
                if task_id in active_tasks:
                    active_tasks[task_id]["current_state"] = "OFF"
                    active_tasks[task_id]["trigger_at"] = time.time() + interval_seconds
                    active_tasks[task_id]["remaining_switches"] -= 1
        except Exception as e:
            logger.error(f"Error triggering initial cycle state: {e}")

    return task_id

def get_active_tasks():
    with tasks_lock:
        task_list = []
        now = time.time()
        for tid, t in active_tasks.items():
            t_copy = dict(t)
            t_copy["seconds_until_next_run"] = max(0.0, round(t["trigger_at"] - now, 1))
            task_list.append(t_copy)
        return task_list

def cancel_task(task_id: str):
    with tasks_lock:
        if task_id in active_tasks:
            removed = active_tasks.pop(task_id)
            logger.info(f"Heartbeat: Cancelled task {task_id}")
            return True
        return False

def _heartbeat_loop():
    """Background engine ticking every 500ms to evaluate scheduled cron/interval tasks."""
    logger.info("Heartbeat automation engine background loop started.")
    while True:
        try:
            time.sleep(0.5)
            now = time.time()
            tasks_to_process = []

            with tasks_lock:
                for task_id, task in list(active_tasks.items()):
                    if now >= task["trigger_at"]:
                        tasks_to_process.append(dict(task))

            # Evaluate Generalized Temperature Automation Rule
            if temp_automation["enabled"] and command_executor_fn:
                try:
                    conn = get_db_connection()
                    cur = conn.cursor()
                    cur.execute("SELECT temperature_c FROM sensor_readings ORDER BY id DESC LIMIT 1")
                    row = cur.fetchone()
                    conn.close()

                    if row:
                        current_temp = row["temperature_c"]
                        dev = temp_automation["device_id"]
                        thresh = temp_automation["threshold"]
                        cond = temp_automation["condition"] # "above" or "below"
                        target_st = temp_automation["target_state"] # "ON" or "OFF"

                        triggered = False
                        if cond in ["above", "greater", "higher", "reaches"] and current_temp >= thresh:
                            triggered = True
                        elif cond in ["below", "less", "lower", "drops"] and current_temp <= thresh:
                            triggered = True

                        if triggered:
                            logger.info(f"[RULE TRIGGERED] Current temp is {current_temp}°C (Target: {cond} {thresh}°C) -> Switching {dev} to {target_st}")
                            command_executor_fn(dev, target_st)
                            # Once triggered, disable rule so it does not keep spamming commands every 500ms
                            temp_automation["enabled"] = False
                except Exception as ex:
                    logger.error(f"Error evaluating rule: {ex}")

            # Proactive Assistant Evaluator (runs periodically)
            if int(now) % 15 == 0:
                try:
                    conn = get_db_connection()
                    cur = conn.cursor()
                    cur.execute("SELECT temperature_c FROM sensor_readings ORDER BY id DESC LIMIT 1")
                    temp_row = cur.fetchone()
                    cur.execute("SELECT reported_state FROM device_state WHERE device_id='relay-01'")
                    relay_row = cur.fetchone()

                    if temp_row and relay_row:
                        current_temp = temp_row["temperature_c"]
                        fan_state = relay_row["reported_state"]

                        # Proactive rule: Room is warm (>= 32.5C) and Fan is OFF
                        if current_temp >= 32.5 and fan_state == "OFF":
                            if auto_mode_enabled:
                                # AUTONOMOUS MODE: Switch relay immediately!
                                logger.info(f"[AUTO-MODE] High temp detected ({current_temp}°C). Autonomously switching relay-01 ON!")
                                command_executor_fn("relay-01", "ON")
                                log_event("AUTO_MODE_ACTION", "INFO", f"Autonomous activation of relay-01 at {current_temp}°C")
                                
                                # Notify user on Telegram about the autonomous action
                                try:
                                    import telegram_bot
                                    telegram_bot.send_telegram_message(
                                        f"⚡ *[Autonomous Action]*\nRoom temperature reached *{current_temp}°C*. I have turned *ON* the fan (relay-01) for you."
                                    )
                                except Exception:
                                    pass
                            else:
                                # SEMI-AUTONOMOUS MODE: Ask user first via Dashboard and Telegram!
                                cur.execute("SELECT id FROM proactive_alerts WHERE alert_type='heat_warning' AND status='pending'")
                                if not cur.fetchone():
                                    msg = f"Room temperature is high ({current_temp}°C) and the fan is OFF. Would you like to turn it ON?"
                                    now_utc = datetime.now(timezone.utc).isoformat()
                                    cur.execute("""
                                        INSERT INTO proactive_alerts (alert_type, message, device_id, recommended_state, status, created_at)
                                        VALUES ('heat_warning', ?, 'relay-01', 'ON', 'pending', ?)
                                    """, (msg, now_utc))
                                    conn.commit()
                                    logger.info(f"[PROACTIVE ALERT GENERATED] {msg}")

                                    # Send interactive Telegram alert with clickable buttons!
                                    try:
                                        import telegram_bot
                                        keyboard = [
                                            [
                                                {"text": " Turn ON Fan", "callback_data": "cmd:relay-01:ON"},
                                                {"text": "❌ Dismiss", "callback_data": "dismiss"}
                                            ]
                                        ]
                                        telegram_bot.send_telegram_message(
                                            f"🌡 *Proactive Home Suggestion*\n\nRoom temperature is *{current_temp}°C* and the fan is OFF.\nWould you like me to turn it ON?",
                                            inline_keyboard=keyboard
                                        )
                                    except Exception:
                                        pass
                    conn.close()
                except Exception as pe:
                    pass

            for task in tasks_to_process:
                task_id = task["task_id"]
                device_id = task.get("device_id")

                if task["type"] == "reminder":
                    remind_msg = task.get("message", "Reminder!")
                    logger.info(f"Heartbeat firing reminder task {task_id}: {remind_msg}")
                    log_event("REMINDER_TRIGGERED", "INFO", remind_msg, task)
                    try:
                        import telegram_bot
                        telegram_bot.send_telegram_message(f"⏰ <b>Reminder:</b> {remind_msg}")
                    except Exception as te:
                        logger.error(f"Failed to dispatch Telegram reminder: {te}")
                    with tasks_lock:
                        active_tasks.pop(task_id, None)

                elif task["type"] == "one_off":
                    target_state = task["target_state"]
                    logger.info(f"Heartbeat firing one-off task {task_id}: {device_id} -> {target_state}")
                    if command_executor_fn:
                        try:
                            command_executor_fn(device_id, target_state)
                            log_event("HEARTBEAT_TASK", "INFO", f"Heartbeat executed {task_id}", task)
                        except Exception as e:
                            logger.error(f"Heartbeat task execution failed: {e}")
                    with tasks_lock:
                        active_tasks.pop(task_id, None)

                elif task["type"] == "cycle":
                    next_state = task["current_state"]
                    logger.info(f"Heartbeat cycle tick {task_id}: {device_id} -> {next_state} (remaining switches: {task['remaining_switches']})")
                    if command_executor_fn:
                        try:
                            command_executor_fn(device_id, next_state)
                        except Exception as e:
                            logger.error(f"Heartbeat cycle step failed: {e}")

                    with tasks_lock:
                        if task_id in active_tasks:
                            active_tasks[task_id]["remaining_switches"] -= 1
                            if active_tasks[task_id]["remaining_switches"] <= 0:
                                active_tasks.pop(task_id, None)
                                if command_executor_fn:
                                    command_executor_fn(device_id, "OFF")
                                logger.info(f"Heartbeat cycle {task_id} completed. Left {device_id} in OFF.")
                            else:
                                active_tasks[task_id]["current_state"] = "OFF" if next_state == "ON" else "ON"
                                active_tasks[task_id]["trigger_at"] = time.time() + task["interval_sec"]
        except Exception as main_e:
            logger.error(f"Heartbeat loop exception: {main_e}")
            time.sleep(1)

# Start background thread immediately upon module load
_hb_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
_hb_thread.start()

def start_heartbeat():
    pass

def stop_heartbeat():
    pass
