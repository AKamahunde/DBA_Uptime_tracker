#!/usr/bin/env python3
"""
MySQL Journal Incident Monitor
Version: 2.0.0
Description: Scans systemd journal and boot records for:
             1. MySQL service shutdowns, OOM kills, and process crashes.
             2. System reboots, kernel crashes, and abrupt power cuts.
             Calculates exact downtime and POSTs incidents to DBTRAC /api/incident/ingest/.
"""

import os
import sys
import re
import json
import socket
import subprocess
import urllib.request
from datetime import datetime, timedelta


def parse_journal_timestamp(line):
    """
    Parses timestamps from systemd journal output line.
    Example line: 'Jul 28 06:45:12 nickledbnew systemd[1]: Stopping mysql.service...'
    or ISO: '2026-10-19T14:15:23+0300'
    """
    try:
        # Try ISO format first
        iso_m = re.match(r'(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2})', line)
        if iso_m:
            dt_str = iso_m.group(1).replace('T', ' ')
            return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").astimezone()

        # Fallback to syslog format 'Mmm dd hh:mm:ss'
        ts_str = line[:15]
        current_year = datetime.now().year
        dt = datetime.strptime(f"{current_year} {ts_str}", "%Y %b %d %H:%M:%S")
        return dt.astimezone()
    except Exception:
        return None


def parse_lookback_hours(timeframe_str):
    m = re.search(r'(\d+)\s*(hour|hr|day|d)', str(timeframe_str).lower())
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        if 'day' in unit or 'd' in unit:
            return num * 24
        return num
    return 48


def get_meaningful_summary(outage_type, logs):
    is_oom = any("Out of memory" in line or "OOM-killer" in line or "Killed process" in line for line in logs)

    if is_oom:
        return (
            "DBA Summary: [CRITICAL OUTAGE - OUT OF MEMORY]\n"
            "MySQL was terminated by the Linux Out-of-Memory (OOM) killer because the server ran out of RAM.\n"
            "The database automatically self-healed and restarted once memory was freed."
        )
    elif outage_type == "Crash Outage":
        return (
            "DBA Summary: [CRITICAL OUTAGE - UNEXPECTED CRASH]\n"
            "MySQL process exited ungracefully (e.g., segmentation fault, abort, or forced kill -9).\n"
            "The database automatically self-healed and restarted."
        )
    elif outage_type == "Graceful Restart":
        return (
            "DBA Summary: [MAINTENANCE - CLEAN RESTART]\n"
            "A system administrator or update script initiated a graceful shutdown and restart of the database service.\n"
            "The service stopped cleanly and started back up."
        )
    elif outage_type == "Power Outage / Abrupt Crash":
        return (
            "DBA Summary: [CRITICAL OUTAGE - UNEXPECTED POWER CUT / HARD CRASH]\n"
            "The server suffered an abrupt power cut or hardware/kernel crash without graceful shutdown.\n"
            "Power was subsequently restored and the system booted, restoring MySQL database services."
        )
    else:
        return (
            "DBA Summary: [OUTAGE - SYSTEM REBOOT]\n"
            "The operating system rebooted or MySQL encountered a sudden service restart.\n"
            "The database started up successfully."
        )


def detect_boot_and_power_outages(lookback_hours=48, db_type="MySQL", system_name=None):
    """
    Detects power outages and system crashes by analyzing boot transitions
    from journalctl --list-boots and /var/log/wtmp (via last).
    """
    if not system_name:
        system_name = socket.gethostname()

    detected = []
    now = datetime.now().astimezone()
    cutoff = now - timedelta(hours=lookback_hours)

    # 1. Primary Method: Query journalctl --list-boots
    try:
        res = subprocess.run(
            ["journalctl", "--list-boots", "--no-pager"],
            capture_output=True,
            text=True
        )
        if res.returncode == 0 and res.stdout.strip():
            boot_lines = res.stdout.strip().splitlines()
            boots = []
            for line in boot_lines:
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                boot_id = parts[1]
                ts_matches = re.findall(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line)
                if len(ts_matches) >= 2:
                    try:
                        b_start = datetime.strptime(ts_matches[0], "%Y-%m-%d %H:%M:%S").astimezone()
                        b_end = datetime.strptime(ts_matches[1], "%Y-%m-%d %H:%M:%S").astimezone()
                        boots.append({
                            'id': boot_id,
                            'start': b_start,
                            'end': b_end,
                            'raw': line
                        })
                    except Exception:
                        pass

            # Check transitions between boots
            for i in range(len(boots) - 1):
                prev_boot = boots[i]
                curr_boot = boots[i + 1]

                if curr_boot['start'] >= cutoff or prev_boot['end'] >= cutoff:
                    # Check if prev_boot had a graceful shutdown sequence
                    clean_shutdown = False
                    try:
                        chk = subprocess.run(
                            ["journalctl", "-b", prev_boot['id'], "-n", "40", "--no-pager"],
                            capture_output=True,
                            text=True
                        )
                        if chk.returncode == 0:
                            clean_markers = [
                                "Shutting down.",
                                "systemd-shutdown",
                                "Reached target Power-Off",
                                "Reached target Shutdown",
                                "Reached target Reboot",
                                "systemd[1]: Stopped target",
                            ]
                            clean_shutdown = any(m in chk.stdout for m in clean_markers)
                    except Exception:
                        pass

                    # Determine when MySQL came up in curr_boot
                    mysql_start_dt = curr_boot['start']
                    try:
                        m_chk = subprocess.run(
                            ["journalctl", "-b", curr_boot['id'], "-u", "mysql", "--no-pager"],
                            capture_output=True,
                            text=True
                        )
                        if m_chk.returncode == 0:
                            for m_line in m_chk.stdout.splitlines():
                                if "Started mysql.service" in m_line or "Starting MySQL" in m_line:
                                    dt_val = parse_journal_timestamp(m_line)
                                    if dt_val:
                                        mysql_start_dt = dt_val
                                        break
                    except Exception:
                        pass

                    outage_start = prev_boot['end']
                    outage_end = mysql_start_dt
                    duration_sec = (outage_end - outage_start).total_seconds()

                    if duration_sec > 30:  # Downtime greater than 30s
                        outage_type = "Server Reboot" if clean_shutdown else "Power Outage / Abrupt Crash"
                        summary = get_meaningful_summary(outage_type, [])

                        detected.append({
                            "start": outage_start.strftime("%Y-%m-%d %H:%M:%S"),
                            "end": outage_end.strftime("%Y-%m-%d %H:%M:%S"),
                            "database": db_type,
                            "system": system_name,
                            "root_cause": (
                                f"AUTO-LOGGED: {outage_type} detected.\n\n{summary}\n\n"
                                f"Evidence:\n"
                                f"Previous Boot ({prev_boot['id'][:8]}): Last log recorded at {outage_start.strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"Subsequent Boot ({curr_boot['id'][:8]}): System booted and MySQL started at {outage_end.strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"Calculated Total Downtime: {round(duration_sec / 60, 1)} minutes"
                            )
                        })
    except Exception as e:
        print(f"[WARN] Boot list check warning: {e}", file=sys.stderr)

    # 2. Fallback via `last -x -F reboot shutdown` if journal boots returned nothing
    if not detected:
        try:
            l_res = subprocess.run(
                ["last", "-x", "-F", "-n", "30", "reboot", "shutdown"],
                capture_output=True,
                text=True
            )
            if l_res.returncode == 0 and l_res.stdout.strip():
                wtmp_lines = l_res.stdout.strip().splitlines()
                date_pattern = r'[A-Za-z]{3}\s+[A-Za-z]{3}\s+\d+\s+\d{2}:\d{2}:\d{2}\s+\d{4}'
                for line in wtmp_lines:
                    if 'crash' in line.lower() or ('reboot' in line and '-' in line):
                        dts = re.findall(date_pattern, line)
                        if len(dts) >= 2:
                            try:
                                crash_dt = datetime.strptime(dts[1], "%a %b %d %H:%M:%S %Y").astimezone()
                                if crash_dt >= cutoff:
                                    btime_res = subprocess.run(["uptime", "-s"], capture_output=True, text=True)
                                    if btime_res.returncode == 0 and btime_res.stdout.strip():
                                        boot_dt = datetime.strptime(btime_res.stdout.strip(), "%Y-%m-%d %H:%M:%S").astimezone()
                                        if boot_dt > crash_dt:
                                            dur_m = round((boot_dt - crash_dt).total_seconds() / 60, 1)
                                            detected.append({
                                                "start": crash_dt.strftime("%Y-%m-%d %H:%M:%S"),
                                                "end": boot_dt.strftime("%Y-%m-%d %H:%M:%S"),
                                                "database": db_type,
                                                "system": system_name,
                                                "root_cause": (
                                                    "DBA Summary: [CRITICAL OUTAGE - UNEXPECTED POWER CUT / HARD CRASH]\n"
                                                    "Server crashed abruptly due to power loss (recorded via /var/log/wtmp crash record).\n\n"
                                                    f"Power loss detected: {crash_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
                                                    f"Server booted and recovered: {boot_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
                                                    f"Total Downtime: {dur_m} minutes"
                                                )
                                            })
                            except Exception:
                                pass
        except Exception as e:
            print(f"[WARN] WTMP last check warning: {e}", file=sys.stderr)

    return detected


def main():
    api_url = "http://192.168.35.38:90/api/incident/ingest/"
    api_key = "uptimetracker_api_9f2c1d8e45x9"
    timeframe = "48 hours ago"
    db_type = "MySQL"
    system_name = socket.gethostname()

    # CLI Overrides
    if len(sys.argv) > 1:
        api_url = sys.argv[1]
    if len(sys.argv) > 2:
        api_key = sys.argv[2]
    if len(sys.argv) > 3:
        timeframe = sys.argv[3]

    if not api_key:
        api_key = os.environ.get('REPLICATION_API_KEY', '')

    if not api_key:
        print("Error: Ingestion API Key must be provided as an argument or in REPLICATION_API_KEY env var.", file=sys.stderr)
        sys.exit(1)

    lookback_hours = parse_lookback_hours(timeframe)
    print(f"Running systemd journal & power outage check for MySQL (lookback: {lookback_hours} hours)...")

    incidents_detected = []

    # 1. Check for Power Cuts, Kernel Crashes & System Reboots
    boot_incidents = detect_boot_and_power_outages(lookback_hours=lookback_hours, db_type=db_type, system_name=system_name)
    if boot_incidents:
        print(f"Found {len(boot_incidents)} power cut / system reboot event(s) across boot history.")
        incidents_detected.extend(boot_incidents)

    # 2. Check for In-Session MySQL Service Shutdowns, OOM Kills & Crashes
    try:
        result = subprocess.run(
            ["journalctl", "-u", "mysql", "--since", f"{lookback_hours} hours ago", "--no-pager"],
            capture_output=True,
            text=True,
            check=True
        )
        lines = result.stdout.splitlines()

        outage_start_dt = None
        outage_type = "Restart"
        log_snippet = []

        for line in lines:
            if not line.strip():
                continue

            dt = parse_journal_timestamp(line)
            if not dt:
                continue

            # Detect Outage Starts
            if "Stopping mysql.service" in line:
                if not outage_start_dt:
                    outage_start_dt = dt
                    outage_type = "Graceful Restart"
                log_snippet.append(line)
            elif "Main process exited" in line or "Failed with result" in line:
                if not outage_start_dt:
                    outage_start_dt = dt
                    outage_type = "Crash Outage"
                log_snippet.append(line)

            # Detect Outage Ends
            elif "Started mysql.service" in line:
                if outage_start_dt:
                    log_snippet.append(line)
                    summary = get_meaningful_summary(outage_type, log_snippet)
                    
                    # De-duplicate against any already detected boot outage
                    start_str = outage_start_dt.strftime("%Y-%m-%d %H:%M:%S")
                    end_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                    if not any(b['start'] == start_str for b in incidents_detected):
                        incidents_detected.append({
                            "start": start_str,
                            "end": end_str,
                            "database": db_type,
                            "system": system_name,
                            "root_cause": f"AUTO-LOGGED: {outage_type} detected.\n\n{summary}\n\nRaw Journal Logs:\n" + "\n".join(log_snippet)
                        })
                    outage_start_dt = None
                    log_snippet = []
            elif outage_start_dt:
                log_snippet.append(line)

    except Exception as e:
        print(f"Error executing journalctl: {e}", file=sys.stderr)

    if not incidents_detected:
        print(f"No service or power outages detected in logs for the last {lookback_hours} hours.")
        return

    print(f"Detected {len(incidents_detected)} outage incident(s). Ingesting to DBTRAC API...")

    success_count = 0
    for payload in incidents_detected:
        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                api_url,
                data=data,
                headers={
                    'Content-Type': 'application/json',
                    'X-API-KEY': api_key
                },
                method='POST'
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                res_body = json.loads(response.read().decode('utf-8'))
                if res_body.get('status') == 'success':
                    print(f"✓ Successfully Logged Incident to Staging: ID {res_body.get('id')} ({payload['start']} to {payload['end']})")
                    success_count += 1
                elif res_body.get('status') == 'ignored':
                    print(f"✓ Already Registered / Staged ({payload['start']})")
                    success_count += 1
                else:
                    print(f"API Response: {res_body}")
        except Exception as e:
            print(f"Failed to ingest incident starting at {payload['start']}: {e}", file=sys.stderr)

    print(f"Completed. Successfully processed {success_count} outage incident(s).")


if __name__ == '__main__':
    main()
