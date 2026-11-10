const mongoose = require('mongoose');

// El número identifica cada plato del menú; el precio está en pesos chilenos.
const productoSchema = new mongoose.Schema({
  numero: { type: Number, required: true, unique: true, min: 1 },
  nombre: { type: String, required: true, trim: true },
  descripcion: { type: String, required: true, trim: true },
  precio: { type: Number, required: true, min: 0 },
  imagen: { type: String, required: true, trim: true }
});

module.exports = mongoose.model('Producto', productoSchema);
