import hashlib
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, FastAPI, Header, HTTPException
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

hasher = PasswordHasher()
DURACION_MINUTOS = 15


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("AUTH_INTROSPECTION_SECRET"):
        raise RuntimeError("AUTH_INTROSPECTION_SECRET no está configurado")
    uri = os.getenv("AUTH_MONGODB_URI", "mongodb://127.0.0.1:27017/el_asador_identity")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    try:
        await client.admin.command("ping")
        db = client.get_default_database()
        app.state.usuarios = db.usuarios
        app.state.sesiones = db.sesiones
        await db.usuarios.create_index("correo", unique=True)
        await db.sesiones.create_index("token_hash", unique=True)
        await db.sesiones.create_index("expires_at", expireAfterSeconds=0)
        correo = os.getenv("ADMIN_CORREO", "").strip().lower()
        password = os.getenv("ADMIN_PASSWORD", "")
        if bool(correo) != bool(password):
            raise RuntimeError("ADMIN_CORREO y ADMIN_PASSWORD deben configurarse juntos")
        if correo and password:
            await db.usuarios.update_one({"correo": correo}, {"$setOnInsert": {
                "correo": correo, "password_hash": hasher.hash(password), "roles": ["user", "admin"], "active": True
            }}, upsert=True)
        yield
    finally:
        client.close()


app = FastAPI(title="El Asador - Auth Service", lifespan=lifespan)


class Credenciales(BaseModel):
    correo: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class Registro(Credenciales):
    password: str = Field(min_length=8, max_length=256)


class TokenSesion(BaseModel):
    token: str


def verificar_gateway(x_gateway_auth_secret: str = Header(default="")):
    secreto = os.getenv("AUTH_INTROSPECTION_SECRET", "")
    if not secreto or not secrets.compare_digest(x_gateway_auth_secret.encode(), secreto.encode()):
        raise HTTPException(status_code=403, detail="Servicio no autorizado")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "Auth Service"}


@app.post("/register", status_code=201, dependencies=[Depends(verificar_gateway)])
async def register(datos: Registro):
    correo = datos.correo.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", correo):
        raise HTTPException(status_code=422, detail="Correo inválido")
    try:
        resultado = await app.state.usuarios.insert_one({
            "correo": correo, "password_hash": hasher.hash(datos.password), "roles": ["user"], "active": False
        })
    except DuplicateKeyError:
        existente = await app.state.usuarios.find_one({"correo": correo})
        try:
            if existente and not existente.get("active", False) and hasher.verify(existente["password_hash"], datos.password):
                return {"user_id": str(existente["_id"]), "correo": correo}
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            pass
        raise HTTPException(status_code=409, detail="El correo ya está registrado")
    return {"user_id": str(resultado.inserted_id), "correo": correo}


@app.post("/login", dependencies=[Depends(verificar_gateway)])
async def login(datos: Credenciales):
    usuario = await app.state.usuarios.find_one({"correo": datos.correo.strip().lower()})
    try:
        if usuario is None or not usuario.get("active", False) or not hasher.verify(usuario["password_hash"], datos.password):
            raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    token = secrets.token_urlsafe(32)
    await app.state.sesiones.insert_one({
        "token_hash": token_hash(token), "user_id": str(usuario["_id"]),
        "correo": usuario["correo"], "roles": usuario["roles"],
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=DURACION_MINUTOS)
    })
    return {"access_token": token, "token_type": "bearer", "expires_in": DURACION_MINUTOS * 60}


@app.post("/introspect", dependencies=[Depends(verificar_gateway)])
async def introspect(datos: TokenSesion):
    sesion = await app.state.sesiones.find_one({"token_hash": token_hash(datos.token)})
    if not sesion or sesion["expires_at"] <= datetime.now(timezone.utc):
        return {"active": False}
    return {"active": True, "user_id": sesion["user_id"], "correo": sesion["correo"], "roles": sesion["roles"]}


@app.post("/logout", dependencies=[Depends(verificar_gateway)])
async def logout(datos: TokenSesion):
    await app.state.sesiones.delete_one({"token_hash": token_hash(datos.token)})
    return {"message": "Sesión finalizada"}


@app.post("/internal/users/{user_id}/activate", dependencies=[Depends(verificar_gateway)])
async def activar_usuario(user_id: str):
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Identidad inválida")
    resultado = await app.state.usuarios.update_one({"_id": ObjectId(user_id)}, {"$set": {"active": True}})
    if not resultado.matched_count:
        raise HTTPException(status_code=404, detail="Identidad no encontrada")
    return {"active": True}


