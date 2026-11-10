# El Asador: FastAPI y MongoDB

API de productos desarrollada con FastAPI y MongoDB.

## Requisitos e inicio

Se necesitan Python, pip y MongoDB Server. Desde esta carpeta, en PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:INTERNAL_GATEWAY_SECRET = Read-Host "Secreto interno del gateway"
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8098
```

MongoDB debe estar iniciado antes. Configure también `INTERNAL_GATEWAY_SECRET` con el mismo valor de `backend_shared_secret` almacenado en Vault antes de iniciar FastAPI. Por defecto se usa `mongodb://127.0.0.1:27017/el_asador`, colección `productos`, la misma que usa GraphQL. Se puede elegir otra base mediante la variable de entorno `MONGODB_URI`, incluyendo el nombre de la base en la URI. La API no carga datos por sí misma: los seis platos se pueden cargar con el script de carga de GraphQL.

La documentación interactiva está en `http://127.0.0.1:8098/docs`.

| Método | Ruta | Operación |
| --- | --- | --- |
| GET | `/productos` | Listar productos |
| GET | `/productos/{producto_id}` | Consultar por ID de MongoDB |
| POST | `/productos` | Crear producto |
| PUT | `/productos/{producto_id}` | Actualizar todos los campos del producto |
| DELETE | `/productos/{producto_id}` | Eliminar producto |

Para crear o actualizar se envía JSON con `numero`, `nombre`, `descripcion`, `precio` e `imagen`. El número es positivo y único; el precio es un entero no negativo en pesos chilenos. El `id` que devuelve la API es el identificador de MongoDB, mientras que `numero` identifica el plato del menú.

La API responde 400 para un ID inválido, 404 si el producto no existe, 409 por un número duplicado y 422 para datos inválidos. La eliminación exitosa responde 204. La API comprueba el secreto interno enviado por el gateway; usar solamente en un entorno local. Las operaciones de escritura modifican la colección configurada.
