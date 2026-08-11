#!/usr/bin/env python3
"""
Raspberry Pi digital HIL console for Nexus Sync.

The Raspberry Pi only drives/reads 3.3 V digital GPIO. It does not generate
analog voltages, currents, DAC outputs, SCR power pulses, or field power.
"""

from __future__ import annotations

import argparse
import json
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional


OUTPUT_SAFE_STATE = {
    "thermal_ok_in": 1,
    "exciter_ready": 1,
    "plant_fault": 0,
    "full_volts": 0,
    "field_current_present": 0,
    "motor_synchronized": 0,
    "discharge_current_present": 0,
    "discharge_extinction_pulse": 0,
}

SAFE_STOP_STATE = {
    "plant_fault": 0,
    "full_volts": 0,
    "field_current_present": 0,
    "motor_synchronized": 0,
    "discharge_current_present": 0,
    "discharge_extinction_pulse": 0,
}

REQUIRED_OUTPUTS = tuple(OUTPUT_SAFE_STATE.keys())
REQUIRED_INPUTS = (
    "motor_run",
    "relay_56k",
    "field_enable",
    "relay_fax",
    "fault_out",
    "scr_enable",
    "fwt_cmd",
    "dst_cmd",
)


class GPIOBackend:
    BCM = "BCM"
    IN = "IN"
    OUT = "OUT"
    PUD_DOWN = "PUD_DOWN"

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self._gpio = None
        self._values: Dict[int, int] = {}
        if not dry_run:
            try:
                import RPi.GPIO as gpio  # type: ignore

                self._gpio = gpio
                self.BCM = gpio.BCM
                self.IN = gpio.IN
                self.OUT = gpio.OUT
                self.PUD_DOWN = gpio.PUD_DOWN
            except ImportError as exc:
                raise RuntimeError(
                    "RPi.GPIO is not available. Run this on Raspberry Pi or use --dry-run."
                ) from exc

    def setmode(self, mode: str) -> None:
        if self._gpio:
            self._gpio.setmode(self.BCM if mode == "BCM" else mode)

    def setup_output(self, pin: int, initial: int) -> None:
        self._values[pin] = int(initial)
        if self._gpio:
            self._gpio.setup(pin, self.OUT, initial=int(initial))

    def setup_input(self, pin: int) -> None:
        self._values.setdefault(pin, 0)
        if self._gpio:
            self._gpio.setup(pin, self.IN, pull_up_down=self.PUD_DOWN)

    def output(self, pin: int, value: int) -> None:
        self._values[pin] = int(value)
        if self._gpio:
            self._gpio.output(pin, int(value))

    def input(self, pin: int) -> int:
        if self._gpio:
            return int(self._gpio.input(pin))
        return int(self._values.get(pin, 0))

    def cleanup(self) -> None:
        if self._gpio:
            self._gpio.cleanup()


@dataclass
class HilConfig:
    gpio_mode: str
    outputs_to_pz: Dict[str, Optional[int]]
    inputs_from_pz: Dict[str, Optional[int]]
    optional_inputs_from_pz: Dict[str, Optional[int]]
    timing_ms: Dict[str, int]


def load_config(path: Path) -> HilConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    return HilConfig(
        gpio_mode=data.get("gpio_mode", "BCM"),
        outputs_to_pz=data.get("outputs_to_pz", {}),
        inputs_from_pz=data.get("inputs_from_pz", {}),
        optional_inputs_from_pz=data.get("optional_inputs_from_pz", {}),
        timing_ms=data.get("timing_ms", {}),
    )


def missing_required(config: HilConfig, mode: str) -> Iterable[str]:
    need_outputs = mode in {"manual", "auto", "fault", "gui", "web"}
    need_inputs = mode in {"manual", "auto", "fault", "monitor", "gui", "web"}
    if need_outputs:
        for name in REQUIRED_OUTPUTS:
            if config.outputs_to_pz.get(name) is None:
                yield f"outputs_to_pz.{name}"
    if need_inputs:
        for name in REQUIRED_INPUTS:
            if config.inputs_from_pz.get(name) is None:
                yield f"inputs_from_pz.{name}"


def validate_config(config: HilConfig, mode: str) -> None:
    missing = list(missing_required(config, mode))
    if missing:
        joined = "\n  - ".join(missing)
        raise SystemExit(
            "GPIO config incomplete. Assign BCM GPIO numbers before running this mode:\n"
            f"  - {joined}"
        )

    pins = []
    for section in (config.outputs_to_pz, config.inputs_from_pz, config.optional_inputs_from_pz):
        pins.extend(pin for pin in section.values() if pin is not None)
    duplicates = sorted({pin for pin in pins if pins.count(pin) > 1})
    if duplicates:
        raise SystemExit(f"GPIO config has duplicate BCM pins: {duplicates}")


class DigitalHil:
    def __init__(self, config: HilConfig, gpio: GPIOBackend) -> None:
        self.config = config
        self.gpio = gpio
        self.running = True

    def pin_out(self, name: str) -> int:
        pin = self.config.outputs_to_pz[name]
        assert pin is not None
        return pin

    def pin_in(self, name: str) -> int:
        pin = self.config.inputs_from_pz[name]
        assert pin is not None
        return pin

    def setup(self) -> None:
        self.gpio.setmode(self.config.gpio_mode)
        for name in REQUIRED_OUTPUTS:
            self.gpio.setup_output(self.pin_out(name), OUTPUT_SAFE_STATE[name])
        for name in REQUIRED_INPUTS:
            self.gpio.setup_input(self.pin_in(name))
        for name, pin in self.config.optional_inputs_from_pz.items():
            if pin is not None:
                self.gpio.setup_input(pin)

    def write(self, name: str, value: int) -> None:
        if name not in REQUIRED_OUTPUTS:
            raise ValueError(f"Unknown output to PZ: {name}")
        self.gpio.output(self.pin_out(name), 1 if value else 0)

    def read(self, name: str) -> int:
        return self.gpio.input(self.pin_in(name))

    def read_optional(self, name: str) -> Optional[int]:
        pin = self.config.optional_inputs_from_pz.get(name)
        if pin is None:
            return None
        return self.gpio.input(pin)

    def safe_stop(self) -> None:
        for name, value in SAFE_STOP_STATE.items():
            self.write(name, value)

    def status(self) -> str:
        outs = " ".join(f"{name}={self.gpio.input(self.pin_out(name))}" for name in REQUIRED_OUTPUTS)
        ins = " ".join(f"{name}={self.read(name)}" for name in REQUIRED_INPUTS)
        return f"RPI->PZ: {outs}\nPZ->RPI: {ins}"

    def require_bench_confirmations(self) -> None:
        checks = [
            "GND comun conectado primero",
            "No hay 5 V conectados a GPIO",
            "Salidas externas de potencia deshabilitadas",
            "Resistencias serie/proteccion instaladas",
        ]
        print("Confirmaciones de seguridad:")
        for item in checks:
            answer = input(f"  {item}? y/n: ").strip().lower()
            if answer != "y":
                raise SystemExit("Abortado por seguridad.")

    def manual(self) -> None:
        print("Manual mode. Commands: set <signal> <0|1>, pulse <signal> [ms], status, safe, quit")
        while self.running:
            cmd = input("hil> ").strip().split()
            if not cmd:
                continue
            try:
                if cmd[0] == "set" and len(cmd) == 3:
                    self.write(cmd[1], int(cmd[2]))
                elif cmd[0] == "pulse" and len(cmd) in {2, 3}:
                    width_ms = int(cmd[2]) if len(cmd) == 3 else 100
                    self.write(cmd[1], 1)
                    time.sleep(width_ms / 1000.0)
                    self.write(cmd[1], 0)
                elif cmd[0] == "status":
                    print(self.status())
                elif cmd[0] == "safe":
                    self.safe_stop()
                elif cmd[0] in {"quit", "exit"}:
                    return
                else:
                    print("Unknown command.")
            except Exception as exc:
                print(f"ERROR: {exc}")

    def monitor(self) -> None:
        refresh = self.config.timing_ms.get("status_refresh", 500) / 1000.0
        while self.running:
            print(self.status())
            time.sleep(refresh)

    def wait_input(self, name: str, value: int, message: str) -> None:
        print(message)
        refresh = self.config.timing_ms.get("status_refresh", 500) / 1000.0
        while self.running and self.read(name) != value:
            if self.read("fault_out"):
                print("fault_out active while waiting.")
            time.sleep(refresh)

    def auto_sequence(self) -> None:
        self.write("thermal_ok_in", 1)
        self.write("exciter_ready", 1)
        self.safe_stop()
        print("IDLE/READY outputs applied.")

        self.wait_input("motor_run", 1, "WAIT_START: waiting for PZ motor_run=1")
        print("PZ requested start.")

        answer = input("Voltajes y corrientes externos ya estan inyectados y estables? y/n: ").strip().lower()
        if answer != "y":
            raise SystemExit("Abortado: no hay confirmacion de inyeccion analogica externa.")

        self.write("full_volts", 1)
        time.sleep(self.config.timing_ms.get("full_volts_delay", 1000) / 1000.0)

        self.wait_input("field_enable", 1, "FIELD_PRESENT: waiting for PZ field_enable=1")
        time.sleep(self.config.timing_ms.get("field_present_delay", 1000) / 1000.0)
        self.write("field_current_present", 1)

        answer = input("La inyeccion externa representa condicion sincronizada? y/n: ").strip().lower()
        if answer != "y":
            raise SystemExit("Abortado: no hay confirmacion de sincronismo.")
        time.sleep(self.config.timing_ms.get("sync_delay", 1000) / 1000.0)
        self.write("motor_synchronized", 1)

        refresh = self.config.timing_ms.get("status_refresh", 500) / 1000.0
        print("RUNNING: Ctrl+C or q then Enter to SAFE_STOP.")
        while self.running:
            print(self.status())
            if self.read("fault_out"):
                print("fault_out active.")
            time.sleep(refresh)

    def fault_test(self) -> None:
        print("FAULT_TEST: asserting plant_fault=1")
        self.write("plant_fault", 1)
        deadline = time.time() + 5.0
        while time.time() < deadline and self.running:
            print(self.status())
            if self.read("fault_out"):
                print("PASS: fault_out responded.")
                break
            time.sleep(self.config.timing_ms.get("status_refresh", 500) / 1000.0)
        input("Press Enter for SAFE_STOP.")
        self.safe_stop()


class DigitalHilGui:
    def __init__(self, hil: DigitalHil) -> None:
        import tkinter as tk
        from tkinter import messagebox

        self.tk = tk
        self.messagebox = messagebox
        self.hil = hil
        self.root = tk.Tk()
        self.root.title("Nexus Sync Digital HIL")
        self.root.geometry("1024x600")
        self.root.configure(bg="#111827")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.output_buttons: Dict[str, object] = {}
        self.input_labels: Dict[str, object] = {}
        self.optional_labels: Dict[str, object] = {}
        self.status_var = tk.StringVar(value="SAFE outputs initialized")
        self.discharge_pulse_running = False
        self.discharge_pulse_level = 0
        self._build()

    def _build(self) -> None:
        tk = self.tk
        title = tk.Label(
            self.root,
            text="Nexus Sync - Raspberry Pi Digital HIL",
            bg="#111827",
            fg="#f9fafb",
            font=("Arial", 22, "bold"),
            pady=10,
        )
        title.pack(fill="x")

        body = tk.Frame(self.root, bg="#111827")
        body.pack(fill="both", expand=True, padx=12, pady=8)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        outputs = tk.LabelFrame(
            body,
            text="Raspberry -> PZ",
            bg="#1f2937",
            fg="#f9fafb",
            font=("Arial", 16, "bold"),
            padx=10,
            pady=10,
        )
        outputs.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        for idx, name in enumerate(REQUIRED_OUTPUTS):
            row = idx // 2
            col = idx % 2
            btn = tk.Button(
                outputs,
                text=self._button_text(name),
                font=("Arial", 15, "bold"),
                width=26,
                height=3,
                command=lambda signal=name: self.toggle_output(signal),
                relief="raised",
                bd=4,
            )
            btn.grid(row=row, column=col, padx=8, pady=8, sticky="ew")
            self.output_buttons[name] = btn

        pulse_btn = tk.Button(
            outputs,
            text="discharge_extinction_pulse\nPULSE OFF",
            font=("Arial", 15, "bold"),
            width=26,
            height=3,
            command=self.toggle_discharge_pulse,
            relief="raised",
            bd=4,
        )
        pulse_btn.grid(row=4, column=0, columnspan=2, padx=8, pady=8, sticky="ew")
        self.output_buttons["_discharge_pulse_train"] = pulse_btn

        right = tk.Frame(body, bg="#111827")
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        inputs = tk.LabelFrame(
            right,
            text="PZ -> Raspberry",
            bg="#1f2937",
            fg="#f9fafb",
            font=("Arial", 16, "bold"),
            padx=10,
            pady=10,
        )
        inputs.grid(row=0, column=0, sticky="nsew", pady=(0, 8))

        for idx, name in enumerate(REQUIRED_INPUTS):
            label = tk.Label(
                inputs,
                text=f"{name}: 0",
                bg="#374151",
                fg="#f9fafb",
                anchor="w",
                font=("Arial", 14, "bold"),
                padx=10,
                pady=8,
            )
            label.grid(row=idx, column=0, sticky="ew", pady=3)
            self.input_labels[name] = label

        optional = tk.LabelFrame(
            right,
            text="Optional Monitor",
            bg="#1f2937",
            fg="#f9fafb",
            font=("Arial", 14, "bold"),
            padx=10,
            pady=8,
        )
        optional.grid(row=1, column=0, sticky="nsew")

        opt_row = 0
        for name, pin in self.hil.config.optional_inputs_from_pz.items():
            if pin is None:
                continue
            label = tk.Label(
                optional,
                text=f"{name}: 0",
                bg="#374151",
                fg="#f9fafb",
                anchor="w",
                font=("Arial", 12, "bold"),
                padx=8,
                pady=5,
            )
            label.grid(row=opt_row, column=0, sticky="ew", pady=2)
            self.optional_labels[name] = label
            opt_row += 1

        footer = tk.Frame(self.root, bg="#111827")
        footer.pack(fill="x", padx=12, pady=(0, 12))
        safe = tk.Button(
            footer,
            text="SAFE_STOP",
            command=self.safe_stop,
            bg="#b91c1c",
            fg="#ffffff",
            activebackground="#7f1d1d",
            activeforeground="#ffffff",
            font=("Arial", 22, "bold"),
            height=2,
            bd=4,
        )
        safe.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ready = tk.Button(
            footer,
            text="READY",
            command=self.ready_state,
            bg="#166534",
            fg="#ffffff",
            activebackground="#14532d",
            activeforeground="#ffffff",
            font=("Arial", 20, "bold"),
            height=2,
            bd=4,
        )
        ready.pack(side="left", fill="x", expand=True, padx=(0, 8))

        close = tk.Button(
            footer,
            text="EXIT",
            command=self.close,
            bg="#374151",
            fg="#ffffff",
            activebackground="#1f2937",
            activeforeground="#ffffff",
            font=("Arial", 20, "bold"),
            height=2,
            bd=4,
        )
        close.pack(side="left", fill="x", expand=True)

        status = tk.Label(
            self.root,
            textvariable=self.status_var,
            bg="#030712",
            fg="#f9fafb",
            anchor="w",
            font=("Arial", 12),
            padx=12,
            pady=6,
        )
        status.pack(fill="x")
        self.refresh()

    def _output_value(self, name: str) -> int:
        return self.hil.gpio.input(self.hil.pin_out(name))

    def _button_text(self, name: str) -> str:
        return f"{name}\n{'ON' if self._output_value(name) else 'OFF'}"

    def _paint_output(self, name: str) -> None:
        btn = self.output_buttons[name]
        value = self._output_value(name)
        bg = "#15803d" if value else "#4b5563"
        active = "#166534" if value else "#374151"
        btn.configure(text=self._button_text(name), bg=bg, fg="#ffffff", activebackground=active)

    def _paint_pulse_train(self) -> None:
        btn = self.output_buttons["_discharge_pulse_train"]
        bg = "#0369a1" if self.discharge_pulse_running else "#4b5563"
        text = "discharge_extinction_pulse\nPULSE ON" if self.discharge_pulse_running else "discharge_extinction_pulse\nPULSE OFF"
        btn.configure(text=text, bg=bg, fg="#ffffff", activebackground="#075985")

    def _paint_input(self, name: str, label: object, value: int) -> None:
        bg = "#15803d" if value else "#374151"
        if name == "fault_out" and value:
            bg = "#b91c1c"
        label.configure(text=f"{name}: {value}", bg=bg)

    def toggle_output(self, name: str) -> None:
        if name == "discharge_extinction_pulse" and self.discharge_pulse_running:
            self.status_var.set("Stop pulse train before manual toggle")
            return
        if name == "plant_fault":
            ok = self.messagebox.askyesno("Confirm plant_fault", "Assert plant_fault=1?")
            if not ok:
                return
        next_value = 0 if self._output_value(name) else 1
        self.hil.write(name, next_value)
        self.status_var.set(f"{name} set to {next_value}")
        self.refresh()

    def toggle_discharge_pulse(self) -> None:
        self.discharge_pulse_running = not self.discharge_pulse_running
        if self.discharge_pulse_running:
            self.discharge_pulse_level = 0
            self.status_var.set("discharge_extinction_pulse pulse train started")
            self._schedule_discharge_pulse()
        else:
            self.discharge_pulse_level = 0
            self.hil.write("discharge_extinction_pulse", 0)
            self.status_var.set("discharge_extinction_pulse pulse train stopped")
        self.refresh()

    def _schedule_discharge_pulse(self) -> None:
        if not self.discharge_pulse_running:
            self.hil.write("discharge_extinction_pulse", 0)
            return
        hz = max(0.5, float(self.hil.config.timing_ms.get("discharge_pulse_hz", 5)))
        half_period_ms = int(500.0 / hz)
        self.discharge_pulse_level = 0 if self.discharge_pulse_level else 1
        self.hil.write("discharge_extinction_pulse", self.discharge_pulse_level)
        self.root.after(max(20, half_period_ms), self._schedule_discharge_pulse)

    def safe_stop(self) -> None:
        self.discharge_pulse_running = False
        self.discharge_pulse_level = 0
        self.hil.safe_stop()
        self.status_var.set("SAFE_STOP applied")
        self.refresh()

    def ready_state(self) -> None:
        self.hil.write("thermal_ok_in", 1)
        self.hil.write("exciter_ready", 1)
        self.hil.safe_stop()
        self.status_var.set("READY state applied")
        self.refresh()

    def refresh(self) -> None:
        for name in REQUIRED_OUTPUTS:
            self._paint_output(name)
        self._paint_pulse_train()
        for name, label in self.input_labels.items():
            self._paint_input(name, label, self.hil.read(name))
        for name, label in self.optional_labels.items():
            value = self.hil.read_optional(name)
            if value is not None:
                self._paint_input(name, label, value)
        self.root.after(self.hil.config.timing_ms.get("status_refresh", 500), self.refresh)

    def show_safety_prompt(self) -> None:
        text = (
            "Antes de habilitar salidas confirma:\n\n"
            "- GND comun conectado primero\n"
            "- No hay 5 V en GPIO\n"
            "- Potencia externa deshabilitada\n"
            "- Resistencias serie/proteccion instaladas\n\n"
            "Continuar?"
        )
        if not self.messagebox.askyesno("Safety confirmation", text):
            raise SystemExit("Abortado por seguridad.")

    def run(self) -> None:
        self.show_safety_prompt()
        self.root.mainloop()

    def close(self) -> None:
        self.safe_stop()
        self.hil.running = False
        self.root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="Nexus Sync Raspberry Pi digital HIL")
    parser.add_argument("--config", default="rpi_digital_hil_config.json")
    parser.add_argument("--mode", choices=("manual", "auto", "monitor", "fault", "gui"), required=True)
    parser.add_argument("--dry-run", action="store_true", help="Use mock GPIO for syntax/console testing")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    validate_config(config, args.mode)
    gpio = GPIOBackend(dry_run=args.dry_run)
    hil = DigitalHil(config, gpio)

    def stop_handler(_signum, _frame) -> None:
        hil.running = False
        print("\nSAFE_STOP requested.")

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    try:
        hil.setup()
        if args.mode not in {"monitor", "gui"}:
            hil.require_bench_confirmations()
        if args.mode == "manual":
            hil.manual()
        elif args.mode == "auto":
            hil.auto_sequence()
        elif args.mode == "monitor":
            hil.monitor()
        elif args.mode == "fault":
            hil.fault_test()
        elif args.mode == "gui":
            DigitalHilGui(hil).run()
    finally:
        if args.mode != "monitor":
            hil.safe_stop()
        print("Final state:")
        try:
            print(hil.status())
        finally:
            gpio.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
