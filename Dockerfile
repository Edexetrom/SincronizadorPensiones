# Utilizar una imagen oficial de Python ligera
FROM python:3.11-slim

# Evitar que Python escriba archivos .pyc en disco y habilitar el búfer de salida para logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Establecer directorio de trabajo
WORKDIR /app

# Instalar dependencias del sistema necesarias e incrementar seguridad
RUN pip install --no-cache-dir --upgrade pip

# Copiar el archivo de dependencias primero para aprovechar la caché de Docker
COPY requirements.txt .

# Instalar dependencias de Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código del script de sincronización
COPY Sync_Folios.py .

# Comando por defecto (sobrescrito en docker-compose, pero útil como valor predeterminado)
CMD ["python", "Sync_Folios.py"]
