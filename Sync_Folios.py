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

    # Orden de procesamiento de menor jerarquía a mayor jerarquía.
    # El último en procesarse (SA) tendrá la última palabra (sobrescribe).
    # Formato de mapeo: [Indice_Pension, Nombre, SA, P, S, T]
    configuraciones = [
        {"nombre": "T-ra", "datos": datos_t_ra, "mapa": [10, 11, 8, 5, 3, 1]},
        {"nombre": "S-da", "datos": datos_s_da, "mapa": [8, 9, 6, 3, 1, -1]},
        {"nombre": "P-ra", "datos": datos_p_ra, "mapa": [6, 7, 4, 1, -1, -1]},
        {"nombre": "PTE-CONT", "datos": datos_pte_cont, "mapa": [10, 11, 8, 5, -1, -1]},
        {"nombre": "SA", "datos": datos_sa, "mapa": [10, 11, 8, -1, -1, -1]}
    ]

    diccionario_maestro = {}

    print("🧠 Procesando jerarquías y eliminando duplicados en memoria...")
    
    for config in configuraciones:
        datos = config["datos"]
        idx_pen, idx_nom, idx_sa, idx_p, idx_s, idx_t = config["mapa"]
        
        # Ignoramos encabezados (asumiendo que están en la fila 0)
        if len(datos) < 2:
            continue
            
        pensiones_vistas_esta_hoja = set()
        
        # Leemos la matriz de abajo hacia arriba (más recientes primero)
        for fila in reversed(datos[1:]):
            if not fila:
                continue
                
            # Extraer pensión
            pension = obtener_valor_seguro(fila, idx_pen)
            
            # Reglas de descarte
            if not pension or pension.startswith('-'):
                continue
                
            # Evitar procesar duplicados dentro de la misma hoja 
            # (ya leímos el más reciente al ir de abajo hacia arriba)
            if pension in pensiones_vistas_esta_hoja:
                continue
            
            pensiones_vistas_esta_hoja.add(pension)
            
            # Inicializar o recuperar el registro
            if pension not in diccionario_maestro:
                # [Pension, Nombre, SA, PRIMERA, SEGUNDA, TERCERA]
                diccionario_maestro[pension] = [pension, "", "", "", "", ""]
            
            registro = diccionario_maestro[pension]
            
            # Helper interno para sobreescribir valores si existen en la celda
            def inyectar_valor(idx_origen, pos_destino):
                val = obtener_valor_seguro(fila, idx_origen)
                if val: # Si hay valor en la celda, se sobreescribe
                    registro[pos_destino] = val
                    
            inyectar_valor(idx_nom, 1) # Nombre
            inyectar_valor(idx_sa, 2)  # SA
            inyectar_valor(idx_p, 3)   # PRIMERA
            inyectar_valor(idx_s, 4)   # SEGUNDA
            inyectar_valor(idx_t, 5)   # TERCERA

    print("📝 Preparando y ordenando datos...")
    
    # Extraemos solo los valores para ordenarlos en Python (super rápido)
    datos_para_ordenar = list(diccionario_maestro.values())

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
    matriz_salida = [["# DE PENSION", "Nombre", "SA", "PRIMERA", "SEGUNDA", "TERCERA"]] + datos_para_ordenar
        
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