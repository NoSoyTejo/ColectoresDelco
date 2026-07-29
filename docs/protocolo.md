# Protocolo del colector — según `Comados.doc`

Fuente oficial: [Comados.doc](Comados.doc)  
Texto extraído: [Comados_extracted.txt](Comados_extracted.txt)

## Parámetros serial

| Parámetro | Valor |
|-----------|--------|
| Baud rate | 9600 |
| Data bits | 8 |
| Parity | None (`N`) |
| Stop bits | 1 |
| Terminador | `\r\n` (probar `\r` si no responde) |

Medidores y cabeceras se completan a **12 dígitos** con ceros a la izquierda.

## Conexión por IP (TCP)

Tras `UnLock` la app lanza una **sonda multi-ruta**:

1. `VER` y `O` **sin** `QUIT` (algunos gateways rechazan QUIT pero aceptan consulta)
2. `QUIT` + `VER` (como en COM / convertidor transparente)
3. Reintento con fin de línea **CR** solo (`\r`)

| Resultado | Modo | Comportamiento |
|-----------|------|----------------|
| Respuesta útil en alguna ruta | **TCP completo** | Mantenimiento por IP; guarda estrategia (`no_quit` / `standard` / `cr`) |
| Solo `NO` | **TCP limitado** | Solo Login / QUIT / comando libre |

Botones: **Reprobar canal TCP** · **Diagnóstico IP profundo** (mapa QUIT/VER/O/i/WAKE).

Si el puerto solo implementa UnLock, no hay forma de inventar mantenimiento por software: hace falta COM, otro puerto TCP, o convertidor RS232↔Ethernet en modo **raw/transparente**.



| Parámetro | Valor |
|-----------|--------|
| Host | IP o hostname del colector / convertidor RS232↔Ethernet |
| Puerto TCP | editable (default **4001**) |
| Terminador | el mismo que en serial (`\r\n`) |

En la UI: selector **COM | IP**. Requiere que el equipo en esa IP **escuche TCP** y hable el protocolo de texto del colector. Si no hay módulo de red ni gateway, use cable COM.

## Flujo habitual

1. `QUIT` — detiene la lectura  
2. Comando de mantenimiento  
3. `WAKE` — inicializa  
4. `ZOOM` — start read  

Si tras `QUIT` responde **lock** → `PWR666666` hasta **unlock**.

La app **espera respuesta** entre cada paso de estas secuencias.

## Los 13 comandos del manual

| # | Función | Secuencia en la app |
|---|---------|---------------------|
| 1 | Lectura directa | `R`+medidor(12)+`F031812` (F18+F12). Cabecera/display obsoletos. |
| 2 | Refresco forzado | `QUIT` → `SGXF`+medidor → `WAKE` → `ZOOM` |
| 3 | Borrar medidor | `QUIT` → `E`+medidor → `WAKE` → `ZOOM` |
| 4 | Agregar medidor | `QUIT` → `A`+medidor[+cabecera] → `WAKE` → `ZOOM`. CP4: `A…00` |
| 5 | Login | `PWR666666` (auto al conectar / ante lock) |
| 6 | Versión | `VER` (`CCE16`=v1, `SLD16`=v2) |
| 7 | Cantidad medidores | `QUIT` → `O` (IDnum, AMRsw…) |
| 8 | Reiniciar ciclo K | `QUIT` → `O` (leer AMRsw e *inferir*) → si ≠`10100000`: `k=10100000` → `O` → `QUIT` → `WAKE` → `ZOOM` |
| 9 | Forzar lecturas | `QUIT` → `WAKE` → `ZOOM` |
| 10 | Ver horarios | `QUIT` → `i` (`NO` = sin horarios) |
| 11 | Insertar horarios | **No documentado en el manual.** La app prueba `I`+HHMM+HHMM; en SLD16 v3.1.41 responde `NO` aunque `i`=vacío. Falta captura desde RemoteCOM. |
| 12 | Fecha/hora | `C`+AAMMDDHHMMSS |
| 13 | Borrar base | `QUIT` (OK) → `DEL` |

### §10 Horarios polling (comando `i`)

Respuesta tipica del manual:

```
Vendo horarios de pooling
Total de poolings: 3     ← cantidad de horarios ingresados en el colector
1 - De 00:00 até 03:59   ← horario polling
2 - De 04:00 até 15:59
3 - De 16:00 até 23:59
```

Las 3 franjas del ejemplo: **00:00–03:59**, **04:00–15:59**, **16:00–23:59**.
En la UI: Download (`i`) muestra el total + franjas; botón **3 franjas (§10)** carga esa plantilla.

### Notas de comportamiento

- **§8 / AMRsw**: el manual muestra `10000000` solo como *ejemplo*. Con lo que devuelva `O` se infiere: si es `10100000` no hace falta `k=`; cualquier otro valor (p.ej. `00000000`) → enviar `k=10100000`.
- **K / O / WAKE / ZOOM / A / E responden NO**: la secuencia **se detiene** (no sigue con el siguiente comando). No reintentar en bucle.
- **QUIT responde NO**: a menudo ya estaba detenido; no reenviar QUIT en bucle.
- **i responde NO**: sin horarios (informativo).
- **Lock**: login automático `PWR666666` (máx. 3); reintento del comando tras UnLock (máx. 2).
- **WAKE / ROUTERERROR**: un solo `QUIT` y luego `QUIT→WAKE→ZOOM`.

### Interpretación lectura directa

Ejemplo: `000023388410 F031812 00 000025710040 090527060924`

- Lectura `000025710040` → **2571.40**  
- Fecha `090527060924` → **2009-05-27 06:09:24**  

## Código

- `src/protocol.py` — comandos y parsers  
- `src/serial_client.py` — transporte COM  
- `src/tcp_client.py` — transporte TCP (IP)  
- `src/ui/app_window.py` — pestaña Comandos + selector COM/IP  
- `src/ui/extra_tabs.py` — Reloj / horarios / medidores / carga masiva  
