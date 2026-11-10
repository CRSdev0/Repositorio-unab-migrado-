import os
import re
import time
from collections import deque

import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title="El Asador - API Gateway")
VAULT_ADDR = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200").rstrip("/")
VAULT_TOKEN = os.getenv("VAULT_TOKEN")
if not VAULT_TOKEN:
    raise RuntimeError("VAULT_TOKEN no está configurado")

TARGETS = {
    "inventory": os.getenv("INVENTORY_URL", "http://127.0.0.1:8098").rstrip("/"),
    "clients": os.getenv("CLIENTS_URL", "http://127.0.0.1:8110").rstrip("/"),
    "orders": os.getenv("ORDERS_URL", "http://127.0.0.1:8111").rstrip("/"),
    "payments": os.getenv("PAYMENTS_URL", "http://127.0.0.1:8112").rstrip("/"),
    "graphql": os.getenv("GRAPHQL_URL", "http://127.0.0.1:8090").rstrip("/"),
    "auth": os.getenv("AUTH_SERVICE_URL", "http://127.0.0.1:8100").rstrip("/"),
}


# Límite sencillo para el proceso local del gateway.
RATE_LIMIT = max(1, int(os.getenv("RATE_LIMIT_PER_MINUTE", "120")))
solicitudes = {}


@app.middleware("http")
async def limitar_solicitudes(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    ahora = time.monotonic()
    cliente = request.client.host if request.client else "desconocido"
    if len(solicitudes) > 10000:
        for clave in list(solicitudes):
            if not solicitudes[clave] or ahora - solicitudes[clave][-1] >= 60:
                del solicitudes[clave]
    historial = solicitudes.setdefault(cliente, deque())
    while historial and ahora - historial[0] >= 60:
        historial.popleft()
    if len(historial) >= RATE_LIMIT:
        return JSONResponse(status_code=429, content={"detail": "Demasiadas solicitudes"}, headers={"Retry-After": "60"})
    historial.append(ahora)
    return await call_next(request)


class Credenciales(BaseModel):
    correo: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class Registro(Credenciales):
    password: str = Field(min_length=8, max_length=256)
    nombre: str = Field(min_length=1, max_length=150)
    telefono: str | None = Field(default=None, max_length=30)
    direccion: str | None = Field(default=None, max_length=300)
    run: str | None = Field(default=None, max_length=20)
    fecha_nacimiento: str | None = Field(default=None, max_length=10)
    sexo: str | None = Field(default=None, max_length=40)
    region: str | None = Field(default=None, max_length=100)
    provincia: str | None = Field(default=None, max_length=100)
    comuna: str | None = Field(default=None, max_length=100)


async def obtener_secretos():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            respuesta = await client.get(f"{VAULT_ADDR}/v1/secret/data/gateway", headers={"X-Vault-Token": VAULT_TOKEN})
        respuesta.raise_for_status()
        datos = respuesta.json()["data"]["data"]
        for nombre in ("auth_introspection_secret", "inventory_secret", "clients_secret", "orders_secret", "payments_secret", "graphql_secret"):
            if not isinstance(datos[nombre], str) or not datos[nombre]:
                raise ValueError("Secreto inválido")
        return datos
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=503, detail="Gestor de secretos no disponible")


async def solicitar(method, url, **kwargs):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            return await client.request(method, url, **kwargs)
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Servicio no disponible")


async def auth_post(path, data, secretos):
    r = await solicitar("POST", TARGETS["auth"] + path, json=data,
                        headers={"X-Gateway-Auth-Secret": secretos["auth_introspection_secret"]})
    if r.status_code >= 500 or r.status_code == 403:
        raise HTTPException(status_code=503, detail="Servicio de autenticación no disponible")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail="Operación de autenticación rechazada")
    return r.json()


async def sesion_actual(session_token: str | None = Cookie(default=None)):
    if not session_token:
        raise HTTPException(status_code=401, detail="Sesión requerida")
    secretos = await obtener_secretos()
    identidad = await auth_post("/introspect", {"token": session_token}, secretos)
    if not identidad.get("active"):
        raise HTTPException(status_code=401, detail="Sesión inválida o expirada")
    if not identidad.get("user_id") or not identidad.get("correo") or not isinstance(identidad.get("roles"), list):
        raise HTTPException(status_code=503, detail="Respuesta de autenticación inválida")
    return identidad, secretos


@app.get("/health")
def health():
    return {"status": "ok", "service": "API Gateway"}


@app.post("/auth/register", status_code=201)
async def registrar(datos: Registro):
    secretos = await obtener_secretos()
    identidad = await auth_post("/register", {"correo": datos.correo, "password": datos.password}, secretos)
    user_id = identidad["user_id"]
    r = await solicitar("POST", TARGETS["clients"] + "/internal/clientes",
        headers={"X-Gateway-Secret": secretos["clients_secret"]},
        json={**datos.model_dump(exclude={"password"}), "user_id": user_id, "correo": identidad["correo"]})
    if r.status_code != 201:
        raise HTTPException(status_code=503, detail="No fue posible crear el perfil; puede reintentar el registro")
    await auth_post(f"/internal/users/{user_id}/activate", {}, secretos)
    return {"user_id": user_id, "correo": identidad["correo"]}


@app.post("/auth/login")
async def login(datos: Credenciales, response: Response):
    secretos = await obtener_secretos()
    resultado = await auth_post("/login", datos.model_dump(), secretos)
    response.set_cookie("session_token", resultado["access_token"], httponly=True, samesite="lax",
                        max_age=resultado["expires_in"], path="/", secure=os.getenv("COOKIE_SECURE", "false").lower() == "true")
    return {"message": "Sesión iniciada"}


@app.get("/auth/me")
async def me(sesion=Depends(sesion_actual)):
    identidad, _ = sesion
    return {"user_id": identidad["user_id"], "correo": identidad["correo"], "roles": identidad["roles"]}


@app.post("/auth/logout")
async def logout(response: Response, session_token: str | None = Cookie(default=None), sesion=Depends(sesion_actual)):
    _, secretos = sesion
    await auth_post("/logout", {"token": session_token}, secretos)
    response.delete_cookie("session_token", path="/", httponly=True, samesite="lax", secure=os.getenv("COOKIE_SECURE", "false").lower() == "true")
    return {"message": "Sesión finalizada"}


# Solo se enrutan los métodos y destinos definidos aquí.
ROUTES = [
    (r"productos", {"GET": "public", "POST": "admin"}, "inventory"),
    (r"admin/productos", {"GET": "admin"}, "inventory"),
    (r"productos/[0-9a-fA-F]{24}", {"GET": "public", "PUT": "admin", "DELETE": "admin"}, "inventory"),
    (r"clientes/me", {"GET": "user", "PUT": "user"}, "clients"),
    (r"pedidos", {"GET": "user", "POST": "user"}, "orders"),
    (r"pedidos/[0-9a-fA-F]{24}", {"GET": "user"}, "orders"),
    (r"pedidos/[0-9a-fA-F]{24}/cancelar", {"POST": "user"}, "orders"),
    (r"pedidos/[0-9a-fA-F]{24}/estado", {"PATCH": "admin"}, "orders"),
    (r"pagos", {"POST": "user"}, "payments"),
    (r"pagos/[0-9a-fA-F]{24}", {"GET": "user"}, "payments"),
    (r"graphql", {"POST": "user"}, "graphql"),
]


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request, session_token: str | None = Cookie(default=None)):
    route = next(((methods, target) for pattern, methods, target in ROUTES if re.fullmatch(pattern, path)), None)
    if not route:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")
    methods, target = route
    if request.method not in methods:
        raise HTTPException(status_code=405, detail="Método no permitido")
    politica = methods[request.method]
    identidad = None
    if politica != "public":
        identidad, secretos = await sesion_actual(session_token)
        if politica == "admin" and "admin" not in identidad["roles"]:
            raise HTTPException(status_code=403, detail="Se requiere rol de administrador")
    else:
        secretos = await obtener_secretos()
    headers = {"X-Gateway-Secret": secretos[target + "_secret"]}
    if identidad:
        headers.update({"X-Authenticated-User": identidad["user_id"],
                        "X-Authenticated-Username": identidad["correo"],
                        "X-Authenticated-Roles": ",".join(identidad["roles"])})
    for nombre in ("content-type", "idempotency-key"):
        if request.headers.get(nombre):
            headers[nombre] = request.headers[nombre]
    backend_path = "productos" if path == "admin/productos" else path
    params = dict(request.query_params)
    if path == "productos" and request.method == "GET":
        params["disponible"] = "true"
    r = await solicitar(request.method, f"{TARGETS[target]}/{backend_path}",
                         params=params, content=await request.body(), headers=headers)
    if politica == "public" and target == "inventory" and path != "productos" and r.status_code == 200:
        if not r.json().get("disponible", True):
            raise HTTPException(status_code=404, detail="Producto no encontrado")
    response_headers = {"content-type": r.headers["content-type"]} if "content-type" in r.headers else {}
    return Response(content=r.content, status_code=r.status_code, headers=response_headers)
