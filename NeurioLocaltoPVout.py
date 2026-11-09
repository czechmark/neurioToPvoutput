#!/usr/bin/env python3
"""
Read a Neurio sensor on the local network and upload to PVOutput.

Two ways to run it:
  cron:     */5 * * * * /usr/bin/python3 /opt/neurio/neurio_pvoutput.py --once
  service:  python3 neurio_pvoutput.py          (loops every 5 min; use with systemd)

Configuration (environment variables, or a KEY=VALUE file passed with --config):
  NEURIO_HOST          IP or hostname of the Neurio sensor, e.g. 192.168.1.50
  PVOUTPUT_API_KEY     API key from pvoutput.org > Settings
  PVOUTPUT_SYSTEM_ID   System ID from pvoutput.org > Settings
  INTERVAL_MINUTES     optional, default 5 (must match your PVOutput status interval)
  GEN_ENERGY_FIELD     optional, eImp_Ws (default) or eExp_Ws - which counter on the
                       GENERATION channel counts produced energy on your install
  DB_PATH              optional, e.g. /var/www/neuriolog.db - also log each reading to
                       the SQLite "energy" table (replaces bothTables.py)

Uses only the Python standard library.
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

PVOUTPUT_URL = "https://pvoutput.org/service/r2/addstatus.jsp"
HTTP_TIMEOUT = 15
log = logging.getLogger("neurio_pvoutput")


# ---------------------------------------------------------------- config
def load_config(path=None):
    cfg = dict(os.environ)
    if path:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
    missing = [k for k in ("NEURIO_HOST", "PVOUTPUT_API_KEY", "PVOUTPUT_SYSTEM_ID")
               if not cfg.get(k)]
    if missing:
        sys.exit(f"Missing configuration: {', '.join(missing)}")
    cfg.setdefault("INTERVAL_MINUTES", "5")
    cfg.setdefault("GEN_ENERGY_FIELD", "eImp_Ws")
    return cfg


# ---------------------------------------------------------------- neurio
def read_neurio(host):
    """Return the parsed JSON from the sensor's local /current-sample endpoint."""
    url = f"http://{host}/current-sample"
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
        return json.load(r)


def summarize(sample, gen_field="eImp_Ws"):
    """
    Pull PVOutput values out of a Neurio sample.
    Energy counters on the sensor are lifetime watt-seconds; PVOutput wants Wh.
    """
    ch = {c.get("type"): c for c in sample.get("channels", [])}
    out = {}

    cons = ch.get("CONSUMPTION")
    if cons:
        out["v3"] = round(cons["eImp_Ws"] / 3600)      # lifetime consumption, Wh
        out["v4"] = max(0, round(cons["p_W"]))           # consumption power, W

    gen = ch.get("GENERATION")
    if gen:
        out["v1"] = round(gen[gen_field] / 3600)         # lifetime generation, Wh
        out["v2"] = abs(round(gen["p_W"]))               # generation power, W

    volts = [c["v_V"] for c in sample.get("channels", [])
             if c.get("type", "").startswith("PHASE_") and c.get("v_V")]
    if volts:
        out["v6"] = round(sum(volts) / len(volts), 1)    # voltage

    if not out:
        raise ValueError("No CONSUMPTION or GENERATION channel in Neurio sample")
    return out


# ---------------------------------------------------------------- sqlite log
def db_row(sample, when=None):
    """
    Row for the existing energy table, same layout bothTables.py wrote:
    timestamp, net import, net export, gen import, gen export,
    consumption import, consumption export - energies in kWh, 3 decimals.
    """
    ch = {c.get("type"): c for c in sample.get("channels", [])}
    kwh = lambda name, field: round(ch[name][field] / 3_600_000, 3)
    when = when or datetime.now()
    return (when.isoformat(sep=" ", timespec="microseconds"),
            kwh("NET", "eImp_Ws"), kwh("NET", "eExp_Ws"),
            kwh("GENERATION", "eImp_Ws"), kwh("GENERATION", "eExp_Ws"),
            kwh("CONSUMPTION", "eImp_Ws"), kwh("CONSUMPTION", "eExp_Ws"))


def log_db(path, sample):
    row = db_row(sample)
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.execute("INSERT INTO energy VALUES (?,?,?,?,?,?,?)", row)
        conn.commit()
    finally:
        conn.close()
    log.info("Logged to %s: %s", path, row)


# ---------------------------------------------------------------- pvoutput
def cumulative_flag(values):
    """c1=1: v1 and v3 are lifetime totals; 2: only v1; 3: only v3."""
    has_gen, has_cons = "v1" in values, "v3" in values
    return 1 if has_gen and has_cons else 2 if has_gen else 3


def upload(cfg, values, when):
    params = {"d": when.strftime("%Y%m%d"), "t": when.strftime("%H:%M"),
              "c1": cumulative_flag(values), **values}
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(PVOUTPUT_URL, data=data, headers={
        "X-Pvoutput-Apikey": cfg["PVOUTPUT_API_KEY"],
        "X-Pvoutput-SystemId": cfg["PVOUTPUT_SYSTEM_ID"],
    })
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            body = r.read().decode().strip()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"PVOutput {e.code}: {e.read().decode().strip()}") from e
    log.info("Uploaded %s -> %s", params, body)


# ---------------------------------------------------------------- run
def run_once(cfg):
    interval = int(cfg["INTERVAL_MINUTES"])
    now = datetime.now()
    # Stamp the reading on the interval boundary (e.g. 14:05, 14:10)
    when = now - timedelta(minutes=now.minute % interval,
                           seconds=now.second, microseconds=now.microsecond)
    sample = read_neurio(cfg["NEURIO_HOST"])
    errors = []
    try:
        upload(cfg, summarize(sample, cfg["GEN_ENERGY_FIELD"]), when)
    except Exception as e:
        errors.append(f"PVOutput: {e}")
    if cfg.get("DB_PATH"):
        try:
            log_db(cfg["DB_PATH"], sample)
        except Exception as e:
            errors.append(f"Database: {e}")
    if errors:  # one failing never blocks the other
        raise RuntimeError("; ".join(errors))


def run_forever(cfg):
    interval = int(cfg["INTERVAL_MINUTES"]) * 60
    while True:
        try:
            run_once(cfg)
        except Exception as e:  # keep looping; systemd restarts us if we die
            log.error("Upload failed: %s", e)
        # sleep until 15 s past the next interval boundary
        time.sleep(interval - (time.time() % interval) + 15)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--once", action="store_true", help="upload one reading and exit (cron)")
    p.add_argument("--config", help="KEY=VALUE config file")
    p.add_argument("--dry-run", action="store_true",
                   help="read the Neurio and print what would be sent")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)

    if args.dry_run:
        sample = read_neurio(cfg["NEURIO_HOST"])
        print(json.dumps(sample, indent=2))
        print("Would send:", summarize(sample, cfg["GEN_ENERGY_FIELD"]))
        if cfg.get("DB_PATH"):
            print("Would log to", cfg["DB_PATH"], db_row(sample))
    elif args.once:
        try:
            run_once(cfg)
        except Exception as e:
            log.error("Upload failed: %s", e)
            sys.exit(1)
    else:
        run_forever(cfg)


if __name__ == "__main__":
    main()

