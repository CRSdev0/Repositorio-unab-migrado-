const express = require('express');
const mongoose = require('mongoose');
const cors = require('cors');
const { ApolloServer, gql } = require('apollo-server-express');
const Producto = require('./models/producto');

// Definimos qué datos y operaciones estarán disponibles en GraphQL.
const typeDefs = gql`
  type Producto {
    id: ID!
    numero: Int!
    nombre: String!
    descripcion: String!
    precio: Int!
    imagen: String!
  }

  type Query {
    productos: [Producto!]!
    producto(numero: Int!): Producto
  }

  input ProductoInput {
    numero: Int!
    nombre: String!
    descripcion: String!
    precio: Int!
    imagen: String!
  }

  type Mutation {
    agregarProducto(input: ProductoInput!): Producto!
    modificarProducto(numero: Int!, input: ProductoInput!): Producto
    eliminarProducto(numero: Int!): Boolean!
  }
`;

// Cada operación consulta o modifica los productos guardados en MongoDB.
const resolvers = {
  Query: {
    productos: () => Producto.find().sort({ numero: 1 }).exec(),
    producto: (_, { numero }) => Producto.findOne({ numero }).exec()
  },
  Mutation: {
    agregarProducto: (_, { input }) => new Producto(input).save(),
    modificarProducto: (_, { numero, input }) => Producto.findOneAndUpdate(
      { numero }, input, { new: true, runValidators: true }
    ).exec(),
    eliminarProducto: async (_, { numero }) => {
      const resultado = await Producto.deleteOne({ numero }).exec();
      return resultado.deletedCount === 1;
    }
  }
};

async function iniciar() {
  // Usamos la base local salvo que se indique otra conexión en el entorno.
  const uri = process.env.MONGODB_URI || 'mongodb://127.0.0.1:27017/el_asador';
  await mongoose.connect(uri, { serverSelectionTimeoutMS: 5000 });
  const app = express();
  app.use(cors());
  const servidor = new ApolloServer({ typeDefs, resolvers });
  await servidor.start();
  servidor.applyMiddleware({ app, cors: false });
  const puerto = Number(process.env.PORT || 8090);
  app.listen(puerto, () => console.log(`GraphQL disponible en http://localhost:${puerto}${servidor.graphqlPath}`));
}

iniciar().catch(error => {
  console.error('No se pudo iniciar el servidor:', error.message);
  process.exitCode = 1;
});
