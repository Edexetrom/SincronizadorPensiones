import os
import re
import time
import socket
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Cargar variables de entorno desde el archivo .env
load_dotenv()

def obtener_servicio_sheets():
    """Configura y retorna el servicio de la API de Google Sheets V4"""
    # Los scopes definen los permisos (lectura y escritura en hojas de cálculo)
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
    
    # Manejamos los saltos de línea de la llave privada que suelen romperse en los .env
    private_key = os.getenv("GOOGLE_PRIVATE_KEY")
    if private_key:
        private_key = private_key.strip("'\"")
        # Si la llave está en base64 (no contiene el encabezado típico) la decodificamos
        if "-----BEGIN PRIVATE KEY-----" not in private_key:
            import base64
            try:
                decoded_key = base64.b64decode(private_key).decode('utf-8')
                if "-----BEGIN PRIVATE KEY-----" in decoded_key:
                    private_key = decoded_key
            except Exception:
                pass
        private_key = private_key.replace('\\n', '\n')
    
    # Construimos el diccionario de credenciales de la Service Account
    creds_dict = {
        "type": "service_account",
        "private_key": private_key,
        "client_email": os.getenv("GOOGLE_CLIENT_EMAIL"),
        "token_uri": "https://oauth2.googleapis.com/token"
    }
    
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return build('sheets', 'v4', credentials=creds)

def extraer_id_spreadsheet(url):
    """Extrae el ID único del libro a partir de la URL de Google Sheets"""
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    return match.group(1) if match else None

def extraer_gid(url):
    """Extrae el GID (ID interno de la hoja) directamente de la URL para evitar descargar metadata"""
    match = re.search(r'gid=([0-9]+)', url)
    return int(match.group(1)) if match else 0

def obtener_valor_seguro(fila, indice):
    """
    Retorna el valor de una celda de forma segura.
    La API de Google recorta las celdas vacías al final de una fila, 
    por lo que filas pueden tener longitudes distintas.
    """
    if indice == -1: 
        return ""
    if indice < len(fila): 
        valor = str(fila[indice]).strip()
        return valor if valor.lower() not in ['null', 'none'] else ""
    return ""

def reintentar_api(funcion, max_intentos=4):
    """Ejecuta peticiones a Google con un escudo protector anti-cortes (503, Timeouts)"""
    demora = 2
    for intento in range(max_intentos):
        try:
            return funcion()
        except Exception as e:
            if intento == max_intentos - 1:
                raise e # Si ya falló 4 veces, lanzamos el error
            print(f"      [!] Micro-corte de Google detectado. Reintentando en {demora}s... (Intento {intento + 1}/{max_intentos})")
            time.sleep(demora)
            demora *= 2 # Espera progresiva: 2s, 4s, 8s...

def consolidar_pensiones():
    socket.setdefaulttimeout(300) # Le da a Python 5 minutos de paciencia para la respuesta HTTP
    
    print("🚀 Iniciando proceso de consolidación...")
    tiempo_inicio = time.time()
    
    servicio = obtener_servicio_sheets()
    
    # Extraer IDs de las URLs en el .env
    id_obj = extraer_id_spreadsheet(os.getenv("Libro_Obj"))
    id_puente = extraer_id_spreadsheet(os.getenv("Libro_Puente"))
    id_control = extraer_id_spreadsheet(os.getenv("Libro_Contr2ol")) # Nota: usé el nombre exacto de tu .env
    
    # Nombres de las hojas de destino
    hoja_destino = os.getenv("Obj_Hoja1")
    
    print("📡 Descargando datos masivos de Control y Puente...")
    # Usamos batchGet para traer múltiples hojas de un libro en UNA SOLA llamada
    resultado_control = servicio.spreadsheets().values().batchGet(
        spreadsheetId=id_control,
        ranges=[
            f"{os.getenv('Control_Hoja3')}!A:L", # T-ra
            f"{os.getenv('Control_Hoja2')}!A:K", # S-da
            f"{os.getenv('Control_Hoja1')}!A:H"  # P-ra
        ]
    ).execute()
    
    resultado_puente = servicio.spreadsheets().values().batchGet(
        spreadsheetId=id_puente,
        ranges=[
            f"{os.getenv('Puente_Hoja2')}!A:L",  # PTE-CONT
            f"{os.getenv('Puente_Hoja1')}!A:L"   # SA
        ]
    ).execute()

    # Extracción de valores de las respuestas (o listas vacías si no hay datos)
    datos_t_ra = resultado_control.get('valueRanges', [])[0].get('values', [])
    datos_s_da = resultado_control.get('valueRanges', [])[1].get('values', [])
    datos_p_ra = resultado_control.get('valueRanges', [])[2].get('values', [])
    
    datos_pte_cont = resultado_puente.get('valueRanges', [])[0].get('values', [])
    datos_sa = resultado_puente.get('valueRanges', [])[1].get('values', [])

    print(f"✅ Descarga completada en {round(time.time() - tiempo_inicio, 2)} segundos.")

    # Función auxiliar para cargar y procesar datos de una hoja
    def cargar_hoja(datos, mapa, nombre_hoja):
        idx_pen, idx_nom, idx_sa, idx_p, idx_s, idx_t = mapa
        filas = []
        if len(datos) < 2:
            return filas
        for fila in reversed(datos[1:]):
            if not fila:
                continue
            pension = obtener_valor_seguro(fila, idx_pen)
            if not pension or pension.startswith('-'):
                continue
            sa = obtener_valor_seguro(fila, idx_sa)
            # Validar SA (debe comenzar con 'SA-')
            if sa and not sa.lower().startswith('sa-'):
                sa = ""
            p = obtener_valor_seguro(fila, idx_p)
            s = obtener_valor_seguro(fila, idx_s)
            t = obtener_valor_seguro(fila, idx_t)
            nombre = obtener_valor_seguro(fila, idx_nom)
            
            filas.append({
                "pension": pension,
                "nombre": nombre,
                "sa": sa,
                "p": p,
                "s": s,
                "t": t
            })
        return filas

    print("🧠 Procesando jerarquías y eliminando duplicados en memoria...")
    
    # Cargar datos de todas las hojas (de abajo hacia arriba)
    filas_sa = cargar_hoja(datos_sa, [10, 11, 8, -1, -1, -1], "SA")
    filas_pte_cont = cargar_hoja(datos_pte_cont, [10, 11, 8, 5, -1, -1], "PTE-CONT")
    filas_p_ra = cargar_hoja(datos_p_ra, [6, 7, 4, 1, -1, -1], "P-ra")
    filas_s_da = cargar_hoja(datos_s_da, [8, 9, 6, 3, 1, -1], "S-da")
    filas_t_ra = cargar_hoja(datos_t_ra, [10, 11, 8, 5, 3, 1], "T-ra")

    # Construir diccionarios de búsqueda con llaves compuestas
    dict_sa = {}
    for r in filas_sa:
        key = (r["pension"], r["sa"])
        if key not in dict_sa:
            dict_sa[key] = r["nombre"]

    dict_p = {}
    # P-ra (menor prioridad)
    for r in filas_p_ra:
        key = (r["pension"], r["sa"])
        if key not in dict_p:
            dict_p[key] = (r["p"], r["nombre"], "P-ra")
    # PTE-CONT (mayor prioridad)
    seen_pte_cont = set()
    for r in filas_pte_cont:
        key = (r["pension"], r["sa"])
        if key not in seen_pte_cont:
            dict_p[key] = (r["p"], r["nombre"], "PTE-CONT")
            seen_pte_cont.add(key)

    dict_s = {}
    for r in filas_s_da:
        key = (r["pension"], r["sa"], r["p"])
        if key not in dict_s:
            dict_s[key] = (r["s"], r["nombre"])

    dict_t = {}
    for r in filas_t_ra:
        key = (r["pension"], r["sa"], r["p"], r["s"])
        if key not in dict_t:
            dict_t[key] = (r["t"], r["nombre"])

    # Consolidación escalonada
    registros_consolidados = []
    processed_sa = set()
    processed_p = set()
    processed_s = set()

    # Paso 1: Procesar desde SA
    for (pension, sa), nombre_sa in dict_sa.items():
        p_key = (pension, sa)
        p_val, nombre_p, _ = dict_p.get(p_key, ("", "", ""))
        
        s_key = (pension, sa, p_val)
        s_val, nombre_s = dict_s.get(s_key, ("", ""))
        
        t_key = (pension, sa, p_val, s_val)
        t_val, nombre_t = dict_t.get(t_key, ("", ""))
        
        nombre_final = nombre_sa or nombre_p or nombre_s or nombre_t
        
        registros_consolidados.append([pension, nombre_final, sa, p_val, s_val, t_val])
        
        processed_sa.add((pension, sa))
        if p_val:
            processed_p.add((pension, sa, p_val))
        if s_val:
            processed_s.add((pension, sa, p_val, s_val))

    # Paso 2: Procesar desde P
    for (pension, sa), (p_val, nombre_p, _) in dict_p.items():
        if (pension, sa) in processed_sa:
            continue
            
        s_key = (pension, sa, p_val)
        s_val, nombre_s = dict_s.get(s_key, ("", ""))
        
        t_key = (pension, sa, p_val, s_val)
        t_val, nombre_t = dict_t.get(t_key, ("", ""))
        
        nombre_final = nombre_p or nombre_s or nombre_t
        
        registros_consolidados.append([pension, nombre_final, sa, p_val, s_val, t_val])
        
        processed_sa.add((pension, sa))
        if p_val:
            processed_p.add((pension, sa, p_val))
        if s_val:
            processed_s.add((pension, sa, p_val, s_val))

    # Paso 3: Procesar desde S
    for (pension, sa, p_val), (s_val, nombre_s) in dict_s.items():
        if (pension, sa, p_val) in processed_p:
            continue
            
        t_key = (pension, sa, p_val, s_val)
        t_val, nombre_t = dict_t.get(t_key, ("", ""))
        
        nombre_final = nombre_s or nombre_t
        
        registros_consolidados.append([pension, nombre_final, sa, p_val, s_val, t_val])
        
        processed_sa.add((pension, sa))
        if p_val:
            processed_p.add((pension, sa, p_val))
        if s_val:
            processed_s.add((pension, sa, p_val, s_val))

    # Paso 4: Procesar desde T
    for (pension, sa, p_val, s_val), (t_val, nombre_t) in dict_t.items():
        if (pension, sa, p_val, s_val) in processed_s:
            continue
            
        registros_consolidados.append([pension, nombre_t, sa, p_val, s_val, t_val])

    print("📝 Preparando y ordenando datos...")
    
    # Extraemos los valores consolidados
    datos_para_ordenar = registros_consolidados

    # Función inteligente para manejar números vs texto vs celdas vacías en los folios
    def llave_orden(fila):
        def procesar_celda(valor):
            v = str(valor).strip()
            if not v:
                return (2, 0, "") # Peso 2: Las celdas vacías se van hasta el fondo
            if v.isdigit():
                return (0, int(v), "") # Peso 0: Los números van primero y se ordenan matemáticamente
            return (1, 0, v) # Peso 1: Textos/alfanuméricos van en medio
            
        # Jerarquía de orden: TERCERA (índice 5), SEGUNDA (4), PRIMERA (3), SA (2)
        return (
            procesar_celda(fila[5]),
            procesar_celda(fila[4]),
            procesar_celda(fila[3]),
            procesar_celda(fila[2])
        )

    # Ordenamos la matriz de menor a mayor basado en la jerarquía
    datos_para_ordenar.sort(key=llave_orden)
    
    # Armamos la matriz final colocando el encabezado hasta arriba
    matriz_salida = [["# DE PENSION", "NOMBRE", "SA", "PRIMERA", "SEGUNDA", "TERCERA"]] + datos_para_ordenar
        
    total_registros = len(matriz_salida) - 1

    # Escribir los datos en la hoja de destino ("Libro_Obj")
    try:
        rango_limpieza = f"{hoja_destino}!A1:F30000"
        rango_escritura = f"{hoja_destino}!A1:F{len(matriz_salida)}"
        
        print("🧹 Limpiando hoja destino...")
        reintentar_api(lambda: servicio.spreadsheets().values().clear(
            spreadsheetId=id_obj,
            range=rango_limpieza
        ).execute())
        
        print(f"💾 Escribiendo {total_registros} registros consolidados y ordenados...")
        cuerpo_datos = {"values": matriz_salida}
        
        reintentar_api(lambda: servicio.spreadsheets().values().update(
            spreadsheetId=id_obj,
            range=rango_escritura,
            valueInputOption="RAW",
            body=cuerpo_datos
        ).execute())
        
        print("🎨 Aplicando formato...")
        url_obj = os.getenv("Libro_Obj")
        sheet_id_destino = extraer_gid(url_obj)

        formato_request = {
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id_destino,
                            "startRowIndex": 0, "endRowIndex": 1,
                            "startColumnIndex": 0, "endColumnIndex": 6
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": {"red": 0.85, "green": 0.91, "blue": 0.82}, # Verde claro
                                "textFormat": {"bold": True}
                            }
                        },
                        "fields": "userEnteredFormat(backgroundColor,textFormat)"
                    }
                }
            ]
        }
        
        reintentar_api(lambda: servicio.spreadsheets().batchUpdate(
            spreadsheetId=id_obj, 
            body=formato_request
        ).execute())

        tiempo_total = round(time.time() - tiempo_inicio, 2)
        print(f"✨ ¡ÉXITO! Proceso completado en {tiempo_total} segundos.")
        
    except Exception as e:
        print(f"❌ Error crítico al escribir en Google Sheets: {e}")

if __name__ == "__main__":
    consolidar_pensiones()