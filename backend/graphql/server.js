const express = require('express');
const { ApolloServer, gql } = require('apollo-server-express');
const { GraphQLError, Kind } = require('graphql');
const crypto = require('crypto');

const secret = process.env.GRAPHQL_INTERNAL_SECRET;
const ordersSecret = process.env.ORDERS_INTERNAL_SECRET;
const inventorySecret = process.env.INVENTORY_INTERNAL_SECRET;
if (!secret || !ordersSecret || !inventorySecret) throw new Error('Faltan secretos internos de GraphQL');
const ordersUrl = (process.env.ORDERS_URL || 'http://127.0.0.1:8111').replace(/\/$/, '');
const inventoryUrl = (process.env.INVENTORY_URL || 'http://127.0.0.1:8098').replace(/\/$/, '');

const typeDefs = gql`
  type Producto { id: ID!, nombre: String!, descripcion: String!, precio: Int!, imagen: String!, disponible: Boolean! }
  type DetallePedido { product_id: ID!, nombre: String!, cantidad: Int!, precio_unitario: Int!, producto: Producto }
  type Pedido { id: ID!, fecha: String!, estado: String!, total: Int!, direccion_despacho: String!, items: [DetallePedido!]! }
  type Query { resumenPedido(id: ID!): Pedido }
`;

// Limitar consultas evita que una petición arbitraria recorra el esquema sin límite.
function limitarConsulta(context) {
  return { Document(node) {
    const fragments = Object.fromEntries(node.definitions.filter(d => d.kind === Kind.FRAGMENT_DEFINITION).map(d => [d.name.value, d]));
    function revisar(set, depth, stack, stats) {
      if (!set) return;
      if (depth > 6) { context.reportError(new GraphQLError('Consulta demasiado profunda')); return; }
      for (const selection of set.selections) {
        if (++stats.fields > 100) { context.reportError(new GraphQLError('Consulta demasiado extensa')); return; }
        if (selection.kind === Kind.FRAGMENT_SPREAD) {
          const name = selection.name.value;
          if (fragments[name] && !stack.includes(name)) revisar(fragments[name].selectionSet, depth, [...stack, name], stats);
        } else revisar(selection.selectionSet, selection.kind === Kind.FIELD ? depth + 1 : depth, stack, stats);
      }
    }
    for (const op of node.definitions.filter(d => d.kind === Kind.OPERATION_DEFINITION)) revisar(op.selectionSet, 1, [], { fields: 0 });
  }};
}

async function requestJson(url, options) {
  let respuesta;
  try { respuesta = await fetch(url, { ...options, signal: AbortSignal.timeout(5000) }); }
  catch { throw new GraphQLError('Servicio interno no disponible'); }
  if (respuesta.status === 404) return null;
  if (!respuesta.ok) throw new GraphQLError('No fue posible consultar el servicio interno');
  return respuesta.json();
}

const resolvers = { Query: { async resumenPedido(_, { id }, context) {
  const headers = { 'X-Gateway-Secret': ordersSecret, 'X-Authenticated-User': context.userId, 'X-Authenticated-Roles': context.roles };
  const pedido = await requestJson(`${ordersUrl}/pedidos/${encodeURIComponent(id)}`, { headers });
  if (!pedido) return null;
  const ids = [...new Set(pedido.items.map(item => item.product_id))];
  const productos = await requestJson(`${inventoryUrl}/internal/productos`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Gateway-Secret': inventorySecret }, body: JSON.stringify({ ids })
  });
  if (!productos) throw new GraphQLError('Inventario no disponible');
  const mapa = new Map(productos.map(producto => [producto.id, producto]));
  return { ...pedido, items: pedido.items.map(item => ({ ...item, producto: mapa.get(item.product_id) || null })) };
} } };

function iguales(a, b) {
  const izquierda = Buffer.from(a || ''); const derecha = Buffer.from(b || '');
  return izquierda.length === derecha.length && crypto.timingSafeEqual(izquierda, derecha);
}

async function iniciar() {
  const app = express();
  app.use(express.json({ limit: '64kb' }));
  app.use('/graphql', (req, res, next) => {
    if (!iguales(req.header('X-Gateway-Secret'), secret)) return res.status(403).json({ error: 'Servicio no autorizado' });
    if (!req.header('X-Authenticated-User')) return res.status(401).json({ error: 'Identidad requerida' });
    next();
  });
  const servidor = new ApolloServer({ typeDefs, resolvers, validationRules: [limitarConsulta], introspection: false,
    context: ({ req }) => ({ userId: req.header('X-Authenticated-User'), roles: req.header('X-Authenticated-Roles') || '' }) });
  await servidor.start(); servidor.applyMiddleware({ app, cors: false });
  const puerto = Number(process.env.PORT || 8090);
  app.listen(puerto, '127.0.0.1', () => console.log(`GraphQL disponible en puerto ${puerto}`));
}
iniciar().catch(error => { console.error('No se pudo iniciar GraphQL:', error.message); process.exitCode = 1; });
