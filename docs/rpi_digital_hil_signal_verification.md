# Nexus Sync - Verificacion de senales para Raspberry Pi Digital HIL

![SIEZA](../assets/branding/sieza_logo_light.png)

Proyecto fuente: `C:\Users\zamor\Documents\zboardProject_copy2\nexus-sync-product`

Pinout fuente: `Puzhi PZ-Starlite CON Pins Signal and Equal Length.xlsx`, hojas `JM1` y `JM2`.

XDC activo usado para verificacion: `fpga/constraints/pz_sync_control_hdmi_bd.xdc`.

## Resumen

Todas las entradas digitales solicitadas hacia PZ existen como puertos RTL top-level y tienen pin fisico en JM2. Todas las salidas digitales solicitadas desde PZ existen y tienen pin fisico en JM1/JM2. Las senales `field_pwm`, `sync_pulse` y `scr_gate_g1..g6` se dejan como monitoreo opcional porque Python normal no debe usarse para medir timing fino SCR.

El archivo `rpi_digital_hil_config.json` ya contiene asignacion GPIO BCM para Raspberry Pi.

## Entradas hacia PZ

| Senal | Encontrada | Evidencia RTL | Direccion | Pin PZ | Package | GPIO BCM Raspberry | Activo/pull | Uso en logica | AXI/status | Reposo seguro |
|---|---|---|---|---|---|---:|---|---|---|---:|
| `discharge_current_present` | Si | `pz_sync_control_axi_top.v:88`, `:793`, `:1171`, `:1220` | entrada PZ | JM2-5 | G19 | 20 | HIGH, pulldown | espera descarga, stopping, reset safe | `PLANT_IN` bit 8 / `0x34` | 0 |
| `discharge_extinction_pulse` | Si | `pz_sync_control_axi_top.v:89`, `:794`, `:897`, `:1218` | entrada PZ | JM2-7 | G20 | 21 | pulso HIGH, pulldown | estimador frecuencia descarga/slip | `PLANT_IN` bit 9 / `0x34` | 0 |
| `full_volts` | Si | `pz_sync_control_axi_top.v:90`, `:792`, `:1169`, `:1221` | entrada PZ | JM2-9 | H15 | 7 | HIGH, pulldown | secuencia de arranque | `PLANT_IN` bit 10 / `0x34` | 0 |
| `thermal_ok_in` | Si | `pz_sync_control_axi_top.v:91`, `:790` | entrada PZ | JM2-11 | G15 | 5 | HIGH por defecto, pullup | permisivo READY / trip termico | `PLANT_IN` bit 11 / `0x34` | 1 |
| `exciter_ready` | Si | `pz_sync_control_axi_top.v:92`, `:791`, `:1168`, `:1222` | entrada PZ | JM2-13 | K16 | 6 | HIGH, pulldown | permisivo READY/reset safe | `PLANT_IN` bit 12 / `0x34` | 1 |
| `field_current_present` | Si | `pz_sync_control_axi_top.v:93`, `:795`, `:1174`, `:1223` | entrada PZ | JM2-15 | J16 | 26 | HIGH, pulldown | verify field, running, reset safe | `PLANT_IN` bit 13 / `0x34` | 0 |
| `motor_synchronized` | Si | `pz_sync_control_axi_top.v:94`, `:796`, `:1176`, `:1224` | entrada PZ | JM2-17 | N15 | 16 | HIGH, pulldown | deteccion pull-out / running | `PLANT_IN` bit 14 / `0x34` | 0 |
| `plant_fault` | Si | `pz_sync_control_axi_top.v:95`, `:797`, `:1177`, `:1225` | entrada PZ | JM2-19 | N16 | 13 | HIGH, pulldown | trip externo de planta | `PLANT_IN` bit 15 / `0x34` | 0 |

## Salidas desde PZ

| Senal | Encontrada | Evidencia RTL/XDC | Direccion | Pin PZ | Package | GPIO BCM Raspberry | Comportamiento | Uso Raspberry | Nota |
|---|---|---|---|---|---|---:|---|---|---|
| `motor_run` | Si | `pz_sync_control_axi_top.v:71`, `sync_control_fsm.v:82`, XDC `:26` | salida PZ | JM1-15 | G18 | 17 | estado HIGH | requerido | activo de START_DETECTED a RUNNING |
| `relay_56k` | Si | `pz_sync_control_axi_top.v:85`, `sync_control_fsm.v:88`, XDC `:43` | salida PZ | JM1-16 | A20 | 27 | estado HIGH | requerido | high solo en READY |
| `field_enable` | Si | `pz_sync_control_axi_top.v:72`, `sync_control_fsm.v:83`, XDC `:29` | salida PZ | JM1-17 | D19 | 22 | estado HIGH | requerido | habilitacion de campo |
| `relay_fax` | Si | `pz_sync_control_axi_top.v:86`, `sync_control_fsm.v:89`, XDC `:47` | salida PZ | JM1-18 | C20 | 23 | estado HIGH | requerido | high solo en RUNNING |
| `field_pwm` | Si | `pz_sync_control_top.v:34`, `:320`, XDC `:32` | salida PZ | JM1-19 | D20 | null | PWM HIGH | opcional | deshabilitado durante prueba principal |
| `sync_pulse` | Si | `pz_sync_control_top.v:38`, `:352`, XDC `:35` | salida PZ | JM1-21 | J18 | null | pulso HIGH | opcional | deshabilitado durante prueba principal |
| `fault_out` | Si | `pz_sync_control_axi_top.v:84`, `sync_control_fsm.v:87`, XDC `:38` | salida PZ | JM1-23 | H18 | 24 | estado HIGH | requerido | activo en FAULT/ACK_FAULT/LOCKOUT |
| `scr_enable` | Si | `pz_sync_control_axi_top.v:74`, `sync_control_fsm.v:84`, XDC `:57` | salida PZ | JM1-25 | K17 | 25 | estado HIGH | requerido | permiso logico de gates |
| `fwt_cmd` | Si | `sync_control_fsm.v:85`, XDC `:80` | salida PZ | JM1-27 | K18 | 12 | estado HIGH | requerido | timing campo/freewheel |
| `dst_cmd` | Si | `sync_control_fsm.v:86`, XDC `:83` | salida PZ | JM1-29 | L16 | 18 | estado HIGH | requerido | descarga/arranque/stopping |
| `scr_gate_g1` | Si | `pz_sync_control_axi_top.v:75`, `:655`, XDC `:62` | salida PZ | JM2-21 | T16 | null | pulso HIGH | opcional | deshabilitado durante prueba principal |
| `scr_gate_g2` | Si | XDC `:65` | salida PZ | JM2-23 | U17 | null | pulso HIGH | opcional | deshabilitado durante prueba principal |
| `scr_gate_g3` | Si | XDC `:68` | salida PZ | JM2-25 | P14 | null | pulso HIGH | opcional | deshabilitado durante prueba principal |
| `scr_gate_g4` | Si | XDC `:71` | salida PZ | JM2-27 | R14 | null | pulso HIGH | opcional | deshabilitado durante prueba principal |
| `scr_gate_g5` | Si | XDC `:74` | salida PZ | JM2-29 | T11 | null | pulso HIGH | opcional | deshabilitado durante prueba principal |
| `scr_gate_g6` | Si | XDC `:77` | salida PZ | JM2-31 | T10 | null | pulso HIGH | opcional | deshabilitado durante prueba principal |

## Verificacion contra Excel PZ

La hoja `JM1` del Excel contiene los package pins G18, A20, D19, C20, D20, J18, H18, K17, K18 y L16 en los pines fisicos JM1 esperados.

La hoja `JM2` del Excel contiene los package pins G19, G20, H15, G15, K16, J16, N15, N16, T16, U17, P14, R14, T11 y T10 en los pines fisicos JM2 esperados.

## Nota sobre constraints

`fpga/constraints/pz_sync_control.xdc` no contiene todo el set final de senales HIL. El mapa completo y activo esta en `fpga/constraints/pz_sync_control_hdmi_bd.xdc`, que incluye LVCMOS33 para las senales verificadas.

## Resultado

PASS para existencia de senales, direccion logica y pines fisicos PZ. La asignacion Raspberry BCM queda documentada en `rpi_digital_hil_connection_map.md` y aplicada en `rpi_digital_hil_config.json`.

---

Nexus by SIEZA 2026. Todos los derechos reservados.
