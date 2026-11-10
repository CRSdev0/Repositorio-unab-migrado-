# Arquitectura del backend

El backend incluye `inventory`, `orders`, `clients`, `payments`, el API Gateway y GraphQL. El frontend todavía no está conectado.

```mermaid
flowchart LR
    C[Cliente] --> G[API Gateway]
    G --> A[Auth Service]
    G --> I[Inventory]
    G --> CL[Clients]
    G --> O[Orders]
    G --> P[Payments simulado]
    G --> Q[GraphQL Gateway]
    G --> V[Vault]
    Q --> O
    Q --> I
    O --> I
    P --> O
    A --> DA[(Identidades y sesiones)]
    I --> DI[(Productos)]
    CL --> DC[(Perfiles)]
    O --> DO[(Pedidos)]
    P --> DP[(Pagos)]
```

Cada almacén es una base MongoDB lógica. Las flechas representan las llamadas implementadas, no una garantía de aislamiento de red. Los servicios se prueban en localhost.

## Entidades de negocio

```mermaid
classDiagram
    class Cliente {
        user_id
        nombre
        correo
        direccion
    }
    class Producto {
        id
        nombre
        descripcion
        precio
        disponible
    }
    class Pedido {
        id
        fecha
        estado
        total
        direccion_despacho
    }
    class DetallePedido {
        product_id
        nombre
        cantidad
        precio_unitario
    }
    Cliente "1" --> "0..*" Pedido : realiza
    Pedido "1" *-- "1..*" DetallePedido : contiene
    DetallePedido "0..*" --> "1" Producto : referencia
```

El pedido guarda una copia del nombre y precio en el momento de la compra. La relación con producto se representa mediante un identificador, sin compartir la base entre servicios.

## Detalles de implementación

- El gateway usa tokens opacos y consulta al Auth Service; no implementa validación JWT.
- GraphQL consulta órdenes e inventario por HTTP y no lee directamente sus bases.
- Payments consulta y actualiza el pedido mediante la API de Orders.
- El registro coordina Auth Service y Clients a través del gateway; puede reintentarse ante una interrupción.
- El frontend, disponibilidad de stock en cantidades, reservas y pagos reales no forman parte de esta implementación.
