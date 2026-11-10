import os
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="El Asador - Auth Service")
hasher = PasswordHasher()

# Las cuentas de prueba se configuran al iniciar el servicio.
secreto_gateway = os.getenv("AUTH_INTROSPECTION_SECRET")
clave_cliente = os.getenv("CLIENTE_PASSWORD")
clave_admin = os.getenv("ADMIN_PASSWORD")
if not secreto_gateway or not clave_cliente or not clave_admin:
    raise RuntimeError("Configure AUTH_INTROSPECTION_SECRET, CLIENTE_PASSWORD y ADMIN_PASSWORD")

usuarios = {
    "cliente@elasador.local": {"id": "USR-001", "hash": hasher.hash(clave_cliente), "roles": ["user"]},
    "admin@elasador.local": {"id": "USR-002", "hash": hasher.hash(clave_admin), "roles": ["user", "admin"]},
}
sesiones = {}
DURACION_MINUTOS = 15


class Credenciales(BaseModel):
    correo: str
    password: str


class TokenSesion(BaseModel):
    token: str


def verificar_gateway(x_gateway_auth_secret: str = Header(default="")):
    if not secrets.compare_digest(x_gateway_auth_secret, secreto_gateway):
        raise HTTPException(status_code=403, detail="Gateway no autorizado")


@app.get("/health")
def health():
    return {"status": "ok", "service": "Auth Service"}


@app.post("/login", dependencies=[Depends(verificar_gateway)])
def login(datos: Credenciales):
    correo = datos.correo.strip().lower()
    usuario = usuarios.get(correo)
    try:
        if usuario is None or not hasher.verify(usuario["hash"], datos.password):
            raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    except VerifyMismatchError:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    # El token representa una sesión temporal guardada en memoria.
    token = secrets.token_urlsafe(32)
    sesiones[token] = {
        "user_id": usuario["id"],
        "correo": correo,
        "roles": usuario["roles"],
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=DURACION_MINUTOS),
    }
    return {"access_token": token, "token_type": "bearer", "expires_in": DURACION_MINUTOS * 60}


@app.post("/introspect", dependencies=[Depends(verificar_gateway)])
def introspect(datos: TokenSesion):
    sesion = sesiones.get(datos.token)
    if sesion is None:
        return {"active": False}
    if datetime.now(timezone.utc) >= sesion["expires_at"]:
        sesiones.pop(datos.token, None)
        return {"active": False}
    return {"active": True, "user_id": sesion["user_id"], "correo": sesion["correo"], "roles": sesion["roles"]}


@app.post("/logout", dependencies=[Depends(verificar_gateway)])
def logout(datos: TokenSesion):
    sesiones.pop(datos.token, None)
    return {"message": "Sesión finalizada"}
