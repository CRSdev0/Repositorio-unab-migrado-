import hashlib
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from bson import ObjectId
from fastapi import Depends, FastAPI, Header, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, StringConstraints
from typing import Annotated
from pymongo.errors import DuplicateKeyError


@asynccontextmanager
async def lifespan(app: FastAPI):
    for key in ("ORDERS_INTERNAL_SECRET", "INVENTORY_INTERNAL_SECRET", "PAYMENTS_ORDERS_SECRET"):
        if not os.getenv(key):
            raise RuntimeError(f"{key} no está configurado")
    uri = os.getenv("ORDERS_MONGODB_URI", "mongodb://127.0.0.1:27017/el_asador_orders")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    try:
        await client.admin.command("ping")
        app.state.pedidos = client.get_default_database().pedidos
        await app.state.pedidos.create_index([("user_id", 1), ("idempotency_key", 1)], unique=True)
        yield
    finally:
        client.close()


app = FastAPI(title="El Asador - Pedidos", lifespan=lifespan)


def verificar_secreto(nombre: str, recibido: str):
    esperado = os.getenv(nombre, "")
    if not esperado or not secrets.compare_digest(recibido.encode(), esperado.encode()):
        raise HTTPException(status_code=403, detail="Servicio no autorizado")


def verificar_gateway(x_gateway_secret: str = Header(default="")):
    verificar_secreto("ORDERS_INTERNAL_SECRET", x_gateway_secret)


def identidad(x_authenticated_user: str = Header(default="")):
    if not x_authenticated_user:
        raise HTTPException(status_code=401, detail="Identidad requerida")
    return x_authenticated_user


class ItemIn(BaseModel):
    product_id: str = Field(min_length=1, max_length=80)
    cantidad: int = Field(gt=0, le=100)


class PedidoIn(BaseModel):
    direccion_despacho: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=300)]
    items: list[ItemIn] = Field(min_length=1, max_length=50)


class CambioEstado(BaseModel):
    estado: str


class CambioPago(BaseModel):
    estado: str
    pago_id: str = Field(min_length=1, max_length=100)


def salida(doc):
    return {"id": str(doc["_id"]), "fecha": doc["fecha"], "estado": doc["estado"],
            "total": doc["total"], "direccion_despacho": doc["direccion_despacho"],
            "items": doc["items"]}


def id_valido(valor):
    if not ObjectId.is_valid(valor):
        raise HTTPException(status_code=400, detail="ID de pedido inválido")
    return ObjectId(valor)


@app.get("/health")
def health():
    return {"status": "ok", "service": "orders"}


@app.post("/pedidos", status_code=201, dependencies=[Depends(verificar_gateway)])
async def crear_pedido(datos: PedidoIn, user_id: str = Depends(identidad), idempotency_key: str = Header(default="", alias="Idempotency-Key")):
    if not idempotency_key or len(idempotency_key) > 100:
        raise HTTPException(status_code=400, detail="Se requiere Idempotency-Key de hasta 100 caracteres")
    huella = hashlib.sha256(json.dumps(datos.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    existente = await app.state.pedidos.find_one({"user_id": user_id, "idempotency_key": idempotency_key})
    if existente:
        if existente["huella"] != huella:
            raise HTTPException(status_code=409, detail="La clave ya se usó para otro pedido")
        return salida(existente)
    url = os.getenv("INVENTORY_URL", "http://127.0.0.1:8098").rstrip("/")
    items = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            for item in datos.items:
                respuesta = await client.get(f"{url}/productos/{item.product_id}", headers={"X-Gateway-Secret": os.environ["INVENTORY_INTERNAL_SECRET"]})
                if respuesta.status_code in (400, 404):
                    raise HTTPException(status_code=400, detail="Producto no encontrado")
                respuesta.raise_for_status()
                producto = respuesta.json()
                if not producto.get("disponible", True):
                    raise HTTPException(status_code=409, detail="Producto no disponible")
                items.append({"product_id": producto["id"], "nombre": producto["nombre"], "cantidad": item.cantidad, "precio_unitario": producto["precio"]})
    except (httpx.HTTPError, KeyError, ValueError):
        raise HTTPException(status_code=503, detail="Inventario no disponible")
    pedido = {"user_id": user_id, "idempotency_key": idempotency_key, "huella": huella,
              "fecha": datetime.now(timezone.utc), "estado": "PENDIENTE_PAGO",
              "direccion_despacho": datos.direccion_despacho.strip(), "items": items,
              "total": sum(i["cantidad"] * i["precio_unitario"] for i in items)}
    try:
        resultado = await app.state.pedidos.insert_one(pedido)
        pedido["_id"] = resultado.inserted_id
    except DuplicateKeyError:
        pedido = await app.state.pedidos.find_one({"user_id": user_id, "idempotency_key": idempotency_key})
        if not pedido or pedido["huella"] != huella:
            raise HTTPException(status_code=409, detail="La clave ya se usó para otro pedido")
    return salida(pedido)


@app.get("/pedidos", dependencies=[Depends(verificar_gateway)])
async def listar_pedidos(user_id: str = Depends(identidad), x_authenticated_roles: str = Header(default="")):
    roles = x_authenticated_roles.split(",")
    filtro = {} if "admin" in roles else {"user_id": user_id}
    return [salida(doc) async for doc in app.state.pedidos.find(filtro).sort("fecha", -1)]


@app.get("/pedidos/{pedido_id}", dependencies=[Depends(verificar_gateway)])
async def obtener_pedido(pedido_id: str, user_id: str = Depends(identidad), x_authenticated_roles: str = Header(default="")):
    filtro = {"_id": id_valido(pedido_id)}
    if "admin" not in x_authenticated_roles.split(","):
        filtro["user_id"] = user_id
    doc = await app.state.pedidos.find_one(filtro)
    if not doc:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    return salida(doc)


@app.post("/pedidos/{pedido_id}/cancelar", dependencies=[Depends(verificar_gateway)])
async def cancelar_pedido(pedido_id: str, user_id: str = Depends(identidad)):
    identificador = id_valido(pedido_id)
    doc = await app.state.pedidos.find_one_and_update(
        {"_id": identificador, "user_id": user_id, "estado": "PENDIENTE_PAGO"},
        {"$set": {"estado": "CANCELADO"}}, return_document=True)
    if doc:
        return salida(doc)
    doc = await app.state.pedidos.find_one({"_id": identificador, "user_id": user_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    if doc["estado"] == "CANCELADO":
        return salida(doc)
    raise HTTPException(status_code=409, detail="El pedido no puede cancelarse en su estado actual")


@app.patch("/pedidos/{pedido_id}/estado", dependencies=[Depends(verificar_gateway)])
async def cambiar_estado(pedido_id: str, datos: CambioEstado, user_id: str = Depends(identidad), x_authenticated_roles: str = Header(default="")):
    if "admin" not in x_authenticated_roles.split(","):
        raise HTTPException(status_code=403, detail="Se requiere rol de administrador")
    anteriores = {"EN_PREPARACION": "PAGADO", "EN_CAMINO": "EN_PREPARACION", "ENTREGADO": "EN_CAMINO"}
    if datos.estado not in anteriores:
        raise HTTPException(status_code=400, detail="Estado no permitido")
    identificador = id_valido(pedido_id)
    doc = await app.state.pedidos.find_one_and_update(
        {"_id": identificador, "estado": anteriores[datos.estado]},
        {"$set": {"estado": datos.estado}}, return_document=True)
    if doc:
        return salida(doc)
    if not await app.state.pedidos.find_one({"_id": identificador}):
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    raise HTTPException(status_code=409, detail="Transición de estado no permitida")


@app.post("/internal/pedidos/{pedido_id}/pago")
async def aplicar_pago(pedido_id: str, datos: CambioPago, x_service_secret: str = Header(default="")):
    verificar_secreto("PAYMENTS_ORDERS_SECRET", x_service_secret)
    if datos.estado not in ("PAGADO", "PAGO_RECHAZADO"):
        raise HTTPException(status_code=400, detail="Estado de pago inválido")
    doc = await app.state.pedidos.find_one_and_update(
        {"_id": id_valido(pedido_id), "estado": "PENDIENTE_PAGO"},
        {"$set": {"estado": datos.estado, "pago_id": datos.pago_id}}, return_document=True)
    if doc:
        return salida(doc)
    doc = await app.state.pedidos.find_one({"_id": id_valido(pedido_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    if doc.get("pago_id") == datos.pago_id and doc["estado"] == datos.estado:
        return salida(doc)
    raise HTTPException(status_code=409, detail="El pedido no admite este cambio de pago")
