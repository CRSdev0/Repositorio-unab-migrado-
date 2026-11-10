import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from bson import ObjectId
from fastapi import Depends, FastAPI, Header, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError


@asynccontextmanager
async def lifespan(app: FastAPI):
    for key in ("PAYMENTS_INTERNAL_SECRET", "ORDERS_INTERNAL_SECRET", "PAYMENTS_ORDERS_SECRET"):
        if not os.getenv(key):
            raise RuntimeError(f"{key} no está configurado")
    uri = os.getenv("PAYMENTS_MONGODB_URI", "mongodb://127.0.0.1:27017/el_asador_payments")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    try:
        await client.admin.command("ping")
        app.state.pagos = client.get_default_database().pagos
        await app.state.pagos.create_index("pedido_id", unique=True)
        yield
    finally:
        client.close()


app = FastAPI(title="El Asador - Pagos de prueba", lifespan=lifespan)


def verificar_gateway(x_gateway_secret: str = Header(default="")):
    esperado = os.getenv("PAYMENTS_INTERNAL_SECRET", "")
    if not esperado or not secrets.compare_digest(x_gateway_secret.encode(), esperado.encode()):
        raise HTTPException(status_code=403, detail="Servicio no autorizado")


def identidad(x_authenticated_user: str = Header(default="")):
    if not x_authenticated_user:
        raise HTTPException(status_code=401, detail="Identidad requerida")
    return x_authenticated_user


class PagoIn(BaseModel):
    pedido_id: str = Field(min_length=1, max_length=80)


def salida(doc):
    return {"id": str(doc["_id"]), "pedido_id": doc["pedido_id"], "monto": doc["monto"],
            "estado": doc["estado"], "modo": "PRUEBA", "fecha": doc["fecha"]}


@app.get("/health")
def health():
    return {"status": "ok", "service": "payments"}


@app.post("/pagos", status_code=201, dependencies=[Depends(verificar_gateway)])
async def pagar(datos: PagoIn, user_id: str = Depends(identidad)):
    url = os.getenv("ORDERS_URL", "http://127.0.0.1:8111").rstrip("/")
    headers = {"X-Gateway-Secret": os.environ["ORDERS_INTERNAL_SECRET"], "X-Authenticated-User": user_id}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            pedido_resp = await client.get(f"{url}/pedidos/{datos.pedido_id}", headers=headers)
            if pedido_resp.status_code == 404:
                raise HTTPException(status_code=404, detail="Pedido no encontrado")
            if pedido_resp.status_code == 400:
                raise HTTPException(status_code=400, detail="ID de pedido inválido")
            pedido_resp.raise_for_status()
            pedido = pedido_resp.json()
            pago = await app.state.pagos.find_one({"pedido_id": datos.pedido_id, "user_id": user_id})
            if pago and pago["estado"] == "APROBADO":
                return salida(pago)
            if not pago:
                if pedido["estado"] != "PENDIENTE_PAGO":
                    raise HTTPException(status_code=409, detail="El pedido no admite pagos")
                pago = {"_id": ObjectId(), "pedido_id": datos.pedido_id, "user_id": user_id,
                        "monto": pedido["total"], "estado": "PENDIENTE", "fecha": datetime.now(timezone.utc)}
                try:
                    await app.state.pagos.insert_one(pago)
                except DuplicateKeyError:
                    pago = await app.state.pagos.find_one({"pedido_id": datos.pedido_id, "user_id": user_id})
                    if not pago:
                        raise HTTPException(status_code=409, detail="Pago no disponible")
            # El resultado es simulado: no se realiza ningún cobro externo.
            respuesta = await client.post(f"{url}/internal/pedidos/{datos.pedido_id}/pago",
                headers={"X-Service-Secret": os.environ["PAYMENTS_ORDERS_SECRET"]},
                json={"estado": "PAGADO", "pago_id": str(pago["_id"])})
            if respuesta.status_code == 409:
                raise HTTPException(status_code=409, detail="El pedido no admite pagos")
            respuesta.raise_for_status()
            await app.state.pagos.update_one({"_id": pago["_id"]}, {"$set": {"estado": "APROBADO"}})
            pago["estado"] = "APROBADO"
            return salida(pago)
    except (httpx.HTTPError, KeyError, ValueError):
        raise HTTPException(status_code=503, detail="Servicio de pedidos no disponible")


@app.get("/pagos/{pago_id}", dependencies=[Depends(verificar_gateway)])
async def consultar_pago(pago_id: str, user_id: str = Depends(identidad)):
    if not ObjectId.is_valid(pago_id):
        raise HTTPException(status_code=400, detail="ID de pago inválido")
    pago = await app.state.pagos.find_one({"_id": ObjectId(pago_id), "user_id": user_id})
    if not pago:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    return salida(pago)
