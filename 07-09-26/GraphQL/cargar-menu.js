const mongoose = require('mongoose');
const Producto = require('./models/producto');
const menu = require('./menu');

async function cargarMenu() {
  const uri = process.env.MONGODB_URI || 'mongodb://127.0.0.1:27017/el_asador';
  await mongoose.connect(uri, { serverSelectionTimeoutMS: 5000 });
  // No sobrescribir productos existentes: solo insertar los números que falten.
  for (const producto of menu) {
    await Producto.updateOne({ numero: producto.numero }, { $setOnInsert: producto }, { upsert: true });
  }
  console.log('Productos iniciales cargados.');
}

cargarMenu()
  .catch(error => { console.error('No se pudo cargar el menú:', error.message); process.exitCode = 1; })
  .finally(() => mongoose.disconnect());
