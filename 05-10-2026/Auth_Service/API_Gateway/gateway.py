import os

import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

app = FastAPI(title="El Asador - API Gateway")
VAULT_ADDR = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200").rstrip("/")
VAULT_TOKEN = os.getenv("VAULT_TOKEN")
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://127.0.0.1:8100").rstrip("/")
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8098").rstrip("/")
if not VAULT_TOKEN:
    raise RuntimeError("VAULT_TOKEN no está configurado")


class Credenciales(BaseModel):
    correo: str
    password: str


# Vault guarda los secretos de comunicación entre servicios.
async def obtener_secretos():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            respuesta = await client.get(
                f"{VAULT_ADDR}/v1/secret/data/gateway",
                headers={"X-Vault-Token": VAULT_TOKEN},
            )
        respuesta.raise_for_status()
        datos = respuesta.json()["data"]["data"]
        for nombre in ("auth_introspection_secret", "backend_shared_secret"):
            if not isinstance(datos[nombre], str) or not datos[nombre]:
                raise ValueError("Secreto inválido")
        return datos
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=503, detail="Gestor de secretos no disponible")


async def consultar_auth(ruta: str, datos: dict, secreto: str):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            respuesta = await client.post(
                f"{AUTH_SERVICE_URL}{ruta}", json=datos,
                headers={"X-Gateway-Auth-Secret": secreto},
            )
        if respuesta.status_code == 401:
            raise HTTPException(status_code=401, detail="Credenciales incorrectas")
        respuesta.raise_for_status()
        return respuesta.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(status_code=503, detail="Servicio de autenticación no disponible")


# El gateway pregunta al Auth Service si la sesión sigue activa.
async def sesion_actual(session_token: str | None = Cookie(default=None)):
    if not session_token:
        raise HTTPException(status_code=401, detail="Sesión requerida")
    secretos = await obtener_secretos()
    identidad = await consultar_auth("/introspect", {"token": session_token}, secretos["auth_introspection_secret"])
    if not identidad.get("active"):
        raise HTTPException(status_code=401, detail="Sesión inválida o expirada")
    if not isinstance(identidad.get("roles"), list) or not identidad.get("user_id") or not identidad.get("correo"):
        raise HTTPException(status_code=503, detail="Respuesta de autenticación inválida")
    return identidad, secretos


@app.get("/health")
def health():
    return {"status": "ok", "service": "API Gateway"}


@app.post("/auth/login")
async def login(datos: Credenciales, response: Response):
    secretos = await obtener_secretos()
    resultado = await consultar_auth("/login", datos.model_dump(), secretos["auth_introspection_secret"])
    token = resultado.get("access_token")
    duracion = resultado.get("expires_in")
    if not isinstance(token, str) or not token or not isinstance(duracion, int):
        raise HTTPException(status_code=503, detail="Respuesta de autenticación inválida")
    response.set_cookie("session_token", token, httponly=True, samesite="lax", max_age=duracion, path="/")
    return {"message": "Sesión iniciada"}


@app.get("/auth/me")
async def me(sesion=Depends(sesion_actual)):
    identidad, _ = sesion
    return {"user_id": identidad["user_id"], "correo": identidad["correo"], "roles": identidad["roles"]}


@app.post("/auth/logout")
async def logout(response: Response, session_token: str | None = Cookie(default=None), sesion=Depends(sesion_actual)):
    _, secretos = sesion
    await consultar_auth("/logout", {"token": session_token}, secretos["auth_introspection_secret"])
    response.delete_cookie("session_token", path="/", httponly=True, samesite="lax")
    return {"message": "Sesión finalizada"}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request, sesion=Depends(sesion_actual)):
    identidad, secretos = sesion
    # Las operaciones de escritura están reservadas al administrador.
    if request.method != "GET" and "admin" not in identidad["roles"]:
        raise HTTPException(status_code=403, detail="Se requiere rol de administrador")
    headers = {
        "X-Gateway-Secret": secretos["backend_shared_secret"],
        "X-Authenticated-User": identidad["user_id"],
        "X-Authenticated-Username": identidad["correo"],
        "X-Authenticated-Roles": ",".join(identidad["roles"]),
    }
    if request.headers.get("content-type"):
        headers["content-type"] = request.headers["content-type"]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            upstream = await client.request(
                method=request.method, url=f"{BACKEND_URL}/{path}",
                params=request.query_params, content=await request.body(), headers=headers,
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Backend no disponible")
    response_headers = {}
    if "content-type" in upstream.headers:
        response_headers["content-type"] = upstream.headers["content-type"]
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)
