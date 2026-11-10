import os
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, StringConstraints
from typing import Annotated
from pymongo.errors import DuplicateKeyError


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("CLIENTS_INTERNAL_SECRET"):
        raise RuntimeError("CLIENTS_INTERNAL_SECRET no está configurado")
    client = AsyncIOMotorClient(os.getenv("CLIENTS_MONGODB_URI", "mongodb://127.0.0.1:27017/el_asador_clients"), serverSelectionTimeoutMS=5000)
    try:
        await client.admin.command("ping")
        app.state.clientes = client.get_default_database().clientes
        await app.state.clientes.create_index("user_id", unique=True)
        yield
    finally:
        client.close()


app = FastAPI(title="El Asador - Clientes", lifespan=lifespan)


def verificar_servicio(x_gateway_secret: str = Header(default="")):
    secreto = os.getenv("CLIENTS_INTERNAL_SECRET", "")
    if not secreto or not secrets.compare_digest(x_gateway_secret.encode(), secreto.encode()):
        raise HTTPException(status_code=403, detail="Servicio no autorizado")


class ClienteIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    nombre: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    correo: str = Field(min_length=3, max_length=254)
    telefono: str | None = Field(default=None, max_length=30)
    direccion: str | None = Field(default=None, max_length=300)
    run: str | None = Field(default=None, max_length=20)
    fecha_nacimiento: str | None = Field(default=None, max_length=10)
    sexo: str | None = Field(default=None, max_length=40)
    region: str | None = Field(default=None, max_length=100)
    provincia: str | None = Field(default=None, max_length=100)
    comuna: str | None = Field(default=None, max_length=100)


class PerfilCambio(BaseModel):
    nombre: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    telefono: str | None = Field(default=None, max_length=30)
    direccion: str | None = Field(default=None, max_length=300)
    run: str | None = Field(default=None, max_length=20)
    fecha_nacimiento: str | None = Field(default=None, max_length=10)
    sexo: str | None = Field(default=None, max_length=40)
    region: str | None = Field(default=None, max_length=100)
    provincia: str | None = Field(default=None, max_length=100)
    comuna: str | None = Field(default=None, max_length=100)


def salida(doc):
    return {k: doc.get(k) for k in ("user_id", "nombre", "correo", "telefono", "direccion", "run", "fecha_nacimiento", "sexo", "region", "provincia", "comuna")}


@app.get("/health")
def health():
    return {"status": "ok", "service": "clients"}


@app.post("/internal/clientes", status_code=201, dependencies=[Depends(verificar_servicio)])
async def crear_cliente(datos: ClienteIn):
    try:
        await app.state.clientes.insert_one(datos.model_dump())
    except DuplicateKeyError:
        existente = await app.state.clientes.find_one({"user_id": datos.user_id})
        if not existente or existente.get("correo") != datos.correo:
            raise HTTPException(status_code=409, detail="El perfil ya existe")
        return salida(existente)
    return datos.model_dump()


@app.get("/clientes/me", dependencies=[Depends(verificar_servicio)])
async def perfil(x_authenticated_user: str = Header(default="")):
    if not x_authenticated_user:
        raise HTTPException(status_code=401, detail="Identidad requerida")
    doc = await app.state.clientes.find_one({"user_id": x_authenticated_user})
    if not doc:
        raise HTTPException(status_code=404, detail="Perfil no encontrado")
    return salida(doc)


@app.put("/clientes/me", dependencies=[Depends(verificar_servicio)])
async def actualizar(datos: PerfilCambio, x_authenticated_user: str = Header(default="")):
    if not x_authenticated_user:
        raise HTTPException(status_code=401, detail="Identidad requerida")
    doc = await app.state.clientes.find_one_and_update({"user_id": x_authenticated_user}, {"$set": datos.model_dump()}, return_document=True)
    if not doc:
        raise HTTPException(status_code=404, detail="Perfil no encontrado")
    return salida(doc)


