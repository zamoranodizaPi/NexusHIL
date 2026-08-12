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
        """Always leaves the sequence view at a clean, unambiguous step 0.

        Previously auto_step/auto_message only reset to idle when
        auto_active was still True. If a sequence had already finished
        (done/failed) and the operator pressed SAFE_STOP afterwards, the
        physical outputs correctly went to reposo but the guided-sequence
        stepper kept showing the old finished/failed state -- looked like
        SAFE_STOP "did nothing". SAFE_STOP must unconditionally reset the
        sequence view, regardless of whether a run was in progress.
        """
        with self.lock:
            self.auto_cancel = True
            self.stop_pulse_locked()
            self.hil.safe_stop()
            self.armed = False
            self.auto_active = False
            self.auto_step = "idle"
            self.auto_message = "Reiniciado a estado seguro. Listo para una nueva secuencia."
            self.last_message = "SAFE_STOP aplicado; sistema reiniciado y desarmado"
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


STEPPER_STEPS = (
    ("Seguridad", "Checklist + ARMAR"),
    ("Reposo digital", "thermal_ok_in / exciter_ready"),
    ("READY real de la PZ", "relay_56k = 1"),
    ("START", "motor_run = 1 (fisico, en la PZ)"),
    ("Full Volts", "full_volts = 1"),
    ("Descarga", "discharge_current_present + tren de pulso"),
    ("Campo", "field_current_present = 1"),
    ("Sincronismo", "motor_synchronized = 1"),
    ("RUNNING", "relay_fax = 1"),
)


def html_page() -> bytes:
    manual_controls = "\n".join(
        f'<button class="signal" data-signal="{name}"><span>{name}</span><strong id="out-{name}">0</strong></button>'
        for name in REQUIRED_OUTPUTS
    )
    input_chips = "\n".join(
        f'<div class="chip" id="in-{name}"><span class="cdot"></span><span class="cname">{name}</span><span class="cval">0</span></div>'
        for name in REQUIRED_INPUTS
    )
    optional_chips = "\n".join(
        f'<div class="chip optional" id="opt-{name}"><span class="cdot"></span><span class="cname">{name}</span><span class="cval">0</span></div>'
        for name in ("field_pwm", "sync_pulse", "scr_gate_g1", "scr_gate_g2", "scr_gate_g3", "scr_gate_g4", "scr_gate_g5", "scr_gate_g6")
    )
    stepper_rows = "\n".join(
        f'<div class="step-row" id="srow-{i}"><span class="step-dot" id="sdot-{i}">{i}</span>'
        f'<div class="step-body"><div class="step-label">{label}</div><div class="step-sub">{sub}</div></div></div>'
        for i, (label, sub) in enumerate(STEPPER_STEPS)
    )
    steps_json = json.dumps([label for label, _sub in STEPPER_STEPS])
    html = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nexus HIL &middot; SIEZA</title>
  <style>
    :root {{
      --green: #0b7a34; --green2: #12a052; --green3: #2fc274;
      --bg: #101312; --ink: #0c0f0e; --panel: #1b1f1d; --panel2: #151817; --panel3: #212925;
      --line: #2c332f; --line2: #3a433c; --text: #eef4f0; --muted: #9aa39e; --muted2: #7c8781;
      --warn: #d6a700; --warn2: #ffd74b; --fault: #c9302c; --fault2: #ff8b88;
      --blue: #2f80ed; --blue2: #8ec5ff; --ok2: #76e09a; --gray: #7a7f7c;
      --radius: 9px; --shadow: 0 1px 0 rgba(255,255,255,.02) inset, 0 10px 26px -14px rgba(0,0,0,.55);
      --font-body: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
      --font-display: "Segoe UI Semibold", -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
      --font-mono: ui-monospace, "Cascadia Code", "Consolas", "SFMono-Regular", Menlo, monospace;
    }}
    * {{ box-sizing: border-box }}
    html, body {{ margin: 0; padding: 0 }}
    body {{ font-family: var(--font-body); background: var(--bg); color: var(--text); font-size: 14.5px; line-height: 1.5; -webkit-font-smoothing: antialiased }}
    h1, h2, h3, h4 {{ font-family: var(--font-display); margin: 0 }}
    ::selection {{ background: var(--green2); color: #fff }}

    .top {{
      display: flex; align-items: center; gap: 16px; padding: 14px 22px;
      background: linear-gradient(180deg, var(--ink), var(--bg)); border-bottom: 1px solid var(--line);
      position: sticky; top: 0; z-index: 5;
    }}
    .top .logo-chip {{ width: 40px; height: 40px; border-radius: 8px; background: #fff; display: grid; place-items: center; flex: none; box-shadow: var(--shadow) }}
    .top .logo-chip img {{ width: 30px; height: 30px; object-fit: contain }}
    .top .brand-text h1 {{ font-size: 17px; letter-spacing: .06em; font-weight: 700 }}
    .top .brand-text p {{ margin: 2px 0 0; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; font-weight: 600 }}
    .top-right {{ margin-left: auto; display: flex; align-items: center; gap: 10px }}
    .clock {{ font-family: var(--font-mono); font-size: 12.5px; color: var(--muted); font-variant-numeric: tabular-nums }}

    .badge {{ display: inline-block; padding: 5px 9px; border-radius: 5px; font-size: 11.5px; font-weight: 700; border: 1px solid var(--line); letter-spacing: .03em }}
    .badge.ok {{ background: #123920; color: var(--ok2); border-color: transparent }}
    .badge.warn {{ background: #382f0d; color: var(--warn2); border-color: transparent }}
    .badge.fault {{ background: #3b1514; color: var(--fault2); border-color: transparent }}
    .badge.muted {{ color: var(--muted) }}

    main {{ max-width: 1180px; margin: 0 auto; padding: 22px; display: grid; gap: 16px }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); padding: 18px 20px }}
    .panel h3 {{ font-size: 12.5px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); font-weight: 700; margin: 0 0 14px }}

    /* Guided sequence */
    .stepper-v {{ display: flex; flex-direction: column }}
    .step-row {{ display: flex; gap: 14px; padding: 9px 0; position: relative }}
    .step-row:not(:last-child)::after {{ content: ''; position: absolute; left: 13px; top: 32px; bottom: -9px; width: 2px; background: var(--line) }}
    .step-row.done:not(:last-child)::after {{ background: var(--green2) }}
    .step-dot {{
      width: 28px; height: 28px; border-radius: 50%; border: 2px solid var(--line); flex: none;
      display: grid; place-items: center; font-size: 12px; font-weight: 700; color: var(--muted);
      background: var(--panel2); position: relative; z-index: 1;
    }}
    .step-dot.active {{ background: var(--blue); border-color: var(--blue); color: #fff; animation: stepPulse 1.7s ease-in-out infinite }}
    .step-dot.done {{ background: var(--green2); border-color: var(--green2); color: #fff }}
    .step-dot.fault {{ background: var(--fault); border-color: var(--fault); color: #fff }}
    .step-dot.warn {{ background: var(--warn2); border-color: var(--warn2); color: var(--ink) }}
    @keyframes stepPulse {{ 0%,100% {{ box-shadow: 0 0 0 4px rgba(47,128,237,.2) }} 50% {{ box-shadow: 0 0 0 9px rgba(47,128,237,.05) }} }}
    .step-body {{ flex: 1; padding-top: 2px; opacity: .55 }}
    .step-row.active .step-body, .step-row.done .step-body, .step-row.fault .step-body {{ opacity: 1 }}
    .step-label {{ font-weight: 700; font-size: 13.5px }}
    .step-sub {{ font-size: 11px; color: var(--muted2); margin-top: 2px; font-family: var(--font-mono) }}
    .step-row.fault .step-label {{ color: var(--fault2) }}

    .cta {{ margin-top: 16px; padding: 16px; border-radius: var(--radius); border: 1px solid var(--line); background: var(--panel2) }}
    .cta .cta-msg {{ font-size: 14px; margin-bottom: 12px; color: var(--text) }}
    .cta.action-required {{ border-color: var(--warn2); background: #2a2308; animation: ctaGlow 1.6s ease-in-out infinite }}
    @keyframes ctaGlow {{ 0%,100% {{ box-shadow: 0 0 0 0 rgba(255,215,75,.18) }} 50% {{ box-shadow: 0 0 0 7px rgba(255,215,75,.05) }} }}
    .cta.done {{ border-color: var(--green3) }}
    .cta.failed {{ border-color: var(--fault2) }}

    .checks {{ display: grid; gap: 8px; margin-bottom: 14px }}
    .checks label {{ display: flex; align-items: center; gap: 9px; color: var(--muted); font-size: 13px }}
    .checks input {{ width: 16px; height: 16px; accent-color: var(--green2) }}

    button {{ font-family: var(--font-body); background: #203027; color: var(--text); border: 1px solid #375041; border-radius: 6px; padding: 10px 14px; cursor: pointer; font-size: 13.5px; font-weight: 600 }}
    button:hover {{ border-color: var(--green2) }}
    button:disabled {{ opacity: .4; cursor: not-allowed }}
    .btn-row {{ display: flex; gap: 10px; flex-wrap: wrap }}
    .btn-primary {{ background: var(--green); border-color: var(--green3); color: #fff; padding: 12px 18px; font-size: 14px }}
    .btn-primary:hover {{ border-color: var(--green3) }}
    .btn-danger {{ background: #3b1514; border-color: var(--fault); color: var(--fault2) }}
    .btn-danger:hover {{ border-color: var(--fault2) }}
    .btn-ghost {{ background: transparent; border-color: var(--line) }}

    .safe-stop-dock {{ display: flex; justify-content: flex-end; align-items: center; gap: 12px }}
    .safe-stop-hint {{ font-size: 11px; color: var(--muted2); text-align: right }}
    .reset-toast {{
      position: fixed; top: 18px; right: 18px; z-index: 20; padding: 12px 18px; border-radius: 7px;
      background: var(--panel3); border: 1px solid var(--green3); color: var(--ok2); font-weight: 700;
      font-size: 13px; box-shadow: var(--shadow); opacity: 0; transform: translateY(-8px);
      transition: opacity .2s, transform .2s; pointer-events: none;
    }}
    .reset-toast.show {{ opacity: 1; transform: translateY(0) }}

    /* Live status chips */
    .chip-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 9px }}
    .chip {{ display: flex; align-items: center; gap: 9px; padding: 10px 12px; border-radius: 7px; background: var(--panel2); border: 1px solid var(--line) }}
    .chip .cdot {{ width: 9px; height: 9px; border-radius: 50%; flex: none; background: var(--gray) }}
    .chip .cdot.on {{ background: var(--ok2); box-shadow: 0 0 0 3px rgba(118,224,154,.18) }}
    .chip .cdot.on.fault {{ background: var(--fault2); box-shadow: 0 0 0 3px rgba(255,139,136,.22) }}
    .chip .cname {{ font-size: 12px; font-weight: 600; font-family: var(--font-mono) }}
    .chip .cval {{ margin-left: auto; font-family: var(--font-mono); font-size: 11px; color: var(--muted2) }}
    .subgroup-label {{ font-size: 10.5px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted2); font-weight: 700; margin: 16px 0 8px }}
    .subgroup-label:first-child {{ margin-top: 0 }}

    /* Manual/diagnostics panel */
    details.panel summary {{ cursor: pointer; list-style: none; display: flex; align-items: center; justify-content: space-between; font-size: 12.5px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); font-weight: 700 }}
    details.panel summary::-webkit-details-marker {{ display: none }}
    details.panel summary::after {{ content: '\\25be'; color: var(--muted2); font-size: 13px }}
    details.panel[open] summary {{ margin-bottom: 14px }}
    .manual-hint {{ font-size: 12px; color: var(--muted2); margin-bottom: 14px }}
    .manual-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 9px; margin-bottom: 14px }}
    button.signal {{ text-align: left; display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 10px 12px }}
    button.signal strong {{ min-width: 26px; text-align: center; padding: 3px 6px; border-radius: 4px; background: var(--panel3); color: var(--muted) }}
    button.signal.on {{ border-color: var(--green3) }}
    button.signal.on strong {{ background: var(--green2); color: #fff }}
    .disabled-note {{ font-size: 11.5px; color: var(--warn2); margin-bottom: 10px }}

    footer {{ max-width: 1180px; margin: 0 auto; padding: 6px 22px 30px; color: var(--muted2); font-size: 11.5px }}

    @media (max-width: 640px) {{
      .top {{ padding: 12px 14px }}
      main {{ padding: 14px }}
    }}
  </style>
</head>
<body>
  <header class="top">
    <div class="logo-chip"><img src="/assets/branding/sieza_logo_light.png" alt="SIEZA"></div>
    <div class="brand-text">
      <h1>NEXUS HIL</h1>
      <p>Simulador digital &middot; Raspberry Pi</p>
    </div>
    <div class="top-right">
      <span id="armedBadge" class="badge muted">DESARMADO</span>
      <span id="conn" class="badge warn">CONECTANDO</span>
      <span id="clock" class="clock">--:--:--</span>
    </div>
  </header>
  <main>
    <section class="panel">
      <h3>Secuencia guiada</h3>
      <div class="stepper-v">{stepper_rows}</div>

      <div class="cta" id="ctaSecurity">
        <div class="cta-msg">Confirma seguridad antes de armar el sistema.</div>
        <div class="checks">
          <label><input type="checkbox" id="gnd"> GND com&uacute;n conectado primero</label>
          <label><input type="checkbox" id="no_5v"> No hay 5&nbsp;V en GPIO</label>
          <label><input type="checkbox" id="power_disabled"> Potencia externa deshabilitada</label>
          <label><input type="checkbox" id="series_resistors"> Resistencias serie/protecci&oacute;n instaladas</label>
        </div>
        <div class="btn-row"><button class="btn-primary" id="armBtn" disabled>ARMAR SISTEMA</button></div>
      </div>

      <div class="cta" id="ctaReady" style="display:none">
        <div class="cta-msg">Sistema armado. Listo para iniciar la secuencia autom&aacute;tica.</div>
        <div class="btn-row"><button class="btn-primary" id="autoStartBtn">INICIAR SECUENCIA AUTOM&Aacute;TICA</button></div>
      </div>

      <div class="cta" id="ctaRunning" style="display:none">
        <div class="cta-msg" id="ctaRunningMsg">&nbsp;</div>
        <div class="btn-row"><button class="btn-danger" id="autoCancelBtn">CANCELAR SECUENCIA</button></div>
      </div>

      <div class="cta" id="ctaEnd" style="display:none">
        <div class="cta-msg" id="ctaEndMsg">&nbsp;</div>
        <div class="btn-row"><button class="btn-primary" id="autoRetryBtn">REINICIAR SECUENCIA</button></div>
      </div>

      <p class="manual-hint" id="statusLine">Cargando...</p>
      <div class="safe-stop-dock">
        <span class="safe-stop-hint">Baja todas las salidas a reposo<br>y reinicia la secuencia al paso 0</span>
        <button class="btn-danger" id="safeBtn">SAFE_STOP / REINICIAR</button>
      </div>
    </section>
    <div class="reset-toast" id="resetToast">&#10003; Reiniciado a estado seguro (paso 0)</div>

    <section class="panel">
      <h3>Estado en vivo</h3>
      <div class="subgroup-label">PZ &rarr; Raspberry</div>
      <div class="chip-grid">{input_chips}</div>
      <div class="subgroup-label">Monitoreo opcional</div>
      <div class="chip-grid">{optional_chips}</div>
    </section>

    <details class="panel manual-panel">
      <summary>Modo manual / diagn&oacute;stico</summary>
      <p class="manual-hint">Control directo de cada se&ntilde;al Raspberry&nbsp;&rarr;&nbsp;PZ. Uso normal: solo la secuencia guiada de arriba. Esto es para depuraci&oacute;n puntual (ver "Diagnostico si no entra READY" en la documentaci&oacute;n).</p>
      <p class="disabled-note" id="manualDisabledNote" style="display:none">Deshabilitado mientras la secuencia autom&aacute;tica est&aacute; activa.</p>
      <div class="manual-grid">{manual_controls}</div>
      <div class="btn-row">
        <button class="btn-ghost" id="readyBtn">Forzar READY</button>
        <button class="btn-ghost" id="pulseBtn">discharge_extinction_pulse PULSE OFF</button>
      </div>
    </details>
  </main>
  <footer>{COPYRIGHT}</footer>
  <script>
    const STEP_LABELS = {steps_json};
    const AUTO_STEP_INDEX = {{
      starting: 1, ready: 1, wait_relay56k: 2, wait_start: 3, full_volts: 4,
      discharge: 5, discharge_wait: 5, field: 6, field_wait: 6, sync: 7,
      verify_running: 8, done: 8, done_unconfirmed: 8
    }};
    let lastActiveIndex = 0;

    async function api(path, payload) {{
      const res = await fetch(path, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(payload || {{}})
      }});
      return await res.json();
    }}

    function paintStepper(auto, armed) {{
      const idx = AUTO_STEP_INDEX[auto.step];
      if (idx !== undefined) lastActiveIndex = idx;
      const isDoneFinal = auto.step === 'done' || auto.step === 'done_unconfirmed';
      const isFailed = auto.step === 'failed';
      for (let i = 0; i < STEP_LABELS.length; i++) {{
        const dot = document.getElementById('sdot-' + i);
        const row = document.getElementById('srow-' + i);
        let state = 'pending';
        if (i === 0) {{
          state = armed ? 'done' : 'active';
        }} else if (isFailed && i === lastActiveIndex) {{
          state = 'fault';
        }} else if (isDoneFinal && i === 8) {{
          state = auto.step === 'done' ? 'done' : 'warn';
        }} else if (auto.active && idx !== undefined && i === idx) {{
          state = 'active';
        }} else if (idx !== undefined && i < idx) {{
          state = 'done';
        }} else if (isFailed && i < lastActiveIndex) {{
          state = 'done';
        }} else if (isDoneFinal && i < 8) {{
          state = 'done';
        }}
        dot.className = 'step-dot ' + state;
        row.className = 'step-row ' + state;
        dot.textContent = state === 'done' ? '\\u2713' : (state === 'fault' ? '!' : String(i));
      }}
    }}

    function paintCta(auto, armed) {{
      const secBox = document.getElementById('ctaSecurity');
      const readyBox = document.getElementById('ctaReady');
      const runBox = document.getElementById('ctaRunning');
      const endBox = document.getElementById('ctaEnd');
      secBox.style.display = 'none'; readyBox.style.display = 'none';
      runBox.style.display = 'none'; endBox.style.display = 'none';
      runBox.classList.remove('action-required');

      if (!armed) {{ secBox.style.display = 'block'; return; }}
      if (auto.active) {{
        runBox.style.display = 'block';
        document.getElementById('ctaRunningMsg').textContent = auto.message;
        if (auto.step === 'wait_start') runBox.classList.add('action-required');
        return;
      }}
      if (auto.step === 'done' || auto.step === 'done_unconfirmed' || auto.step === 'failed') {{
        endBox.style.display = 'block';
        endBox.className = 'cta ' + (auto.step === 'failed' ? 'failed' : 'done');
        document.getElementById('ctaEndMsg').textContent = auto.message;
        return;
      }}
      readyBox.style.display = 'block';
    }}

    function paintChip(id, value, isFault) {{
      const el = document.getElementById(id);
      if (!el) return;
      const dot = el.querySelector('.cdot');
      dot.classList.toggle('on', !!value);
      dot.classList.toggle('fault', !!value && isFault);
      el.querySelector('.cval').textContent = value == null ? '-' : value;
    }}

    function checksComplete() {{
      return ['gnd', 'no_5v', 'power_disabled', 'series_resistors']
        .every(id => document.getElementById(id).checked);
    }}
    for (const id of ['gnd', 'no_5v', 'power_disabled', 'series_resistors']) {{
      document.getElementById(id).addEventListener('change', () => {{
        document.getElementById('armBtn').disabled = !checksComplete();
      }});
    }}

    async function refresh() {{
      try {{
        const res = await fetch('/api/status');
        const s = await res.json();
        document.getElementById('conn').className = 'badge ok';
        document.getElementById('conn').textContent = 'CONECTADO';
        document.getElementById('armedBadge').className = 'badge ' + (s.armed ? 'ok' : 'muted');
        document.getElementById('armedBadge').textContent = s.armed ? 'ARMADO' : 'DESARMADO';
        document.getElementById('statusLine').textContent = s.message;

        for (const [name, value] of Object.entries(s.outputs_to_pz)) {{
          const btn = document.querySelector(`[data-signal="${{name}}"]`);
          if (btn) {{
            btn.classList.toggle('on', !!value);
            btn.querySelector('strong').textContent = value;
            btn.disabled = !s.armed || s.auto.active;
          }}
        }}
        for (const [name, value] of Object.entries(s.inputs_from_pz)) {{
          paintChip(`in-${{name}}`, value, name === 'fault_out');
        }}
        for (const [name, value] of Object.entries(s.optional_inputs_from_pz)) {{
          paintChip(`opt-${{name}}`, value, false);
        }}
        document.getElementById('manualDisabledNote').style.display = s.auto.active ? 'block' : 'none';

        document.getElementById('pulseBtn').textContent = s.pulse.running
          ? `discharge_extinction_pulse PULSE ON (${{s.pulse.hz}} Hz)`
          : `discharge_extinction_pulse PULSE OFF (${{s.pulse.hz}} Hz)`;
        document.getElementById('pulseBtn').dataset.running = s.pulse.running ? '1' : '0';
        document.getElementById('pulseBtn').disabled = !s.armed || s.auto.active;
        document.getElementById('readyBtn').disabled = !s.armed || s.auto.active;
        document.getElementById('armBtn').disabled = s.armed || !checksComplete();

        paintStepper(s.auto, s.armed);
        paintCta(s.auto, s.armed);
      }} catch (err) {{
        document.getElementById('conn').className = 'badge fault';
        document.getElementById('conn').textContent = 'SIN CONEXION';
      }}
    }}

    document.getElementById('armBtn').onclick = async () => {{
      await api('/api/arm', {{
        gnd: document.getElementById('gnd').checked,
        no_5v: document.getElementById('no_5v').checked,
        power_disabled: document.getElementById('power_disabled').checked,
        series_resistors: document.getElementById('series_resistors').checked
      }});
      refresh();
    }};
    document.getElementById('readyBtn').onclick = async () => {{ await api('/api/ready'); refresh(); }};
    document.getElementById('safeBtn').onclick = async () => {{
      await api('/api/safe_stop');
      await refresh();
      const toast = document.getElementById('resetToast');
      toast.classList.add('show');
      setTimeout(() => toast.classList.remove('show'), 2200);
    }};
    document.getElementById('pulseBtn').onclick = async (ev) => {{
      const running = ev.currentTarget.dataset.running === '1';
      await api('/api/pulse', {{ running: !running }});
      refresh();
    }};
    for (const btn of document.querySelectorAll('button.signal')) {{
      btn.onclick = async () => {{
        const signal = btn.dataset.signal;
        const current = btn.classList.contains('on') ? 1 : 0;
        if (signal === 'plant_fault' && current === 0 && !confirm('Confirmar plant_fault=1')) return;
        await api('/api/output', {{ signal, value: current ? 0 : 1 }});
        refresh();
      }};
    }}
    document.getElementById('autoStartBtn').onclick = async () => {{ await api('/api/auto/start'); refresh(); }};
    document.getElementById('autoCancelBtn').onclick = async () => {{ await api('/api/auto/cancel'); refresh(); }};
    document.getElementById('autoRetryBtn').onclick = async () => {{ await api('/api/auto/start'); refresh(); }};

    function tickClock() {{
      document.getElementById('clock').textContent = new Date().toLocaleTimeString('es-MX', {{ hour12: false }});
    }}
    tickClock();
    setInterval(tickClock, 1000);
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
