# Alcance y pendientes

- El frontend permanece sin conexión. Las pruebas actuales se realizan mediante HTTP y no sustituyen las pruebas posteriores desde el navegador.
- El adaptador de pagos es una simulación local que aprueba pagos de prueba. No existe proveedor ni cobro real, y no se procesa información bancaria.
- La disponibilidad es booleana, sin cantidades de stock ni reserva. No se calcula costo de despacho.
- Vault se usa en modo de desarrollo, con secretos en memoria. Los servicios escuchan en localhost; esto no demuestra por sí mismo aislamiento de red, TLS ni un despliegue de producción.
- El límite de solicitudes se aplica en memoria por proceso del gateway y no es compartido entre varias instancias.
- El registro coordina identidad y perfil sin transacción distribuida. Una cuenta queda inactiva hasta que se crea su perfil; si se interrumpe el proceso, se puede reintentar con el mismo correo y contraseña. Si la interrupción ocurre después de activar la cuenta, el usuario puede iniciar sesión. No existe un proceso de conciliación automático.
- No se ha integrado un proveedor de correo ni una recuperación de contraseña; el login y registro se prueban por API.
- La integración de GraphQL usa lotes para productos de un pedido, pero aún faltan pruebas específicas de carga y de todos los límites ante consultas complejas.
- No se han definido requisitos de gRPC ni de analítica.
- Las dependencias Apollo Server 3 y Motor siguen el material previo y tienen limitaciones de mantenimiento; conviene revisar su actualización según el alcance académico.
