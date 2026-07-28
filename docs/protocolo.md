# Protocolo del colector — según `ComandosColectores.doc`

Fuente oficial: [ComandosColectores.doc](ComandosColectores.doc)  
Texto extraído: [ComandosColectores.txt](ComandosColectores.txt)

## Parámetros serial (defaults MVP)

| Parámetro | Valor por defecto | Notas |
|-----------|-------------------|--------|
| Baud rate | 9600 | Configurable en la UI |
| Data bits | 8 | |
| Parity | None (`N`) | |
| Stop bits | 1 | |
| Terminador | `\r\n` | Probar `\r` si no responde |
| Encoding | ASCII | |

Medidores y cabeceras se completan siempre a **12 dígitos** con ceros a la izquierda.

## Flujo habitual

1. `QUIT` — detiene la lectura del colector  
2. Comando de mantenimiento (login, agregar, borrar, display, etc.)  
3. `WAKE`  
4. `ZOOM` — vuelve a iniciar lecturas (`start read`)

Si tras `QUIT` responde **lock**, enviar login `PWR666666` hasta obtener **unlock**.

## Mapa de comandos

| Función | Comando | Notas |
|---------|---------|--------|
| Login | `PWR666666` | Cuando hay lock |
| Versión | `VER` | v1≈CCE16… / v2≈SLD16… |
| Cantidad medidores | `O` | Muestra AMRsw, IDnum, etc. |
| Horarios polling | `i` | Tras QUIT |
| Lectura directa | `R`+medidor(12)+`F03`+flags | Ej. `R000023388410F0318121606` |
| Display ON/OFF | `XFKG`+medidor+`01`/`00` | Tras QUIT |
| Refresco forzado | `SGXF`+medidor | Tras QUIT |
| Borrar medidor | `E`+medidor | Tras QUIT |
| Agregar medidor | `A`+medidor+cabecera | O `A`+medidor+`00` (CP4) |
| Reiniciar ciclo (K) | `k=10100000` | AMRsw debe quedar `10100000` |
| Borrar base | `DEL` | Tras QUIT OK — borra todos los medidores |
| Set fecha/hora | `C`+AAMMDDHHMMSS | 12 dígitos |

### Flags de lectura directa

- `18` lectura  
- `12` fecha/hora  
- `16` cabecera  
- `06` status display  

### Lectura multi-tarifa (T1–T4)

Según logs RemoteCOM / Leitura Massiva:

| Flag | Campo |
|------|--------|
| F18 | T1 |
| F20 | T2 |
| F21 | T3 |
| F22 | T4 |
| F19 | Total |
| F12 | Fecha |

Comando: `R` + medidor(12) + `F03182021221912`  
Listado por índice: `R0000F03182021221912`, `R0001F03…` (usar comando `O` para cantidad).

### Carga masiva

No hay comando especial: secuencia `QUIT` → varios `A…` → `WAKE` → `ZOOM`.  
Formatos en la pestaña **Carga masiva** de la app.


Ejemplo: `000023388410 F0318121606 01 000025710040 090527060924 000090059613 01`

- Lectura `000025710040` → **2571.40**  
- Fecha `090527060924` → **09-05-27 06:09:24**  
- Display `01` = ON, `00` = OFF  

## Código

- Implementación: `src/protocol.py`  
- Botones de UI: `src/ui/app_window.py`
