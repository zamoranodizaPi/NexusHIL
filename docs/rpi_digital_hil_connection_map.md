# Nexus Sync - Mapa de conexiones Raspberry Pi Digital HIL

![SIEZA](../assets/branding/sieza_logo_light.png)

Este mapa usa numeracion **GPIO BCM** de Raspberry Pi. La PZ y la Raspberry trabajan a 3.3 V. No conectar 5 V. Conectar GND comun primero. Usar una resistencia serie de 220 ohm a 1 kohm en cada GPIO.

La configuracion activa esta en `rpi_digital_hil_config.json`.

## Reglas electricas

- PZ JM1/JM2: logica 3.3 V LVCMOS.
- Raspberry Pi GPIO: logica 3.3 V.
- No unir una salida contra otra salida.
- No alimentar cargas, bobinas, SCR, contactores, campo ni motor desde GPIO.
- Usar optoacopladores si hay ruido, cables largos o hardware de potencia externo.
- Las senales SCR gate son solo senales logicas de prueba.
- Para monitorear timing SCR fino, usar analizador logico, no Python normal.

## A. Salidas Raspberry -> Entradas PZ

| Funcion | Senal Raspberry | GPIO BCM | Pin fisico Raspberry | Direccion | Senal PZ | Pin fisico PZ | Package PZ | Nivel activo | Estado inicial | Nota |
|---|---|---:|---:|---|---|---|---|---|---:|---|
| Termico OK | `RPI_THERMAL_OK_OUT` | 5 | 29 | RPI -> PZ | `thermal_ok_in` | JM2-11 | G15 | HIGH | 1 | Permisivo basico; PZ tiene pull-up |
| Excitador listo | `RPI_EXCITER_READY_OUT` | 6 | 31 | RPI -> PZ | `exciter_ready` | JM2-13 | K16 | HIGH | 1 | Permisivo basico |
| Falla de planta | `RPI_PLANT_FAULT_OUT` | 13 | 33 | RPI -> PZ | `plant_fault` | JM2-19 | N16 | HIGH | 0 | Inyeccion de falla |
| Voltaje pleno | `RPI_FULL_VOLTS_OUT` | 7 | 26 | RPI -> PZ | `full_volts` | JM2-9 | H15 | HIGH | 0 | Remapeado desde GPIO19 porque GPIO19 queda jalado a LOW en el banco |
| Corriente de campo presente | `RPI_FIELD_CURRENT_PRESENT_OUT` | 26 | 37 | RPI -> PZ | `field_current_present` | JM2-15 | J16 | HIGH | 0 | Activar despues de `field_enable` y retardo de banco |
| Motor sincronizado | `RPI_MOTOR_SYNCHRONIZED_OUT` | 16 | 36 | RPI -> PZ | `motor_synchronized` | JM2-17 | N15 | HIGH | 0 | Activar solo con confirmacion manual |
| Corriente de descarga presente | `RPI_DISCHARGE_CURRENT_PRESENT_OUT` | 20 | 38 | RPI -> PZ | `discharge_current_present` | JM2-5 | G19 | HIGH | 0 | Ruta de prueba de descarga |
| Pulso de extincion de descarga | `RPI_DISCHARGE_EXTINCTION_PULSE_OUT` | 21 | 40 | RPI -> PZ | `discharge_extinction_pulse` | JM2-7 | G20 | Pulso HIGH | 0 | No usar Python para timing fino |
| Tierra comun | `RPI_GND` | GND | 6/9/14/20/25/30/34/39 | GND | PZ GND | JM2-3 o JM2-4 | - | - | - | Conectar primero |

## B. Salidas PZ -> Entradas Raspberry

| Funcion | Senal PZ | Pin fisico PZ | Package PZ | Direccion | Senal Raspberry | GPIO BCM | Pin fisico Raspberry | Nivel activo | Nota |
|---|---|---|---|---|---|---:|---:|---|---|
| Orden de arranque/run | `motor_run` | JM1-15 | G18 | PZ -> RPI | `PZ_MOTOR_RUN_IN` | 17 | 11 | HIGH | Detecta orden de arranque de PZ |
| Rele 56K / listo | `relay_56k` | JM1-16 | A20 | PZ -> RPI | `PZ_RELAY_56K_IN` | 27 | 13 | HIGH | Indicacion READY |
| Habilitacion de campo | `field_enable` | JM1-17 | D19 | PZ -> RPI | `PZ_FIELD_ENABLE_IN` | 22 | 15 | HIGH | Dispara paso de corriente de campo en modo auto |
| Rele FAX / running | `relay_fax` | JM1-18 | C20 | PZ -> RPI | `PZ_RELAY_FAX_IN` | 23 | 16 | HIGH | Indicacion RUNNING |
| Falla/trip | `fault_out` | JM1-23 | H18 | PZ -> RPI | `PZ_FAULT_OUT_IN` | 24 | 18 | HIGH | Monitorear siempre |
| SCR enable | `scr_enable` | JM1-25 | K17 | PZ -> RPI | `PZ_SCR_ENABLE_IN` | 25 | 22 | HIGH | Permiso logico, no potencia |
| Comando FWT | `fwt_cmd` | JM1-27 | K18 | PZ -> RPI | `PZ_FWT_CMD_IN` | 12 | 32 | HIGH | Comando de timing de campo/freewheel |
| Comando DST | `dst_cmd` | JM1-29 | L16 | PZ -> RPI | `PZ_DST_CMD_IN` | 18 | 12 | HIGH | Comando descarga/arranque |

## C. Monitoreo opcional PZ -> Raspberry

Estos pines son opcionales y quedan deshabilitados en `rpi_digital_hil_config.json` durante la prueba principal para evitar confundir estados de PZ con pulsos SCR.

| Funcion | Senal PZ | Pin fisico PZ | Package PZ | Direccion | Senal Raspberry | GPIO BCM | Pin fisico Raspberry | Nivel activo | Nota |
|---|---|---|---|---|---|---:|---:|---|---|
| PWM de campo | `field_pwm` | JM1-19 | D20 | PZ -> RPI | `PZ_FIELD_PWM_IN` | null | - | PWM HIGH | Monitoreo opcional |
| Pulso de sincronismo | `sync_pulse` | JM1-21 | J18 | PZ -> RPI | `PZ_SYNC_PULSE_IN` | null | - | Pulso HIGH | Monitoreo opcional |
| SCR gate 1 | `scr_gate_g1` | JM2-21 | T16 | PZ -> RPI | `PZ_SCR_GATE_G1_IN` | null | - | Pulso HIGH | Monitoreo opcional |
| SCR gate 2 | `scr_gate_g2` | JM2-23 | U17 | PZ -> RPI | `PZ_SCR_GATE_G2_IN` | null | - | Pulso HIGH | Monitoreo opcional |
| SCR gate 3 | `scr_gate_g3` | JM2-25 | P14 | PZ -> RPI | `PZ_SCR_GATE_G3_IN` | null | - | Pulso HIGH | Monitoreo opcional |
| SCR gate 4 | `scr_gate_g4` | JM2-27 | R14 | PZ -> RPI | `PZ_SCR_GATE_G4_IN` | null | - | Pulso HIGH | Monitoreo opcional |
| SCR gate 5 | `scr_gate_g5` | JM2-29 | T11 | PZ -> RPI | `PZ_SCR_GATE_G5_IN` | null | - | Pulso HIGH | Monitoreo opcional |
| SCR gate 6 | `scr_gate_g6` | JM2-31 | T10 | PZ -> RPI | `PZ_SCR_GATE_G6_IN` | null | - | Pulso HIGH | Monitoreo opcional |

## Estado READY / reposo seguro

| Senal | Valor |
|---|---:|
| `thermal_ok_in` | 1 |
| `exciter_ready` | 1 |
| `plant_fault` | 0 |
| `full_volts` | 0 |
| `field_current_present` | 0 |
| `motor_synchronized` | 0 |
| `discharge_current_present` | 0 |
| `discharge_extinction_pulse` | 0 |

## Uso de interfaz web

Ejecutar en Raspberry:

```bash
/home/pi/NexusHIL/scripts/run_rpi_digital_hil_web.sh
```

Tambien existe acceso directo en el escritorio para levantar el servicio:

`/home/pi/Desktop/Nexus HIL Web.desktop`

Desde la computadora abrir:

`http://192.168.1.120:8080`

La interfaz web incluye un boton `discharge_extinction_pulse PULSE ON/OFF`. Ese boton genera un tren de pulsos en GPIO BCM 21 / pin fisico 40 hacia `discharge_extinction_pulse` JM2-7/G20. La frecuencia inicial se configura en `rpi_digital_hil_config.json` como `timing_ms.discharge_pulse_hz`.

---

Nexus by SIEZA 2026. Todos los derechos reservados.
