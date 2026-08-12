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
        self.auto_active = False
        self.auto_cancel = False
        self.auto_step = "idle"
        self.auto_message = "Secuencia automatica no iniciada"
        self.auto_thread: threading.Thread | None = None

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
                "auto": {
                    "active": self.auto_active,
                    "step": self.auto_step,
                    "message": self.auto_message,
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
            self.auto_cancel = True
            self.stop_pulse_locked()
            self.hil.safe_stop()
            self.armed = False
            self.last_message = "SAFE_STOP aplicado; sistema desarmado"
            if self.auto_active:
                self.auto_step = "idle"
                self.auto_message = "Detenido manualmente (SAFE_STOP)."
            return {"ok": True, "message": self.last_message}

    def toggle_pulse(self, run: bool) -> Dict[str, Any]:
        with self.lock:
            if not self.armed:
                return {"ok": False, "message": "Primero confirma seguridad y arma el sistema"}
        if run:
            self.restart_pulse_thread_safe()
            with self.lock:
                self.last_message = "Tren discharge_extinction_pulse iniciado"
                return {"ok": True, "message": self.last_message}
        with self.lock:
            self.stop_pulse_locked()
            self.last_message = "Tren discharge_extinction_pulse detenido"
            return {"ok": True, "message": self.last_message}

    def stop_pulse_locked(self) -> None:
        self.pulse_running = False
        self.pulse_level = 0
        self.hil.write("discharge_extinction_pulse", 0)

    def restart_pulse_thread_safe(self) -> None:
        """Ensures exactly one live pulse_loop thread going forward.

        Real bug this fixes: the old start logic only checked the
        self.pulse_running *flag*, not whether a thread was actually
        alive. If that flag was ever left stale True (e.g. the PZ got
        power-cycled/reflashed independently but this Raspberry's own
        Python process kept running from an earlier test), a later
        "start pulse" call would see pulse_running=True and skip spawning
        a thread entirely -- discharge_extinction_pulse then stayed
        frozen at whatever level it was last left at, the PZ's frequency
        estimator never saw a single edge, and the discharge stage failed
        every time with discharge_freq_mhz stuck at 0. Always stop-and-join
        whatever thread actually exists (regardless of the flag) before
        starting a fresh one. Must be called WITHOUT self.lock held --
        it joins a thread that itself needs the lock to notice the stop
        and exit.
        """
        old_thread = self.pulse_thread
        with self.lock:
            self.pulse_running = False
        if old_thread is not None and old_thread.is_alive():
            old_thread.join(timeout=1.0)
        with self.lock:
            self.pulse_running = True
            self.pulse_level = 0
            self.pulse_thread = threading.Thread(target=self.pulse_loop, daemon=True)
            self.pulse_thread.start()

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

    # ------------------------------------------------------------------
    # Guided automatic sequence -- runs entirely on this Raspberry Pi (no
    # network round-trip in the timing-critical path). Polls the PZ's own
    # GPIO feedback signals (motor_run, fault_out, relay_56k, relay_fax --
    # already wired, no dependency on the PZ's own flaky embedded HTTP
    # server) and fires the timed output chain the instant motor_run goes
    # high. Timing constants below match the RTL windows confirmed on
    # bench 2026-08-11 (see docs/rpi_digital_hil_ponovo_running_procedure.md,
    # "Secuencia exacta verificada en banco"):
    #   - full_volts must land within incomplete_sequence_timeout_ms=12s of
    #     motor_run going high (ST_START_DETECTED) -- fired immediately here.
    #   - discharge_current_present + the pulse train must land within
    #     disc_current_on_timeout_ms=3s of full_volts (not settings-exposed).
    #   - field_current_present must land after leaving WAIT_DISCHARGE
    #     (asserting it earlier trips FAULT_DC_BEFORE_START) but within
    #     field_current_on_timeout_ms of entering VERIFY_FIELD -- the 900ms
    #     dwell below is comfortably inside both windows at the pulse rate
    #     configured in rpi_digital_hil_config.json (2 Hz by default).
    #   - motor_synchronized must land within pullout_timeout_ms=5s of
    #     reaching RUNNING.
    def _auto_set(self, step: str, message: str) -> None:
        with self.lock:
            self.auto_step = step
            self.auto_message = message

    def _auto_wait_for(self, name: str, value: int, timeout_s: float, poll_s: float = 0.1) -> str:
        """Returns 'ok', 'timeout', 'cancel', or 'fault'."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            with self.lock:
                if self.auto_cancel:
                    return "cancel"
                if self.hil.read("fault_out"):
                    return "fault"
                if self.hil.read(name) == value:
                    return "ok"
            time.sleep(poll_s)
        return "timeout"

    def _auto_hold(self, seconds: float) -> str:
        """Sleeps while watching for cancel/fault_out. Returns 'ok', 'cancel', or 'fault'."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            with self.lock:
                if self.auto_cancel:
                    return "cancel"
                if self.hil.read("fault_out"):
                    return "fault"
            time.sleep(0.02)
        return "ok"

    def _auto_abort(self, message: str) -> None:
        with self.lock:
            self.stop_pulse_locked()
            self.hil.safe_stop()
            self.auto_step = "failed"
            self.auto_message = message + " Salidas bajadas a reposo (SAFE_STOP aplicado)."
            self.last_message = self.auto_message

    def auto_start(self) -> Dict[str, Any]:
        with self.lock:
            if not self.armed:
                return {"ok": False, "message": "Primero confirma seguridad y arma el sistema"}
            if self.auto_active:
                return {"ok": False, "message": "La secuencia automatica ya esta en curso"}
            self.auto_active = True
            self.auto_cancel = False
            self._auto_set("starting", "Secuencia automatica iniciada.")
            self.auto_thread = threading.Thread(target=self._auto_run, daemon=True)
            self.auto_thread.start()
            return {"ok": True, "message": "Secuencia automatica iniciada"}

    def auto_cancel_request(self) -> Dict[str, Any]:
        with self.lock:
            if not self.auto_active:
                return {"ok": True, "message": "No hay secuencia automatica en curso"}
            self.auto_cancel = True
            return {"ok": True, "message": "Cancelacion solicitada"}

    def _auto_run(self) -> None:
        try:
            self._auto_set("ready", "Aplicando reposo digital (thermal_ok_in, exciter_ready)...")
            with self.lock:
                self.stop_pulse_locked()
                self.hil.write("thermal_ok_in", 1)
                self.hil.write("exciter_ready", 1)
                self.hil.safe_stop()  # zeros full_volts/discharge/field/sync, keeps thermal/exciter untouched

            self._auto_set(
                "wait_relay56k",
                "Esperando relay_56k=1 (READY real de la PZ -- confirma que Ponovo ya inyecta "
                "voltajes/frecuencia validos). Sin limite de tiempo urgente aca.",
            )
            r = self._auto_wait_for("relay_56k", 1, timeout_s=600, poll_s=0.2)
            if r == "cancel":
                self._auto_set("idle", "Cancelado por el operador.")
                return
            if r == "fault":
                self._auto_abort("fault_out activo mientras se esperaba READY.")
                return
            if r == "timeout":
                self._auto_abort("Timeout (10 min) esperando relay_56k=1. Revisar Ponovo/medicion en la PZ.")
                return

            self._auto_set(
                "wait_start",
                "LISTO -- PRESIONA START AHORA EN LA PANTALLA DE LA PZ. "
                "En cuanto se detecte, esta pagina dispara el resto solo.",
            )
            r = self._auto_wait_for("motor_run", 1, timeout_s=600, poll_s=0.05)
            if r == "cancel":
                self._auto_set("idle", "Cancelado por el operador.")
                return
            if r == "fault":
                self._auto_abort("fault_out activo mientras se esperaba START.")
                return
            if r == "timeout":
                self._auto_abort("Timeout (10 min) esperando START (motor_run=1).")
                return

            self._auto_set("full_volts", "START detectado. Aplicando full_volts...")
            with self.lock:
                self.hil.write("full_volts", 1)

            self._auto_set("discharge", "Aplicando corriente de descarga y arrancando el tren de pulsos...")
            with self.lock:
                self.hil.write("discharge_current_present", 1)
            self.restart_pulse_thread_safe()

            self._auto_set("discharge_wait", "Esperando que la frecuencia de descarga se valide (~900ms)...")
            r = self._auto_hold(0.9)
            if r == "cancel":
                self._auto_set("idle", "Cancelado por el operador.")
                return
            if r == "fault":
                self._auto_abort("fault_out activo durante la etapa de descarga (revisar fault_code en la PZ).")
                return

            self._auto_set("field", "Aplicando corriente de campo (field_current_present)...")
            with self.lock:
                self.hil.write("field_current_present", 1)

            self._auto_set("field_wait", "Esperando verificacion de campo (~1.5s)...")
            r = self._auto_hold(1.5)
            if r == "cancel":
                self._auto_set("idle", "Cancelado por el operador.")
                return
            if r == "fault":
                self._auto_abort("fault_out activo durante verificacion de campo.")
                return

            self._auto_set("sync", "Campo verificado. Aplicando sincronismo (motor_synchronized)...")
            with self.lock:
                self.hil.write("motor_synchronized", 1)

            self._auto_set("verify_running", "Verificando RUNNING (relay_fax)...")
            r = self._auto_wait_for("relay_fax", 1, timeout_s=5, poll_s=0.1)
            if r == "fault":
                self._auto_abort("fault_out activo al verificar RUNNING.")
                return
            if r == "ok":
                self._auto_set("done", "RUNNING CONFIRMADO (relay_fax=1). Secuencia completa.")
            else:
                self._auto_set(
                    "done_unconfirmed",
                    "Secuencia completa pero relay_fax no confirmo en 5s -- revisar manualmente en la PZ.",
                )
        except Exception as exc:  # noqa: BLE001 -- last-resort guard so a bug here always leaves the plant safe
            self._auto_abort(f"Error inesperado en la secuencia automatica: {exc}")
        finally:
            with self.lock:
                self.auto_active = False


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
    .auto-banner {{ grid-column: 1 / -1; }}
    .auto-message {{ font-size: 22px; font-weight: 800; padding: 22px; border-radius: 8px; text-align: center; background: #eef2f4; color: #202124; transition: background 0.3s, color 0.3s; }}
    .auto-message.state-wait {{ background: #1d4ed8; color: white; }}
    .auto-message.state-action {{ background: #b45309; color: white; animation: autoPulse 1s infinite alternate; }}
    .auto-message.state-progress {{ background: #0369a1; color: white; }}
    .auto-message.state-done {{ background: var(--green-dark); color: white; }}
    .auto-message.state-failed {{ background: var(--danger); color: white; }}
    @keyframes autoPulse {{ from {{ opacity: 1; }} to {{ opacity: 0.65; }} }}
    .auto-actions {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }}
    .auto-start {{ background: #7c3aed; }}
    .auto-cancel {{ background: #6b7280; }}
    .auto-actions button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
    @media (max-width: 900px) {{
      main {{ grid-template-columns: 1fr; }}
      .grid {{ grid-template-columns: 1fr; }}
      .actions {{ grid-template-columns: 1fr; }}
      .auto-actions {{ grid-template-columns: 1fr; }}
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
    <section class="auto-banner">
      <h2>Secuencia Automatica Guiada</h2>
      <div class="auto-message" id="autoMessage">Presiona ARMAR abajo, despues INICIAR SECUENCIA AUTOMATICA.</div>
      <div class="auto-actions">
        <button class="action auto-start" id="autoStartBtn">INICIAR SECUENCIA AUTOMATICA</button>
        <button class="action auto-cancel" id="autoCancelBtn" disabled>CANCELAR SECUENCIA</button>
      </div>
    </section>
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
    const AUTO_STATE_CLASS = {{
      idle: '', starting: 'state-progress', ready: 'state-progress',
      wait_relay56k: 'state-wait', wait_start: 'state-action',
      full_volts: 'state-progress', discharge: 'state-progress', discharge_wait: 'state-progress',
      field: 'state-progress', field_wait: 'state-progress', sync: 'state-progress',
      verify_running: 'state-progress', done: 'state-done', done_unconfirmed: 'state-done',
      failed: 'state-failed'
    }};
    function paintAuto(auto) {{
      const el = document.getElementById('autoMessage');
      el.textContent = auto.message;
      el.className = 'auto-message ' + (AUTO_STATE_CLASS[auto.step] || '');
      document.getElementById('autoStartBtn').disabled = auto.active;
      document.getElementById('autoCancelBtn').disabled = !auto.active;
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
        paintAuto(s.auto);
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
    document.getElementById('autoStartBtn').onclick = async () => {{
      const out = await api('/api/auto/start');
      if (!out.ok) setMessage(out.message);
      refresh();
    }};
    document.getElementById('autoCancelBtn').onclick = async () => {{
      const out = await api('/api/auto/cancel');
      setMessage(out.message);
      refresh();
    }};
    refresh();
    setInterval(refresh, 250);
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
                elif parsed.path == "/api/auto/start":
                    json_response(self, app.auto_start())
                elif parsed.path == "/api/auto/cancel":
                    json_response(self, app.auto_cancel_request())
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
