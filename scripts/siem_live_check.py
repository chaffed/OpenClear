# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Sends security events from OpenClear's own senders (services/siem/) to real tools in
containers, and checks they arrive (SIEM_PLAN.md, "Against real tools, before release"):

- Splunk Enterprise through its HTTP Event Collector, with indexer acknowledgement;
- Splunk through a TCP syslog input (RFC 5424, octet-counted, CEF);
- a syslog-ng relay over TLS (RFC 5425), in JSON and LEEF.

Needs Docker. Splunk's image is amd64 only; on an Apple-silicon Mac it runs under emulation
and takes several minutes to start. Starting it accepts Splunk's license and general terms
for this local, temporary instance (SPLUNK_START_ARGS, SPLUNK_GENERAL_TERMS). The containers
are removed afterwards unless --keep is given.

    python scripts/siem_live_check.py [--keep] [--skip-splunk]
"""

import argparse
import json
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openclear.services.siem import formats, senders  # noqa: E402

SPLUNK = "oc-siem-splunk"
SYSLOG_NG = "oc-siem-syslog-ng"
PASSWORD = "Oc-" + secrets.token_hex(8)
HEC_TOKEN = str(uuid.uuid4())


def run(*args: str, check: bool = True) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed:\n{result.stderr}")
    return result.stdout


def record(marker: str, n: int, name: str = "auth.login.failed") -> dict:
    return {
        "id": f"evt_{marker}_{n}", "sequence": n, "time": datetime.now(timezone.utc).isoformat(),
        "event": {"name": name, "category": "authentication", "outcome": "failure", "severity": "medium",
                  "reason": "invalid_credentials"},
        "bank": {"id": "00000000-0000-0000-0000-000000000001", "slug": "live-check"},
        "actor": {"type": "user", "id": "u1", "email": "tester@live-check.example"},
        "source": {"ip": "203.0.113.7", "user_agent": "live-check|with=odd\tchars"},
        "request": {"id": marker, "channel": "web"},
        "details": {"marker": marker, "n": n},
    }


def wait_for(what: str, check, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except Exception:  # noqa: BLE001 -- not up yet
            pass
        time.sleep(5)
    raise SystemExit(f"Timed out waiting for {what}")


# --- syslog-ng over TLS ---------------------------------------------------------------------------


def make_cert(directory: Path) -> Path:
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost", "-keyout", str(directory / "server.key"), "-out", str(directory / "server.crt"))
    (directory / "server.key").chmod(0o644)
    return directory / "server.crt"


def check_syslog_ng(workdir: Path) -> None:
    cert = make_cert(workdir)
    (workdir / "syslog-ng.conf").write_text("""@version: 4.0
@include "scl.conf"
source s_tls {
  syslog(transport("tls") port(6514)
         tls(key-file("/etc/syslog-ng/tls/server.key") cert-file("/etc/syslog-ng/tls/server.crt") peer-verify(optional-untrusted)));
};
destination d_file { file("/var/log/openclear.log" template("$ISODATE $HOST $PROGRAM $MSGID $MSG\\n")); };
log { source(s_tls); destination(d_file); };
""")
    run("docker", "rm", "-f", SYSLOG_NG, check=False)
    run("docker", "run", "-d", "--name", SYSLOG_NG, "-p", "16514:6514",
        "-v", f"{workdir / 'syslog-ng.conf'}:/etc/syslog-ng/syslog-ng.conf:ro",
        "-v", f"{workdir}:/etc/syslog-ng/tls:ro", "balabit/syslog-ng:latest", "--no-caps")
    time.sleep(3)
    marker = "ng" + secrets.token_hex(4)
    for fmt in ("json", "leef", "cef"):
        target = senders.Target(kind="syslog", host="localhost", port=16514, transport="tls", format=fmt, allow_private=True,
                                ca_file=str(cert), timeout=10)
        wait_for("syslog-ng to accept TLS", lambda t=target, f=fmt: senders.send(t, [record(f"{marker}-{f}", 1), record(f"{marker}-{f}", 2)],
                                                                             hostname="oc-live") or True, 60)
    time.sleep(2)
    lines = [line for line in run("docker", "exec", SYSLOG_NG, "cat", "/var/log/openclear.log").splitlines() if marker in line]
    print(f"syslog-ng received {len(lines)} lines")
    for line in lines:
        print("   ", line[:160])
    assert len(lines) == 6, "expected 6 messages (2 each in JSON, LEEF and CEF)"
    assert all(" openclear auth.login.failed " in line for line in lines), "RFC 5424 app name and message id"
    json_line = next(line for line in lines if f"{marker}-json" in line)
    assert json.loads(json_line.split(" auth.login.failed ", 1)[1])["details"]["marker"] == f"{marker}-json"
    assert any("LEEF:2.0|OpenClear|OpenClear|" in line for line in lines) and any("CEF:0|OpenClear|OpenClear|" in line for line in lines)
    print("syslog-ng over TLS: OK")


# --- Splunk ---------------------------------------------------------------------------------------


def splunk_api(method: str, path: str, **kwargs) -> httpx.Response:
    with httpx.Client(verify=False, timeout=30, auth=("admin", PASSWORD)) as client:
        return client.request(method, f"https://localhost:18089{path}", **kwargs)


def splunk_search(query: str) -> list[dict]:
    response = splunk_api("POST", "/services/search/jobs/export", data={"search": query, "output_mode": "json"})
    return [json.loads(line)["result"] for line in response.text.splitlines() if line.strip() and '"result"' in line]


def check_splunk(reuse: bool = False) -> None:
    global PASSWORD
    running = run("docker", "inspect", "-f", "{{.State.Running}}", SPLUNK, check=False).strip() == "true"
    if reuse and running:
        env = json.loads(run("docker", "inspect", "-f", "{{json .Config.Env}}", SPLUNK))
        PASSWORD = next(e.split("=", 1)[1] for e in env if e.startswith("SPLUNK_PASSWORD="))
    else:
        start_splunk()
    print("Waiting for Splunk to start (several minutes under emulation)…")
    # Splunk restarts itself during setup, so its API answering isn't enough: wait for the
    # container's own health check.
    wait_for("Splunk to be healthy", lambda: run("docker", "inspect", "-f", "{{.State.Health.Status}}", SPLUNK).strip() == "healthy", 1800)
    wait_for("Splunk's management API", lambda: splunk_api("GET", "/services/server/info").status_code == 200, 300)
    configure_and_send()


def start_splunk() -> None:
    run("docker", "rm", "-f", SPLUNK, check=False)
    run("docker", "run", "-d", "--name", SPLUNK, "--platform", "linux/amd64", "-p", "18088:8088", "-p", "18089:8089", "-p", "11514:1514",
        "-e", "SPLUNK_START_ARGS=--accept-license", "-e", "SPLUNK_GENERAL_TERMS=--accept-sgt-current-at-splunk-com",
        "-e", f"SPLUNK_PASSWORD={PASSWORD}", "-e", f"SPLUNK_HEC_TOKEN={HEC_TOKEN}", "splunk/splunk:latest")


def configure_and_send() -> None:

    # An index, a HEC token with indexer acknowledgement, and a TCP syslog input.
    splunk_api("POST", "/services/data/indexes", data={"name": "openclear"})
    created = splunk_api("POST", "/servicesNS/nobody/splunk_httpinput/data/inputs/http",
                         data={"name": "openclear-ack", "index": "openclear", "useACK": "1", "output_mode": "json"})
    ack_token = json.loads(created.text)["entry"][0]["content"]["token"]
    splunk_api("POST", "/services/data/inputs/tcp/raw", data={"name": "1514", "sourcetype": "syslog", "index": "openclear"})
    time.sleep(10)

    marker = "sp" + secrets.token_hex(4)
    hec = senders.Target(kind="splunk_hec", url="https://localhost:18088", token=ack_token, splunk_index="openclear",
                         splunk_sourcetype="openclear:security", channel=str(uuid.uuid4()), allow_private=True, verify=False, timeout=60)
    senders.send(hec, [record(f"{marker}-hec", n) for n in range(1, 4)], hostname="oc-live")
    print("Splunk HEC accepted 3 events and confirmed they were indexed")
    syslog = senders.Target(kind="syslog", host="localhost", port=11514, transport="tcp", format="cef", allow_private=True, timeout=10)
    senders.send(syslog, [record(f"{marker}-syslog", n) for n in range(1, 3)], hostname="oc-live")

    def found(kind: str, expected: int) -> bool:
        results = splunk_search(f'search index=openclear "{marker}-{kind}" | stats count')
        return bool(results) and int(results[0]["count"]) >= expected

    wait_for("the HEC events in a search", lambda: found("hec", 3), 180)
    hec_events = splunk_search(f'search index=openclear sourcetype="openclear:security" "{marker}-hec" | spath | table event.name details.n source.ip')
    print("Splunk search, HEC:", hec_events)
    assert {e["event.name"] for e in hec_events} == {"auth.login.failed"} and len(hec_events) == 3
    wait_for("the syslog events in a search", lambda: found("syslog", 2), 180)
    print("Splunk search, syslog (CEF):", splunk_search(f'search index=openclear "{marker}-syslog" | table _raw')[:1])
    print("Splunk through HEC (with acknowledgement) and syslog: OK")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true", help="leave the containers running")
    parser.add_argument("--skip-splunk", action="store_true")
    parser.add_argument("--reuse-splunk", action="store_true", help="use a running container from an earlier --keep run")
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:  # under $HOME so Colima can mount it
            check_syslog_ng(Path(tmp))
        if not args.skip_splunk:
            check_splunk(reuse=args.reuse_splunk)
    finally:
        if not args.keep:
            run("docker", "rm", "-f", SYSLOG_NG, SPLUNK, check=False)
    print("All live checks passed.")


if __name__ == "__main__":
    main()
