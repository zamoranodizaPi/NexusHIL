# Memoria tecnica - Nexus HIL para Nexus Sync

Fecha de cierre de esta memoria: 2026-08-11

Proyecto local: `C:\Users\zamor\Documents\Nexus\NexusHIL`

Control bajo prueba: Nexus Sync / PZ en `192.168.1.50`

Raspberry Pi HIL: `192.168.1.120`, usuario `pi`, servicio web en `http://192.168.1.120:8080`

Leyenda: Nexus by SIEZA 2026. Todos los derechos reservados.

## Objetivo original

Crear un HIL digital para apoyar pruebas del control de motor sincrono Nexus Sync instalado en la PZ. La Raspberry Pi debe simular estados digitales de planta, monitorear salidas digitales de la PZ y permitir control remoto desde una interfaz web. Las senales analogicas de voltaje y corriente no las genera la Raspberry; se inyectan con una fuente/simulador Ponovo hacia el acondicionamiento/ADS/PZ.

## Lo que se implemento

1. Se creo el proyecto Nexus HIL en este repositorio.
2. Se implemento una interfaz web para la Raspberry, reemplazando la interfaz grafica local.
3. Se instalo el servicio `nexus-hil-web.service` en la Raspberry para levantar el HIL web al iniciar.
4. Se creo acceso directo en el escritorio de la Raspberry.
5. Se aplico branding SIEZA/Nexus y la leyenda:

```text
Nexus by SIEZA 2026. Todos los derechos reservados.
```

6. Se deshabilito la pantalla SPI TFT de 3.5 pulgadas que ocupaba los pines fisicos 1 al 26.
7. Se regreso el video por HDMI para liberar GPIOs.
8. Se actualizo el pinout real Raspberry <-> PZ usando la informacion de conexiones y pruebas de banco.
9. Se genero documentacion en espanol para:
   - mapa de conexiones,
   - verificacion de senales,
   - procedimiento Ponovo para intentar READY/RUNNING,
   - documentacion HTML del HIL.
10. Se copio la documentacion actualizada a la Raspberry en `/home/pi/NexusHIL/docs/`.

## Archivos principales creados o actualizados

- `rpi_digital_hil_config.json`
- `tools/rpi_digital_hil/nexus_sync_rpi_digital_hil.py`
- `tools/rpi_digital_hil/nexus_sync_rpi_digital_hil_web.py`
- `scripts/install_rpi_systemd_service.sh`
- `scripts/install_rpi_desktop_shortcut.sh`
- `docs/rpi_digital_hil_connection_map.md`
- `docs/rpi_digital_hil_signal_verification.md`
- `docs/rpi_digital_hil_ponovo_running_procedure.md`
- `docs/rpi_digital_hil_simulator.html`
- `assets/branding/`

## Estado actual de la Raspberry HIL

La Raspberry esta funcionando como HIL web. El servicio queda activo al arranque y la interfaz se consulta desde:

```text
http://192.168.1.120:8080
```

Estado visto por la API de la Raspberry durante la ultima revision:

```json
{
  "armed": true,
  "outputs_to_pz": {
    "thermal_ok_in": 1,
    "exciter_ready": 1,
    "plant_fault": 0,
    "full_volts": 0,
    "field_current_present": 0,
    "motor_synchronized": 0,
    "discharge_current_present": 0,
    "discharge_extinction_pulse": 0
  },
  "inputs_from_pz": {
    "motor_run": 0,
    "relay_56k": 0,
    "field_enable": 0,
    "relay_fax": 0,
    "fault_out": 0,
    "scr_enable": 0,
    "fwt_cmd": 0,
    "dst_cmd": 0
  }
}
```

Esto significa que la Raspberry esta aplicando el reposo digital correcto para buscar READY:

- `thermal_ok_in = 1`
- `exciter_ready = 1`
- `plant_fault = 0`
- `full_volts = 0`
- `field_current_present = 0`
- `motor_synchronized = 0`
- `discharge_current_present = 0`

`relay_56k` permanece en 0, por lo tanto la PZ todavia no declara READY.

## Pinout digital actualmente usado

### Raspberry hacia PZ

| Funcion | GPIO BCM | Pin Raspberry | Senal PZ | Pin PZ | Estado inicial |
|---|---:|---:|---|---|---:|
| Termico OK | 5 | 29 | `thermal_ok_in` | JM2-11 / G15 | 1 |
| Excitador listo | 6 | 31 | `exciter_ready` | JM2-13 / K16 | 1 |
| Falla planta | 13 | 33 | `plant_fault` | JM2-19 / N16 | 0 |
| Voltaje pleno | 7 | 26 | `full_volts` | JM2-9 / H15 | 0 |
| Corriente de campo presente | 26 | 37 | `field_current_present` | JM2-15 / J16 | 0 |
| Motor sincronizado | 16 | 36 | `motor_synchronized` | JM2-17 / N15 | 0 |
| Corriente de descarga presente | 20 | 38 | `discharge_current_present` | JM2-5 / G19 | 0 |
| Pulso extincion descarga | 21 | 40 | `discharge_extinction_pulse` | JM2-7 / G20 | 0/pulso |

Nota importante: `full_volts` se movio a GPIO7 / pin fisico 26 porque GPIO19 / pin 35 quedaba jalado a LOW en el banco. El cambio visual y funcional se corrigio en la web y en la documentacion.

### PZ hacia Raspberry

| Funcion | Senal PZ | Pin PZ | GPIO BCM | Pin Raspberry |
|---|---|---|---:|---:|
| Arranque/run | `motor_run` | JM1-15 / G18 | 17 | 11 |
| READY / 56K | `relay_56k` | JM1-16 / A20 | 27 | 13 |
| Campo habilitado | `field_enable` | JM1-17 / D19 | 22 | 15 |
| FAX running | `relay_fax` | JM1-18 / C20 | 23 | 16 |
| Falla/trip | `fault_out` | JM1-23 / H18 | 24 | 18 |
| SCR enable | `scr_enable` | JM1-25 / K17 | 25 | 22 |
| FWT | `fwt_cmd` | JM1-27 / K18 | 12 | 32 |
| DST | `dst_cmd` | JM1-29 / L16 | 18 | 12 |

Los monitores opcionales de SCR gate quedaron deshabilitados para no confundir pulsos SCR con estados de control.

## Lo que se comprobo en la PZ

La PZ responde en `192.168.1.50`. Se consultaron endpoints HTTP del firmware Nexus Sync.

Respuesta resumida de `/status`:

```json
{
  "firmware": "NEXUS_SYNC_MEAS_V2B_RAW32_ZC_INTERP_HW_TEST",
  "fsm_state": 0,
  "fault_code": 0,
  "fault_name": "NONE",
  "fault_active": true,
  "fault_acknowledged": false,
  "reset_blocked": false,
  "severity": "FAULT",
  "plant": {
    "motor_run": false,
    "relay_56k": false,
    "field_enable": false,
    "fault_out": false
  }
}
```

Respuesta resumida de `/api/measurements`:

```text
measurement_online = true
ads_online = true
axi_online = true
frequency_hz ~= 60.1
voltage_system_healthy = true
voltage_phase_sequence_ok = true
voltage_undervoltage = false
voltage_phase_loss = false
valid_for_protection = true
```

Esto indica que la medicion analogica basica si esta viva. No parece faltar voltaje/frecuencia para READY en ese momento.

Respuesta resumida de `/api/sequence`:

```json
{
  "fsm_state": 0,
  "state": "INIT",
  "full_volts": true,
  "discharge_current_present": true,
  "discharge_pulse": true,
  "field_current_present": true,
  "motor_synchronized": true,
  "scr_enable_ok": true,
  "fault_code": 0
}
```

Esta lectura es anormal porque la Raspberry tenia esos estados en 0 o en reposo. La explicacion encontrada esta en la configuracion de permisivos.

## Causa probable de que no funcione

La PZ no esta bloqueada por un `fault_code` concreto. El problema principal observado es que la configuracion persistente de permisivos esta corrupta, corrida o vieja.

Valores actuales leidos en `/api/settings`:

```text
settings_status = STORED_CLAMPED
clamp_count = 21
permissive_enable_mask = 131
permissive_required_start_mask = 229
permissive_required_run_mask = 255
permissive_active_high_mask = 0
permissive_bypass_mask = 0
```

Valores esperados por el RTL/firmware para el esquema HIL:

```text
permissive_enable_mask = 255              # 0xFF
permissive_required_start_mask = 131      # 0x83
permissive_required_run_mask = 229        # 0xE5
permissive_active_high_mask = 255         # 0xFF
permissive_bypass_mask = 0                # 0x00
```

El valor mas critico es:

```text
permissive_active_high_mask = 0
```

Con ese valor, la PZ interpreta los permisivos como activos en bajo. Para este cableado y para la Raspberry HIL deben ser activos en alto. Por eso la PZ puede leer senales invertidas: cree que `full_volts`, `field_current_present` o `motor_synchronized` estan activos cuando en realidad la Raspberry no los esta activando.

Ademas, los tres primeros campos parecen corridos contra los defaults del firmware:

```text
Actual enable        = 0x83  -> parece default de required_start
Actual required_start = 0xE5 -> parece default de required_run
Actual required_run   = 0xFF -> parece default de enable
```

Esto coincide con `settings_status=STORED_CLAMPED` y `clamp_count=21`, indicando que la PZ cargo ajustes almacenados que fueron corregidos/clampados por firmware.

## Evidencia en el codigo Nexus Sync

En el proyecto Nexus Sync se reviso el RTL:

```text
fpga/rtl/pz_sync_control_axi_top.v
```

La polaridad se aplica asi:

```verilog
thermal_ok_norm = permissive_active_high_mask[0] ? thermal_ok_in : ~thermal_ok_in;
exciter_ready_norm = permissive_active_high_mask[1] ? exciter_ready : ~exciter_ready;
full_volts_norm = permissive_active_high_mask[2] ? full_volts : ~full_volts;
discharge_current_present_norm = permissive_active_high_mask[3] ? discharge_current_present : ~discharge_current_present;
discharge_extinction_pulse_norm = permissive_active_high_mask[4] ? discharge_extinction_pulse : ~discharge_extinction_pulse;
field_current_present_norm = permissive_active_high_mask[5] ? field_current_present : ~field_current_present;
motor_synchronized_norm = permissive_active_high_mask[6] ? motor_synchronized : ~motor_synchronized;
plant_fault_norm = permissive_active_high_mask[7] ? plant_fault : ~plant_fault;
```

En:

```text
fpga/rtl/sync_control_axi_regs.v
```

Los defaults son:

```verilog
DEFAULT_PERMISSIVE_ENABLE_MASK      = 0xFF;
DEFAULT_PERMISSIVE_REQ_START_MASK   = 0x83;
DEFAULT_PERMISSIVE_REQ_RUN_MASK     = 0xE5;
DEFAULT_PERMISSIVE_ACTIVE_HIGH_MASK = 0xFF;
```

Por eso la correccion propuesta no cambia el pinout; corrige como la PZ interpreta las entradas.

## Por que todavia no esta funcionando

El sistema aun no llega a READY/RUNNING por esta cadena:

1. La Raspberry HIL ya esta entregando el reposo digital esperado.
2. La Ponovo/ADS ya muestra medicion valida en la PZ.
3. La PZ permanece en `fsm_state=INIT`, `severity=FAULT`, `relay_56k=0`.
4. La PZ reporta `fault_code=0` y `fault_name=NONE`, por lo tanto no hay una falla electrica identificada.
5. La PZ interpreta permisivos con polaridad equivocada por `permissive_active_high_mask=0`.
6. La configuracion persistente muestra `STORED_CLAMPED`, `clamp_count=21` y campos de permisivos corridos.
7. Por seguridad no se aplico automaticamente la mutacion persistente de esos permisos sin aprobacion explicita.

En resumen: el HIL y el cableado digital ya estan en el punto esperado; lo que falta es corregir la configuracion de permisivos dentro de la PZ y despues hacer ACK/RESET si queda fault latched.

## Correccion pendiente

Aplicar por API, con credenciales default si siguen activas:

```powershell
curl.exe -u operator:SIE2 -H "Content-Type: application/json" -X POST --data-raw "{\"permissive_enable_mask\":255,\"permissive_required_start_mask\":131,\"permissive_required_run_mask\":229,\"permissive_active_high_mask\":255,\"permissive_bypass_mask\":0}" http://192.168.1.50/api/settings/save
```

Despues validar:

```powershell
curl.exe http://192.168.1.50/api/settings
curl.exe http://192.168.1.50/api/sequence
curl.exe http://192.168.1.50/status
```

Valores esperados despues de la correccion:

```text
settings_status != STORED_CLAMPED, o al menos mascaras correctas
permissive_active_high_mask = 255
full_volts = false antes de START
field_current_present = false antes de campo
motor_synchronized = false antes de sincronismo
relay_56k = true cuando READY sea real
```

Si la PZ queda con HTTP intermitente, hacer power-cycle o usar consola UART. Durante las pruebas se observo que el servidor HTTP embebido puede ser sensible a consultas simultaneas; conviene consultar endpoints de uno en uno.

## Secuencia recomendada despues de corregir permisivos

1. Confirmar en la Raspberry web:
   - `thermal_ok_in=1`
   - `exciter_ready=1`
   - `plant_fault=0`
   - `full_volts=0`
   - `field_current_present=0`
   - `motor_synchronized=0`
   - `discharge_current_present=0`
2. Confirmar en Ponovo/PZ:
   - Vab/Vbc/Vca presentes,
   - 60 Hz estable,
   - secuencia ABC,
   - sin undervoltage,
   - sin phase loss.
3. Consultar `/api/sequence`.
4. Si sigue `fault_active=true` pero `fault_code=0`, hacer ACK y RESET desde HMI/API.
5. Esperar `relay_56k=1`.
6. Solo despues de READY real, dar START desde la PZ.
7. Al ver `motor_run=1`, activar `full_volts`.
8. Seguir el procedimiento Ponovo documentado para descarga, campo y sincronismo.

## Riesgos y notas de seguridad

- No activar `full_volts` antes de START.
- No activar `field_current_present` antes de que la PZ pida campo.
- No activar `motor_synchronized` sin condicion analogica representativa.
- No unir referencias ni conectar salidas Ponovo directamente al ADS/PZ sin acondicionamiento, aislamiento y escalamiento validado.
- No cambiar pinout mientras PZ/Raspberry/Ponovo esten energizados.
- No enviar comandos START/RUN remotos desde API sin control fisico del banco.

## Estado final de esta sesion

Completado:

- HIL web en Raspberry.
- Servicio al arranque.
- Branding SIEZA/Nexus.
- Documentacion en espanol.
- Pinout actualizado.
- Pantalla SPI deshabilitada para liberar GPIO.
- Diagnostico de READY/FAULT hecho contra la PZ real.

Pendiente:

- Aprobar y aplicar la correccion persistente de mascaras de permisivos en la PZ.
- Verificar que `relay_56k` suba a 1.
- Ejecutar la secuencia START -> `motor_run` -> `full_volts` -> descarga -> campo -> sincronismo -> RUNNING.

