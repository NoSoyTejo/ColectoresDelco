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

### Interpretación de respuesta de lectura

Ejemplo: `000023388410 F0318121606 01 000025710040 090527060924 000090059613 01`

- Lectura `000025710040` → **2571.40**  
- Fecha `090527060924` → **09-05-27 06:09:24**  
- Display `01` = ON, `00` = OFF  

## Código

- Implementación: `src/protocol.py`  
- Botones de UI: `src/ui/app_window.py`
