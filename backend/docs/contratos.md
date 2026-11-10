# Contratos principales

Todas las rutas públicas se acceden a través de `http://127.0.0.1:8000`. No hay integración con el frontend. Las rutas autenticadas usan la cookie HttpOnly entregada al iniciar sesión.

| Método | Ruta | Acceso | Función |
| --- | --- | --- | --- |
| POST | `/auth/register` | Público | Registrar identidad y perfil de cliente |
| POST | `/auth/login` | Público | Iniciar sesión con correo y contraseña |
| GET | `/auth/me` | Sesión | Consultar identidad y roles |
| POST | `/auth/logout` | Sesión | Revocar la sesión |
| GET | `/api/productos` | Público | Consultar platos disponibles |
| GET | `/api/productos/{id}` | Público | Consultar un plato disponible |
| GET | `/api/admin/productos` | Administrador | Consultar todos los platos |
| POST, PUT, DELETE | `/api/productos` o `/api/productos/{id}` | Administrador | Administrar productos |
| GET, PUT | `/api/clientes/me` | Sesión | Consultar o modificar el perfil propio |
| GET, POST | `/api/pedidos` | Sesión | Consultar pedidos o crear uno |
| GET | `/api/pedidos/{id}` | Propietario o administrador | Consultar pedido |
| POST | `/api/pedidos/{id}/cancelar` | Propietario | Cancelar pedido pendiente de pago |
| PATCH | `/api/pedidos/{id}/estado` | Administrador | Avanzar estado de despacho |
| POST | `/api/pagos` | Sesión | Procesar pago de prueba de un pedido propio |
| GET | `/api/pagos/{id}` | Propietario | Consultar pago de prueba |
| POST | `/api/graphql` | Sesión | Consultar resumen de pedido |

Para crear un pedido, enviar la cabecera `Idempotency-Key` con una clave única de la operación y un JSON como:

```json
{
  "direccion_despacho": "Dirección de prueba 123",
  "items": [{ "product_id": "ID_DE_MONGODB", "cantidad": 2 }]
}
```

El servidor obtiene nombre y precio de inventory y calcula el total. Repetir la misma clave y contenido devuelve el mismo pedido; usar la clave con otro contenido devuelve 409. El pedido comienza como `PENDIENTE_PAGO`.

Para probar el pago, enviar a `/api/pagos` el cuerpo `{ "pedido_id": "ID_DEL_PEDIDO" }`. La respuesta indica `modo: PRUEBA`: no se realiza ningún cobro. Una vez aprobado el pago de prueba, el administrador puede avanzar el pedido mediante `PATCH /api/pedidos/{id}/estado` con los estados `EN_PREPARACION`, `EN_CAMINO` y `ENTREGADO`, en ese orden.

Consulta GraphQL de ejemplo:

```graphql
query Resumen($id: ID!) {
  resumenPedido(id: $id) {
    id estado total
    items { cantidad precio_unitario producto { id nombre } }
  }
}
```

El gateway GraphQL consulta orders e inventory; si el pedido no pertenece al cliente devuelve `null`. El administrador puede consultar pedidos de otros clientes. Los servicios internos requieren su secreto de comunicación; los valores nunca se incluyen en estos contratos.
