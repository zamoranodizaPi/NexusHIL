#!/usr/bin/env python3
"""
Web interface for Nexus Sync Raspberry Pi Digital HIL.

The Raspberry Pi only drives/reads 3.3 V digital GPIO. Analog voltages and
currents must be supplied externally by the Ponovo source through the validated
conditioning/ADS path.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import signal
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from nexus_sync_rpi_digital_hil import (  # noqa: E402
    DigitalHil,
    GPIOBackend,
    OUTPUT_SAFE_STATE,
    REQUIRED_INPUTS,
    REQUIRED_OUTPUTS,
    load_config,
    validate_config,
)


COPYRIGHT = "Nexus by SIEZA 2026. Todos los derechos reservados."
LOGO_PATH = REPO_DIR / "assets" / "branding" / "sieza_logo_light.png"


class HilWebApp:
    def __init__(self, hil: DigitalHil) -> None:
        self.hil = hil
        self.lock = threading.RLock()
        self.armed = False
        self.pulse_running = False
        self.pulse_level = 0
        self.pulse_thread: threading.Thread | None = None
        self.last_message = "Servicio web iniciado en estado seguro"

    def output_value(self, name: str) -> int:
        return self.hil.gpio.input(self.hil.pin_out(name))

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "armed": self.armed,
                "message": self.last_message,
                "copyright": COPYRIGHT,
                "outputs_to_pz": {name: self.output_value(name) for name in REQUIRED_OUTPUTS},
                "inputs_from_pz": {name: self.hil.read(name) for name in REQUIRED_INPUTS},
                "optional_inputs_from_pz": {
                    name: self.hil.read_optional(name)
                    for name in self.hil.config.optional_inputs_from_pz
                },
                "pulse": {
                    "running": self.pulse_running,
                    "hz": self.hil.config.timing_ms.get("discharge_pulse_hz", 5),
                },
            }

    def arm(self, confirmations: Dict[str, bool]) -> Dict[str, Any]:
        required = ("gnd", "no_5v", "power_disabled", "series_resistors")
        missing = [name for name in required if not confirmations.get(name)]
        with self.lock:
            if missing:
                self.armed = False
                self.last_message = "No armado: faltan confirmaciones de seguridad"
                return {"ok": False, "message": self.last_message, "missing": missing}
            self.armed = True
            self.last_message = "Sistema armado para control digital"
            return {"ok": True, "message": self.last_message}

    def set_output(self, name: str, value: int) -> Dict[str, Any]:
        with self.lock:
            if not self.armed:
                return {"ok": False, "message": "Primero confirma seguridad y arma el sistema"}
            if name == "discharge_extinction_pulse" and self.pulse_running:
                return {"ok": False, "message": "Deten el tren de pulsos antes de cambiar esta salida manualmente"}
            if name not in REQUIRED_OUTPUTS:
                return {"ok": False, "message": f"Senal desconocida: {name}"}
            self.hil.write(name, 1 if value else 0)
            self.last_message = f"{name} = {1 if value else 0}"
            return {"ok": True, "message": self.last_message}

    def ready(self) -> Dict[str, Any]:
        with self.lock:
            if not self.armed:
                return {"ok": False, "message": "Primero confirma seguridad y arma el sistema"}
            self.stop_pulse_locked()
            self.hil.write("thermal_ok_in", 1)
            self.hil.write("exciter_ready", 1)
            self.hil.safe_stop()
            self.last_message = "Estado READY aplicado"
            return {"ok": True, "message": self.last_message}

    def safe_stop(self) -> Dict[str, Any]:
        with self.lock:
            self.stop_pulse_locked()
            self.hil.safe_stop()
            self.armed = False
            self.last_message = "SAFE_STOP aplicado; sistema desarmado"
            return {"ok": True, "message": self.last_message}

    def toggle_pulse(self, run: bool) -> Dict[str, Any]:
        with self.lock:
            if not self.armed:
                return {"ok": False, "message": "Primero confirma seguridad y arma el sistema"}
            if run and not self.pulse_running:
                self.pulse_running = True
                self.pulse_level = 0
                self.pulse_thread = threading.Thread(target=self.pulse_loop, daemon=True)
                self.pulse_thread.start()
                self.last_message = "Tren discharge_extinction_pulse iniciado"
            elif not run:
                self.stop_pulse_locked()
                self.last_message = "Tren discharge_extinction_pulse detenido"
            return {"ok": True, "message": self.last_message}

    def stop_pulse_locked(self) -> None:
        self.pulse_running = False
        self.pulse_level = 0
        self.hil.write("discharge_extinction_pulse", 0)

    def pulse_loop(self) -> None:
        while True:
            with self.lock:
                if not self.pulse_running:
                    self.hil.write("discharge_extinction_pulse", 0)
                    return
                hz = max(0.5, float(self.hil.config.timing_ms.get("discharge_pulse_hz", 5)))
                half_period = max(0.02, 0.5 / hz)
                self.pulse_level = 0 if self.pulse_level else 1
                self.hil.write("discharge_extinction_pulse", self.pulse_level)
            time.sleep(half_period)


def html_page() -> bytes:
    controls = "\n".join(
        f'<button class="signal" data-signal="{name}"><span>{name}</span><strong id="out-{name}">0</strong></button>'
        for name in REQUIRED_OUTPUTS
    )
    inputs = "\n".join(
        f'<div class="lamp" id="in-{name}"><span>{name}</span><strong>0</strong></div>'
        for name in REQUIRED_INPUTS
    )
    optional = "\n".join(
        f'<div class="lamp optional" id="opt-{name}"><span>{name}</span><strong>0</strong></div>'
        for name in ("field_pwm", "sync_pulse", "scr_gate_g1", "scr_gate_g2", "scr_gate_g3", "scr_gate_g4", "scr_gate_g5", "scr_gate_g6")
    )
    html = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nexus Sync Digital HIL Web</title>
  <style>
    :root {{
      --green: #9ed51d;
      --green-dark: #3f7f16;
      --charcoal: #2f3437;
      --line: #d9dee3;
      --soft: #f4f8ee;
      --danger: #b3261e;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f7f8fa; color: #202124; }}
    header {{ background: linear-gradient(90deg, var(--charcoal), #1f2a1f); color: white; border-bottom: 6px solid var(--green); padding: 16px 22px; }}
    .brand {{ display: flex; align-items: center; gap: 16px; max-width: 1280px; margin: 0 auto; }}
    .brand img {{ width: 82px; height: 82px; object-fit: contain; background: white; border-radius: 6px; padding: 5px; }}
    h1 {{ margin: 0; font-size: 28px; }}
    header p {{ margin: 6px 0 0; color: #eef6dd; }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 18px; display: grid; grid-template-columns: 1.25fr .95fr; gap: 16px; }}
    section {{ background: white; border: 1px solid var(--line); border-radius: 6px; padding: 14px; }}
    h2 {{ margin: 0 0 12px; color: var(--green-dark); border-bottom: 2px solid var(--soft); padding-bottom: 6px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
    button {{ font: inherit; cursor: pointer; border-radius: 6px; border: 1px solid #b7c0c7; }}
    button.signal {{ min-height: 78px; padding: 10px; text-align: left; background: #e5e7eb; display: flex; justify-content: space-between; align-items: center; gap: 8px; }}
    button.signal strong {{ min-width: 50px; text-align: center; padding: 8px; border-radius: 5px; background: #6b7280; color: white; }}
    button.signal.on {{ background: #edf7d8; border-color: var(--green-dark); }}
    button.signal.on strong {{ background: var(--green-dark); }}
    .actions {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 12px; }}
    .action {{ min-height: 58px; font-weight: 700; color: white; }}
    .arm {{ background: var(--green-dark); }}
    .ready {{ background: #2563eb; }}
    .safe {{ background: var(--danger); }}
    .pulse {{ width: 100%; min-height: 62px; margin-top: 10px; background: #0369a1; color: white; font-weight: 700; }}
    .lamp {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; border: 1px solid var(--line); border-radius: 6px; padding: 9px 10px; margin: 6px 0; background: #f3f4f6; }}
    .lamp strong {{ min-width: 36px; text-align: center; border-radius: 4px; padding: 5px; color: white; background: #6b7280; }}
    .lamp.on {{ background: #edf7d8; border-color: var(--green-dark); }}
    .lamp.on strong {{ background: var(--green-dark); }}
    .lamp.fault.on {{ background: #fdecec; border-color: var(--danger); }}
    .lamp.fault.on strong {{ background: var(--danger); }}
    .checks label {{ display: block; margin: 8px 0; }}
    .status {{ padding: 10px; border-radius: 6px; background: var(--soft); margin-top: 10px; min-height: 42px; }}
    footer {{ max-width: 1280px; margin: 0 auto; padding: 12px 18px 24px; color: #4b5563; font-size: 13px; }}
    @media (max-width: 900px) {{
      main {{ grid-template-columns: 1fr; }}
      .grid {{ grid-template-columns: 1fr; }}
      .actions {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <img src="/assets/branding/sieza_logo_light.png" alt="SIEZA">
      <div>
        <h1>Nexus Sync Digital HIL Web</h1>
        <p>Control digital Raspberry Pi para pruebas HIL. Analogicas por Ponovo.</p>
      </div>
    </div>
  </header>
  <main>
    <section>
      <h2>Raspberry -> PZ</h2>
      <div class="actions">
        <button class="action arm" id="armBtn">ARMAR</button>
        <button class="action ready" id="readyBtn">READY</button>
        <button class="action safe" id="safeBtn">SAFE_STOP</button>
      </div>
      <div class="checks">
        <label><input type="checkbox" id="gnd"> GND comun conectado primero</label>
        <label><input type="checkbox" id="no_5v"> No hay 5 V en GPIO</label>
        <label><input type="checkbox" id="power_disabled"> Potencia externa deshabilitada</label>
        <label><input type="checkbox" id="series_resistors"> Resistencias serie/proteccion instaladas</label>
      </div>
      <div class="status" id="message">Cargando...</div>
      <div class="grid">{controls}</div>
      <button class="pulse" id="pulseBtn">discharge_extinction_pulse PULSE OFF</button>
    </section>
    <section>
      <h2>PZ -> Raspberry</h2>
      <div>{inputs}</div>
      <h2>Monitoreo opcional</h2>
      <div>{optional}</div>
    </section>
  </main>
  <footer>{COPYRIGHT}</footer>
  <script>
    async function api(path, payload) {{
      const res = await fetch(path, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(payload || {{}})
      }});
      return await res.json();
    }}
    function setMessage(text) {{
      document.getElementById('message').textContent = text || '';
    }}
    function paintLamp(id, value, fault=false) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.toggle('on', !!value);
      el.classList.toggle('fault', fault);
      el.querySelector('strong').textContent = value == null ? '-' : value;
    }}
    async function refresh() {{
      try {{
        const res = await fetch('/api/status');
        const s = await res.json();
        setMessage((s.armed ? 'ARMADO - ' : 'DESARMADO - ') + s.message);
        for (const [name, value] of Object.entries(s.outputs_to_pz)) {{
          const btn = document.querySelector(`[data-signal="${{name}}"]`);
          if (btn) {{
            btn.classList.toggle('on', !!value);
            btn.querySelector('strong').textContent = value;
          }}
        }}
        for (const [name, value] of Object.entries(s.inputs_from_pz)) {{
          paintLamp(`in-${{name}}`, value, name === 'fault_out');
        }}
        for (const [name, value] of Object.entries(s.optional_inputs_from_pz)) {{
          paintLamp(`opt-${{name}}`, value, false);
        }}
        document.getElementById('pulseBtn').textContent = s.pulse.running
          ? `discharge_extinction_pulse PULSE ON (${{s.pulse.hz}} Hz)`
          : `discharge_extinction_pulse PULSE OFF (${{s.pulse.hz}} Hz)`;
        document.getElementById('pulseBtn').dataset.running = s.pulse.running ? '1' : '0';
      }} catch (err) {{
        setMessage('Sin conexion con servicio HIL: ' + err);
      }}
    }}
    document.getElementById('armBtn').onclick = async () => {{
      const out = await api('/api/arm', {{
        gnd: document.getElementById('gnd').checked,
        no_5v: document.getElementById('no_5v').checked,
        power_disabled: document.getElementById('power_disabled').checked,
        series_resistors: document.getElementById('series_resistors').checked
      }});
      setMessage(out.message);
      refresh();
    }};
    document.getElementById('readyBtn').onclick = async () => {{
      const out = await api('/api/ready');
      setMessage(out.message);
      refresh();
    }};
    document.getElementById('safeBtn').onclick = async () => {{
      const out = await api('/api/safe_stop');
      setMessage(out.message);
      refresh();
    }};
    document.getElementById('pulseBtn').onclick = async (ev) => {{
      const running = ev.currentTarget.dataset.running === '1';
      const out = await api('/api/pulse', {{ running: !running }});
      setMessage(out.message);
      refresh();
    }};
    for (const btn of document.querySelectorAll('button.signal')) {{
      btn.onclick = async () => {{
        const signal = btn.dataset.signal;
        const current = btn.classList.contains('on') ? 1 : 0;
        if (signal === 'plant_fault' && current === 0 && !confirm('Confirmar plant_fault=1')) return;
        const out = await api('/api/output', {{ signal, value: current ? 0 : 1 }});
        setMessage(out.message);
        refresh();
      }};
    }}
    refresh();
    setInterval(refresh, 500);
  </script>
</body>
</html>"""
    return html.encode("utf-8")


def json_response(handler: BaseHTTPRequestHandler, data: Dict[str, Any], status: int = 200) -> None:
    payload = json.dumps(data).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def make_handler(app: HilWebApp):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"{self.client_address[0]} - {fmt % args}")

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                payload = html_page()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if parsed.path == "/api/status":
                json_response(self, app.snapshot())
                return
            if parsed.path == "/assets/branding/sieza_logo_light.png" and LOGO_PATH.exists():
                data = LOGO_PATH.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mimetypes.guess_type(str(LOGO_PATH))[0] or "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                json_response(self, {"ok": False, "message": "JSON invalido"}, 400)
                return
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/api/arm":
                    json_response(self, app.arm(body))
                elif parsed.path == "/api/output":
                    json_response(self, app.set_output(str(body.get("signal", "")), int(body.get("value", 0))))
                elif parsed.path == "/api/ready":
                    json_response(self, app.ready())
                elif parsed.path == "/api/safe_stop":
                    json_response(self, app.safe_stop())
                elif parsed.path == "/api/pulse":
                    json_response(self, app.toggle_pulse(bool(body.get("running", False))))
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
            except Exception as exc:
                json_response(self, {"ok": False, "message": str(exc)}, 500)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Nexus Sync Digital HIL web server")
    parser.add_argument("--config", default=str(REPO_DIR / "rpi_digital_hil_config.json"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    validate_config(config, "web")
    gpio = GPIOBackend(dry_run=args.dry_run)
    hil = DigitalHil(config, gpio)
    app = HilWebApp(hil)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))

    def stop_handler(_signum, _frame) -> None:
        print("SAFE_STOP requested")
        app.safe_stop()
        server.shutdown()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    try:
        hil.setup()
        print(f"Nexus HIL web listening on http://{args.host}:{args.port}")
        server.serve_forever()
    finally:
        app.safe_stop()
        gpio.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
