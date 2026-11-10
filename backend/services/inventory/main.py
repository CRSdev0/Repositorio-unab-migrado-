import os
import secrets
from contextlib import asynccontextmanager

from bson import ObjectId
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, StringConstraints
from pymongo.errors import DuplicateKeyError
from typing import Annotated


@asynccontextmanager
async def lifespan(app: FastAPI):
    # El secreto permite comprobar que las solicitudes llegan desde el gateway.
    if not os.getenv("INTERNAL_GATEWAY_SECRET"):
        raise RuntimeError("INTERNAL_GATEWAY_SECRET no está configurado")
    # El inventario administra su propia colección de productos.
    uri = os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017/el_asador_inventory")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    try:
        await client.admin.command("ping")
        app.state.productos = client.get_default_database()["productos"]
        await app.state.productos.create_index("numero", unique=True)
        yield
    finally:
        client.close()


app = FastAPI(title="El Asador - Productos", lifespan=lifespan)

Texto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Producto(BaseModel):
    numero: int = Field(gt=0, description="Número del plato en el menú")
    nombre: Texto
    descripcion: Texto
    precio: int = Field(ge=0, description="Precio en pesos chilenos")
    imagen: Texto
    disponible: bool = True


class ProductoSalida(Producto):
    id: str


def producto_salida(documento: dict) -> ProductoSalida:
    return ProductoSalida(id=str(documento["_id"]), **documento)


def validar_id(producto_id: str) -> ObjectId:
    if not ObjectId.is_valid(producto_id):
        raise HTTPException(status_code=400, detail="ID de producto inválido")
    return ObjectId(producto_id)


# Solo aceptamos solicitudes que incluyan el secreto del gateway.
def verificar_gateway(x_gateway_secret: str = Header(default="")):
    secreto = os.getenv("INTERNAL_GATEWAY_SECRET", "")
    if not secrets.compare_digest(x_gateway_secret, secreto):
        raise HTTPException(status_code=403, detail="Solicitud no autorizada desde gateway")


# El backend también comprueba el rol antes de modificar productos.
def exigir_admin(x_authenticated_roles: str = Header(default="")):
    roles = [rol.strip() for rol in x_authenticated_roles.split(",")]
    if "admin" not in roles:
        raise HTTPException(status_code=403, detail="Se requiere rol de administrador")


@app.get("/health", tags=["sistema"])
def health():
    return {"status": "ok"}


@app.get("/productos", response_model=list[ProductoSalida], tags=["productos"], dependencies=[Depends(verificar_gateway)])
async def listar_productos(disponible: bool | None = None):
    filtro = {}
    if disponible is True:
        filtro = {"$or": [{"disponible": True}, {"disponible": {"$exists": False}}]}
    elif disponible is False:
        filtro = {"disponible": False}
    cursor = app.state.productos.find(filtro).sort("numero", 1)
    return [producto_salida(documento) async for documento in cursor]


class ProductosLote(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=50)


@app.post("/internal/productos", response_model=list[ProductoSalida], dependencies=[Depends(verificar_gateway)])
async def productos_lote(datos: ProductosLote):
    ids = [validar_id(producto_id) for producto_id in datos.ids]
    cursor = app.state.productos.find({"_id": {"$in": ids}})
    return [producto_salida(documento) async for documento in cursor]


@app.get("/productos/{producto_id}", response_model=ProductoSalida, tags=["productos"], dependencies=[Depends(verificar_gateway)])
async def obtener_producto(producto_id: str):
    documento = await app.state.productos.find_one({"_id": validar_id(producto_id)})
    if documento is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return producto_salida(documento)


@app.post("/productos", response_model=ProductoSalida, status_code=201, tags=["productos"], dependencies=[Depends(verificar_gateway), Depends(exigir_admin)])
async def crear_producto(producto: Producto):
    datos = producto.model_dump()
    try:
        resultado = await app.state.productos.insert_one(datos)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="El número de producto ya existe")
    documento = await app.state.productos.find_one({"_id": resultado.inserted_id})
    return producto_salida(documento)


@app.put("/productos/{producto_id}", response_model=ProductoSalida, tags=["productos"], dependencies=[Depends(verificar_gateway), Depends(exigir_admin)])
async def actualizar_producto(producto_id: str, producto: Producto):
    identificador = validar_id(producto_id)
    try:
        resultado = await app.state.productos.update_one(
            {"_id": identificador}, {"$set": producto.model_dump()}
        )
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="El número de producto ya existe")
    if resultado.matched_count == 0:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    documento = await app.state.productos.find_one({"_id": identificador})
    return producto_salida(documento)


@app.delete("/productos/{producto_id}", status_code=204, tags=["productos"], dependencies=[Depends(verificar_gateway), Depends(exigir_admin)])
async def eliminar_producto(producto_id: str):
    resultado = await app.state.productos.delete_one({"_id": validar_id(producto_id)})
    if resultado.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
