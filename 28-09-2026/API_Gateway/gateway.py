import os
import secrets

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

app = FastAPI(title="El Asador - API Gateway")
security = HTTPBearer(auto_error=False)
VAULT_ADDR = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200").rstrip("/")
VAULT_TOKEN = os.getenv("VAULT_TOKEN")
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8098").rstrip("/")

if not VAULT_TOKEN:
    raise RuntimeError("VAULT_TOKEN no está configurado")


# Consultamos Vault para obtener el token del cliente y el secreto interno.
async def obtener_secretos():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{VAULT_ADDR}/v1/secret/data/gateway",
                headers={"X-Vault-Token": VAULT_TOKEN},
            )
        response.raise_for_status()
        datos = response.json()["data"]["data"]
        token = datos["client_token"]
        secreto = datos["backend_shared_secret"]
        if not isinstance(token, str) or not token or not isinstance(secreto, str) or not secreto:
            raise ValueError("Secretos inválidos")
        return datos
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=500, detail="No fue posible acceder a los secretos")


# Revisamos el token antes de permitir el acceso a la API.
async def autenticar_cliente(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Bearer token requerido")
    datos = await obtener_secretos()
    if not secrets.compare_digest(credentials.credentials, datos["client_token"]):
        raise HTTPException(status_code=401, detail="Token inválido")
    return datos["backend_shared_secret"]


@app.get("/health")
def health():
    return {"status": "ok", "service": "API Gateway"}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request, secreto: str = Depends(autenticar_cliente)):
    # El backend recibe el secreto interno, no el token del cliente.
    headers = {"X-Gateway-Secret": secreto}
    if request.headers.get("content-type"):
        headers["content-type"] = request.headers["content-type"]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            upstream = await client.request(
                method=request.method,
                url=f"{BACKEND_URL}/{path}",
                params=request.query_params,
                content=await request.body(),
                headers=headers,
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Backend no disponible")
    # Devolvemos al cliente la respuesta y el estado del backend.
    response_headers = {}
    if "content-type" in upstream.headers:
        response_headers["content-type"] = upstream.headers["content-type"]
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)
