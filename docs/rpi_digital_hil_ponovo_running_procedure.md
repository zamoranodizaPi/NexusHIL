# Nexus Sync - Procedimiento Ponovo para llevar el control a RUNNING

![SIEZA](../assets/branding/sieza_logo_light.png)

Este procedimiento combina:

- Ponovo como fuente/simulador de sistemas de potencia para las senales analogicas.
- Raspberry Pi como HIL digital web para permisos, feedbacks y monitoreo.
- PZ/Nexus Sync como controlador bajo prueba.

La Raspberry no genera voltajes ni corrientes. La Ponovo entrega las senales analogicas hacia el acondicionamiento/ADS/PZ. No conectar salidas Ponovo directamente al ADC/ADS si no existe etapa de acondicionamiento, aislamiento y escalamiento validada.

## Coordinacion antes de operar el HIL remoto

`SAFE_STOP`, `READY` y cualquier boton de senal en la web de la Raspberry cambian salidas fisicas reales hacia la PZ. `READY` en particular vuelve a poner en 0 `full_volts`, `field_current_present`, `discharge_current_present` y `motor_synchronized` -- si alguien esta en medio de una secuencia manual en el banco (por ejemplo en la etapa de descarga con `discharge_current_present=ON`), una llamada remota a `READY`/`SAFE_STOP` le resetea esas senales sin aviso y puede contribuir a una falla real (ya paso: una prueba remota de `ARM`+`READY` coincidio con una prueba manual en curso y la PZ cayo en `DISCHARGE_CIRCUIT`).

Regla: antes de tocar la web del HIL desde una sesion remota (incluyendo pedidos a un asistente con acceso a la API), confirmar primero si hay alguien operando fisicamente el banco en ese momento.

## Mapa de canales analogicos ADS/Ponovo

El firmware COMTRADE/HMI etiqueta los canales ADS asi:

| ADS/PZ | Nombre Nexus Sync | Tipo | Canal Ponovo sugerido | Uso para RUNNING |
|---:|---|---|---|---|
| CH0 | `Vab` | Voltaje linea-linea | V1 | Frecuencia, secuencia, PLL/ZC, salud de voltaje |
| CH1 | `Vbc` | Voltaje linea-linea | V2 | Frecuencia, secuencia, PLL/ZC, salud de voltaje |
| CH2 | `Vca` | Voltaje linea-linea | V3 | Frecuencia, secuencia, PLL/ZC, salud de voltaje |
| CH3 | `Ia` | Corriente fase A | I1 | Protecciones de corriente / carga simulada |
| CH4 | `Ib` | Corriente fase B | I2 | Protecciones de corriente / carga simulada |
| CH5 | `Ic` | Corriente fase C | I3 | Protecciones de corriente / carga simulada |
| CH6 | `FieldCurrent` | Corriente de campo | Aux/I4 si disponible | Validacion de campo; tambien existe feedback digital `field_current_present` |
| CH7 | `DischargeCurrent` | Corriente de descarga | Aux/I5 si disponible | Deteccion de descarga; tambien existe feedback digital `discharge_current_present` |

Evidencia de proyecto:

- `nexus_comtrade.c`: CH0..CH7 = `Vab`, `Vbc`, `Vca`, `Ia`, `Ib`, `Ic`, `FieldCurrent`, `DischargeCurrent`.
- `CONTROL_LOGIC_AUDIT.md`: `Field current` usa ADS CH6 o entrada digital; `Discharge current` usa ADS CH7 o entrada digital.

## Valores iniciales recomendados

Usar siempre valores secundarios seguros, compatibles con el acondicionamiento instalado. Si el banco ya fue calibrado con otro nivel, usar ese nivel.

| Senal | Valor inicial seguro | Valor para prueba RUNNING | Comentario |
|---|---:|---:|---|
| Frecuencia | 60.000 Hz | 60.000 Hz estable | Mantener fija hasta tener RUNNING |
| Vab/Vbc/Vca | 0 V | nivel nominal calibrado, por ejemplo 40 V RMS si ese es el nivel validado del banco | Deben estar balanceados |
| Angulo Vab | 0 grados | 0 grados | Referencia |
| Angulo Vbc | -120 grados | -120 grados | Secuencia positiva |
| Angulo Vca | +120 grados | +120 grados | Secuencia positiva |
| Ia/Ib/Ic | 0 A | corriente baja y balanceada si se quiere carga | Evitar pickup de protecciones |
| FieldCurrent CH6 | 0 A | mayor que umbral de campo | Alternativa analogica al digital `field_current_present` |
| DischargeCurrent CH7 | 0 A | mayor que umbral de descarga | Alternativa analogica al digital `discharge_current_present` |

Notas:

- El umbral default de campo/descarga en RTL es `800 raw`; en documentacion de calibracion se menciona aproximadamente `field_current_min = 0.8 A` y `discharge_current_detect = 1.0 A`, pero el valor real depende de la escala del acondicionamiento.
- Para la primera corrida, es mas controlado usar CH6/CH7 bajos y simular presencia de campo/descarga con la Raspberry. Despues se valida la ruta analogica CH6/CH7.

## Condicion real para READY

En Nexus Sync, READY no depende solo de la Raspberry. La FSM entra a READY cuando se cumple:

```text
measurement_online = 1
thermal_ok_in = 1
exciter_ready = 1
plant_fault = 0
fault_latched = 0
```

Donde `measurement_online` viene del ADS/Ponovo:

```text
signal_present = 1
freq_valid = 1
freq_ok = 1
```

Por eso, si `thermal_ok_in=1`, `exciter_ready=1` y `plant_fault=0` pero `relay_56k` sigue en 0, no falta `full_volts`. Lo mas probable es que la PZ todavia no tenga medicion analogica valida, este en fault latched, o no este leyendo el ADC/ADS.

Importante: `full_volts` se activa despues del START. No usar `full_volts` para intentar entrar a READY.

## Diagnostico PZ por API

Si la PZ aparece en `FAULT` pero `fault_code=0` y `fault_name=NONE`, revisar primero la configuracion de permisivos antes de buscar una falla electrica. En la PZ de banco `192.168.1.50` se observo:

```text
measurement_online = true
ads_online = true
frequency_hz = 60.1 aprox.
voltage_system_healthy = true
voltage_phase_sequence_ok = true
fsm_state = INIT
fault_code = 0
fault_active = true
settings_status = STORED_CLAMPED
permissive_enable_mask = 131
permissive_required_start_mask = 229
permissive_required_run_mask = 255
permissive_active_high_mask = 0
```

**Actualizacion 2026-08-11: corregido y confirmado.** Se aplico el POST de abajo contra la PZ de banco. Resultado verificado en vivo: `fault_active` paso a `false`, `fsm_state` paso a `1` (`READY`), `relay_56k=true`. La PZ paso a mostrar `severity=WARNING` ("SELF-TEST DEGRADED") en vez de `FAULT` -- eso es esperado y es un aviso distinto (ver `MEMORIA_NEXUS_HIL.md`, no bloquea README ni START), causado por `using_default_credentials=true` (contrasena de operador todavia de fabrica). No confundir ese warning con el fault de permisivos ya resuelto.

Eso indica medicion analogica sana, pero permisivos guardados con polaridad incorrecta o valores corridos. Para este HIL digital los valores esperados son:

| Campo API | Valor decimal | Hex |
|---|---:|---:|
| `permissive_enable_mask` | 255 | `0xFF` |
| `permissive_required_start_mask` | 131 | `0x83` |
| `permissive_required_run_mask` | 229 | `0xE5` |
| `permissive_active_high_mask` | 255 | `0xFF` |
| `permissive_bypass_mask` | 0 | `0x00` |

Comando API para corregirlo, usando credenciales default de banco si siguen activas:

```powershell
curl.exe -u operator:SIE2 -H "Content-Type: application/json" -X POST --data-raw "{\"permissive_enable_mask\":255,\"permissive_required_start_mask\":131,\"permissive_required_run_mask\":229,\"permissive_active_high_mask\":255,\"permissive_bypass_mask\":0}" http://192.168.1.50/api/settings/save
```

Despues consultar:

```powershell
curl.exe http://192.168.1.50/api/settings
curl.exe http://192.168.1.50/api/sequence
curl.exe http://192.168.1.50/status
```

Si despues de un intento de POST la PZ responde ping pero el puerto 80 queda cerrado, esperar 30 s y volver a consultar. Si no vuelve, hacer power-cycle de la PZ o usar consola UART; no seguir enviando peticiones HTTP en rafaga.

## Preparacion antes de energizar senales

1. Apagar PZ, Raspberry y salidas Ponovo.
2. Verificar que Ponovo entra a una etapa de acondicionamiento/aislamiento adecuada antes del ADS/PZ.
3. Conectar GND/referencia segun el esquema del acondicionamiento. No unir referencias si el acondicionamiento indica entradas aisladas.
4. Cablear Raspberry segun `rpi_digital_hil_connection_map.md`.
5. Encender Raspberry. El servicio `nexus-hil-web.service` debe iniciar solo.
6. Desde la computadora abrir `http://192.168.1.120:8080`.
7. En la interfaz web, confirmar seguridad y presionar `ARMAR`.
8. Presionar `READY` solo para aplicar los permisivos digitales de reposo.
9. Verificar en la interfaz web:
   - `thermal_ok_in = ON`
   - `exciter_ready = ON`
   - `plant_fault = OFF`
   - `full_volts = OFF`
   - `field_current_present = OFF`
   - `motor_synchronized = OFF`
   - `discharge_current_present = OFF`
   - `discharge_extinction_pulse = OFF`

## Configuracion Ponovo

1. Crear un caso trifasico balanceado.
2. Configurar voltajes:
   - V1 -> ADS CH0 / `Vab`, 60 Hz, angulo 0 grados.
   - V2 -> ADS CH1 / `Vbc`, 60 Hz, angulo -120 grados.
   - V3 -> ADS CH2 / `Vca`, 60 Hz, angulo +120 grados.
3. Configurar corrientes principales:
   - I1 -> ADS CH3 / `Ia`.
   - I2 -> ADS CH4 / `Ib`.
   - I3 -> ADS CH5 / `Ic`.
   - Empezar en 0 A o en corriente baja balanceada.
4. Configurar auxiliares si el banco los tiene cableados:
   - Aux/I4 -> ADS CH6 / `FieldCurrent`.
   - Aux/I5 -> ADS CH7 / `DischargeCurrent`.
   - Empezar ambos en 0 A.
5. Mantener corrientes en 0 A durante la busqueda de READY.
6. Para lograr READY real, las salidas de voltaje Ponovo V1/V2/V3 deben estar encendidas al nivel calibrado y con frecuencia valida.

## Secuencia para llegar a RUNNING

### Paso 1 - Medicion analogica estable

1. En Ponovo, dejar corrientes Ia/Ib/Ic/CH6/CH7 en 0 A.
2. Encender salidas de voltaje Ponovo V1/V2/V3 al nivel nominal calibrado.
3. Mantener Vab/Vbc/Vca balanceados a 60 Hz.
4. Esperar a que PZ/HMI muestre medicion valida:
   - frecuencia cercana a 60 Hz,
   - Vab/Vbc/Vca presentes,
   - sin phase loss,
   - sin undervoltage,
   - secuencia correcta.
5. No activar `full_volts` todavia.

### Paso 2 - Permisivos digitales

En la interfaz web Raspberry:

1. Presionar `ARMAR` si todavia esta desarmado.
2. Presionar `READY`.
3. Confirmar:
   - `thermal_ok_in = ON`
   - `exciter_ready = ON`
   - `plant_fault = OFF`
   - `full_volts = OFF`
   - `field_current_present = OFF`
   - `motor_synchronized = OFF`
   - `discharge_current_present = OFF`
4. Esperar `relay_56k = ON`.
5. Si `relay_56k` no enciende, revisar la tabla "Diagnostico si no entra READY" antes de avanzar.

### Paso 3 - Orden START desde PZ

**Importante: START/RUN se da fisicamente en el banco (HMI local de la PZ), nunca por API/remoto sin alguien presente controlando el equipo.** Ver "Coordinacion antes de operar el HIL remoto" al inicio de este documento.

1. Dar START desde HMI/operador de Nexus Sync, en persona.
2. En la interfaz web verificar `motor_run = ON`.
3. Verificar si `dst_cmd = ON`; esto indica que la PZ esta pidiendo etapa de descarga/arranque.

### Paso 4 - Full volts

1. Confirmar que las salidas Ponovo de voltaje ya estan estables.
2. En la interfaz web activar `full_volts = ON`.
3. No activar `full_volts` si Vab/Vbc/Vca no estan estables.

### Paso 5 - Descarga / aceleracion

Para ayudar a la FSM a pasar la etapa de descarga:

1. Si CH7 esta cableado y calibrado:
   - Subir `DischargeCurrent` CH7 por arriba del umbral de deteccion.
2. En cualquier caso, para prueba digital:
   - Activar `discharge_current_present = ON` en la interfaz web.
   - Activar el boton `discharge_extinction_pulse PULSE ON`.
3. Mantener el tren de pulsos de descarga mientras `dst_cmd` este activo.
4. Si la FSM espera una caida de frecuencia de descarga, bajar gradualmente la frecuencia de pulso configurada en `rpi_digital_hil_config.json` (`timing_ms.discharge_pulse_hz`) entre corridas. La interfaz web usa ese valor al iniciar el tren de pulsos.

Valor inicial del tren de pulsos: `5 Hz`.

### Paso 6 - Campo

1. Esperar en la interfaz web que `field_enable = ON`.
2. Si CH6 esta cableado y calibrado:
   - Subir `FieldCurrent` CH6 por arriba del umbral de campo.
3. En prueba digital:
   - Activar `field_current_present = ON`.
4. Verificar que no se active `fault_out`.

### Paso 7 - Sincronismo

1. Mantener Vab/Vbc/Vca a 60 Hz, balanceados y con secuencia positiva.
2. Confirmar en HMI/API que la referencia de sincronismo esta OK si esta disponible (`sync_reference_ok`, `field_apply_ok` o `scr_enable_ok`).
3. En la interfaz web activar `motor_synchronized = ON` solo cuando la condicion analogica Ponovo representa sincronismo.

### Paso 8 - RUNNING

1. Verificar que `relay_fax = ON` o que HMI/API indique RUNNING.
2. Mantener:
   - Vab/Vbc/Vca estables,
   - `thermal_ok_in = ON`,
   - `exciter_ready = ON`,
   - `plant_fault = OFF`,
   - `field_current_present = ON` o CH6 sobre umbral,
   - `motor_synchronized = ON`.
3. Si `fault_out = ON`, detener la prueba y revisar el codigo de falla en HMI/API.

## Secuencia exacta verificada en banco -- llego a RUNNING sostenido (2026-08-11)

Esto es una receta literal, con comandos, de la corrida que efectivamente llego a
`fsm_state=7 (RUNNING)` sostenido (`relay_fax=true`, `fault_code=0`) en el banco de
`192.168.1.50`. Combina los Pasos 1-8 de arriba con el timing exacto que hizo falta --
sin este timing, la secuencia falla de forma reproducible en 3 puntos distintos
(documentados abajo). Usar esto como receta directa; usar los Pasos 1-8 de arriba
como referencia de POR QUE cada cosa hace falta.

**Que es por pantalla tactil de la PZ (fisico, en persona) y que es por la web del HIL:**

| Accion | Donde | Quien |
|---|---|---|
| Corregir permisivos (una vez por cada power-cycle de la PZ, no persiste) | API/curl | Tecnico remoto, por red |
| ARMAR + READY | Web HIL (`192.168.1.120:8080`) | Tecnico remoto, por red |
| **START** | **Pantalla tactil HMI de la PZ** | **Alguien fisicamente en el banco** |
| full_volts, discharge_current_present, pulso, field_current_present, motor_synchronized | Web HIL | Tecnico remoto, por red |
| **ACK y RESET despues de cualquier falla** | **Pantalla tactil HMI de la PZ** | **Alguien fisicamente en el banco** |
| SAFE_STOP | Web HIL | Tecnico remoto, por red |

O sea: hace falta una persona en el banco solo para START/ACK/RESET (los comandos que
mueven la planta de verdad) -- todo lo demas se puede operar remoto desde la web del
HIL. Ver tambien "Coordinacion antes de operar el HIL remoto" al inicio de este
documento: nunca tocar la web del HIL mientras la persona en el banco esta en medio de
una secuencia manual.

### 0. Corregir permisivos (repetir despues de CADA reinicio de la PZ)

```powershell
curl.exe -u operator:SIE2 -H "Content-Type: application/json" -X POST --data-raw "{\"permissive_enable_mask\":255,\"permissive_required_start_mask\":131,\"permissive_required_run_mask\":229,\"permissive_active_high_mask\":255,\"permissive_bypass_mask\":0}" http://192.168.1.50/api/settings/save
```

**Bug conocido, sin arreglar todavia:** esta llamada casi siempre responde
`{"ok":false,"error":"AXI_APPLY_FAILED"}` (HTTP 500) -- ignorar ese error, el valor SI
queda aplicado en vivo (confirmarlo con `curl.exe http://192.168.1.50/status`, tiene
que dar `fault_active:false`). Lo que SI es real: nunca llega a guardarse en la SD
(`nexus_settings_store_save()` no se ejecuta si falla la verificacion AXI previa), asi
que **hay que repetir este paso despues de cada power-cycle de la PZ**, no solo la
primera vez.

### 1. Ponovo estable

Vab/Vbc/Vca balanceados a 60 Hz, corrientes Ia/Ib/Ic en 0 A. Confirmar
`signal_present=true`, `freq_valid=true`, `sync_reference_ok=true` en
`http://192.168.1.50/api/measurement/status`.

### 2. ARMAR + READY (web HIL)

```powershell
$armBody = @{ gnd=$true; no_5v=$true; power_disabled=$true; series_resistors=$true } | ConvertTo-Json
Invoke-WebRequest -Uri "http://192.168.1.120:8080/api/arm" -Method POST -Body $armBody -ContentType "application/json"
Invoke-WebRequest -Uri "http://192.168.1.120:8080/api/ready" -Method POST -Body "{}" -ContentType "application/json"
```

Confirmar `relay_56k:true` en `http://192.168.1.50/status`.

### 3. START -- fisico, en el HMI de la PZ

La persona en el banco toca START en la pantalla. Confirmar `motor_run:true` en
`/status` (`fsm_state` pasa a `2`).

### 4/5. full_volts + descarga + pulso -- TODO JUNTO, sin pausas entre medio

**Punto de falla real #1:** el reloj de 3 segundos (`disc_current_on_timeout_ms=3000`
en el RTL, NO expuesto en `/api/settings`, no se puede cambiar por API) empieza a
contar en el instante en que `full_volts` se activa (ahi la FSM entra a
`WAIT_DISCHARGE`). Si `discharge_current_present` y el pulso se activan en un mensaje
o llamada separada más tarde, ese hueco de tiempo se come el margen y sale
`fault_code=4 DISCHARGE_CIRCUIT`. Por eso estos tres van en la misma tanda de
comandos, sin esperar confirmacion entre uno y otro:

**Punto de falla real #2:** el pulso `discharge_extinction_pulse` pasa por un filtro
de debounce de 100ms en el RTL (`pz_sync_control_axi_top.v:1124`,
`.DEBOUNCE_MS(100)`). A 5 Hz (semiperiodo = 100ms exactos) casi todos los flancos se
descartan como rebote y la frecuencia nunca se valida. **Usar `discharge_pulse_hz=2`**
(semiperiodo 250ms, 2.5x margen) en `rpi_digital_hil_config.json` -- ya viene
configurado asi en el repo.

```powershell
$b1 = @{ signal="full_volts"; value=1 } | ConvertTo-Json
Invoke-WebRequest -Uri "http://192.168.1.120:8080/api/output" -Method POST -Body $b1 -ContentType "application/json"

$b2 = @{ signal="discharge_current_present"; value=1 } | ConvertTo-Json
Invoke-WebRequest -Uri "http://192.168.1.120:8080/api/output" -Method POST -Body $b2 -ContentType "application/json"

$b3 = @{ running=$true } | ConvertTo-Json
Invoke-WebRequest -Uri "http://192.168.1.120:8080/api/pulse" -Method POST -Body $b3 -ContentType "application/json"
```

### 6. Esperar ~900ms, despues field_current_present

**Punto de falla real #3:** activar `field_current_present` DEMASIADO PRONTO (mientras
la FSM todavia esta en `ST_WAIT_DISCHARGE`) dispara `FAULT_DC_BEFORE_START` -- la
proteccion de "corriente de campo antes de tiempo" cubre justo ese estado. Pero
activarlo muy tarde tambien falla: `ST_VERIFY_FIELD` solo espera
`field_current_on_timeout_ms=1500ms` antes de disparar `fault_code=5
NO_FIELD_CURRENT`. Esperar ~900ms desde el paso anterior (tiempo de sobra para que la
frecuencia de descarga se valide a 2 Hz y la FSM salga de `WAIT_DISCHARGE`) dio margen
para las dos ventanas:

```powershell
Start-Sleep -Milliseconds 900
$b4 = @{ signal="field_current_present"; value=1 } | ConvertTo-Json
Invoke-WebRequest -Uri "http://192.168.1.120:8080/api/output" -Method POST -Body $b4 -ContentType "application/json"
```

La FSM entra sola a `RUNNING` (`fsm_state=7`) en cuanto `field_current_present` se
sincroniza -- no hace falta ningun comando adicional para ese salto.

### 7. motor_synchronized -- dentro de los 5s siguientes a RUNNING

`ST_RUNNING` dispara `FAULT_PULL_OUT` si `motor_synchronized` no llega dentro de
`pullout_timeout_ms=5000ms`. Con margen de sobra, activarlo apenas se confirma
RUNNING:

```powershell
$b5 = @{ signal="motor_synchronized"; value=1 } | ConvertTo-Json
Invoke-WebRequest -Uri "http://192.168.1.120:8080/api/output" -Method POST -Body $b5 -ContentType "application/json"
```

### 8. Confirmar RUNNING sostenido

```powershell
Invoke-WebRequest -Uri "http://192.168.1.50/status" -UseBasicParsing
```

Esperado: `fsm_state:7`, `fault_code:0`, `plant.relay_fax:true`, `plant.fault_out:false`,
sostenido en varias consultas seguidas (no solo un instante).

### Si algo falla en el medio

1. `discharge_current_present` y demas salidas quedan asertadas -- primero
   `SAFE_STOP` en la web del HIL (baja todo a reposo, condicion necesaria para que la
   PZ permita RESET: `reset_safe` en el RTL exige `!field_current_sync &&
   !discharge_sync`).
2. **ACK y RESET fisico en el HMI de la PZ** (no por API -- ver la tabla de arriba).
3. Confirmar `fault_active:false` y `fsm_state:1 (READY)` en `/status`.
4. Volver al Paso 0 (los permisivos NO sobreviven si hubo power-cycle de por medio;
   si solo fue ACK+RESET sin apagar la PZ, siguen aplicados) y reintentar desde el
   Paso 3.

### Nota sobre el touch de la PZ

Durante esta misma sesion el panel tactil de la PZ dejo de responder dos veces, sin
relacion aparente con los pasos de arriba (una vez coincidiendo con actividad
electrica real del banco). La solucion que funciono las dos veces fue un
**power-cycle completo de la PZ** (apagar/prender, no solo desconectar el cable USB
del panel -- eso funciono la primera vez pero no la segunda). Si el touch no responde
y hay que dar START/ACK/RESET, hacer el power-cycle, esperar a que arranque, y volver
al Paso 0 de esta secuencia (los permisivos tampoco sobreviven un power-cycle).

## Secuencia de paro seguro

1. En la interfaz web presionar `SAFE_STOP`.
2. Apagar tren de pulsos de descarga si sigue activo.
3. Bajar Ponovo:
   - CH6 FieldCurrent a 0 A.
   - CH7 DischargeCurrent a 0 A.
   - Ia/Ib/Ic a 0 A.
   - Vab/Vbc/Vca a 0 V.
4. Apagar salidas Ponovo.
5. Apagar PZ si se va a cambiar cableado.

## Checklist rapido RUNNING

| Etapa | Ponovo | Raspberry web | Esperado en PZ |
|---|---|---|---|
| READY analogico | Vab/Vbc/Vca 60 Hz balanceados | READY | medicion valida, sin falla |
| Permisivos | sin cambio | thermal OK ON, exciter ON, plant fault OFF | `relay_56k` ON |
| START | sin cambio | observar `motor_run` | `motor_run` ON |
| Full volts | voltajes estables | `full_volts` ON | avanza a descarga |
| Descarga | CH7 sobre umbral si aplica | `discharge_current_present` ON y pulse ON | `dst_cmd` activo, sin falla |
| Campo | CH6 sobre umbral si aplica | `field_current_present` ON | `field_enable` ON |
| Sincronismo | 60 Hz estable, secuencia positiva | `motor_synchronized` ON | RUNNING / `relay_fax` ON |

## Diagnostico si no llega a RUNNING

| Sintoma | Revisar |
|---|---|
| No entra READY | Usar la tabla "Diagnostico si no entra READY"; READY requiere medicion ADS valida y permisivos digitales |
| No aparece `motor_run` | START real desde HMI/API, estado de fault/lockout |
| Se queda en descarga | `full_volts`, `discharge_current_present`, CH7, tren `discharge_extinction_pulse`, `dst_cmd`. Falla real observada en banco: `fault_code=4 DISCHARGE_CIRCUIT` -- revisar que `discharge_current_present` este realmente en `ON` (o CH7 sobre umbral) de forma sostenida durante toda la etapa, no solo un instante; si algo (incluida una llamada remota a `READY`/`SAFE_STOP`) lo vuelve a poner en 0 a mitad de la secuencia, la PZ la interpreta como perdida del circuito de descarga |
| No habilita campo | `field_enable`, condicion de descarga, `field_apply_ok`, PLL/ZC/sync config |
| Falla al activar campo | CH6/`field_current_present`, protecciones, `fault_out`, codigo de falla |
| No declara RUNNING | `motor_synchronized`, secuencia/frecuencia Ponovo, `relay_fax`, pull-out |

## Diagnostico si no entra READY

Estado esperado antes de START:

| Punto | Esperado | Si no se cumple |
|---|---:|---|
| Raspberry `thermal_ok_in` | 1 | Revisar GPIO5/pin 29 hacia JM2-11/G15 |
| Raspberry `exciter_ready` | 1 | Revisar GPIO6/pin 31 hacia JM2-13/K16 |
| Raspberry `plant_fault` | 0 | Asegurar boton OFF; revisar GPIO13/pin 33 hacia JM2-19/N16 |
| Raspberry `full_volts` | 0 | Debe quedar OFF antes de START; ahora esta en GPIO7/pin 26 hacia JM2-9/H15 |
| PZ `fault_out` | 0 | Si esta 1, resetear/ack fault desde HMI/API y revisar codigo de falla |
| PZ `relay_56k` | 1 | Indica READY real |
| Ponovo V1/V2/V3 | ON | Sin voltaje ADS valido no hay `measurement_online` |
| Frecuencia | 60 Hz estable | Ajustar frecuencia nominal/tolerancia si el proyecto esta configurado distinto |
| Secuencia | positiva | V1/V2/V3 deben corresponder a Vab/Vbc/Vca con angulos 0, -120, +120 |
| Corrientes | 0 A | Evitar protecciones durante READY |

Secuencia minima para depurar READY:

1. Presionar `SAFE_STOP` en la web.
2. Apagar `full_volts`, `field_current_present`, `motor_synchronized`, `discharge_current_present` y `discharge_extinction_pulse`.
3. Dejar `plant_fault = OFF`.
4. Encender Ponovo V1/V2/V3 a 60 Hz balanceados.
5. Confirmar en HMI/PZ que hay frecuencia y voltajes validos.
6. Presionar `ARMAR` y despues `READY` en la web.
7. Esperar `relay_56k = ON`.
8. Solo despues de `relay_56k = ON`, dar START desde HMI/API.
9. Solo despues de ver `motor_run = ON`, activar `full_volts`.

Si despues de esto `relay_56k` sigue en 0, el bloqueo ya no esta en la Raspberry; esta en `measurement_online`, `fault_latched`, configuracion de frecuencia/ADC, o un reset/ack pendiente en Nexus Sync.

---

Nexus by SIEZA 2026. Todos los derechos reservados.
