<!DOCTYPE html>
<html lang="es">
    <head>
        <title>Semana 03</title>
    </head>
    <body onload="cargarProductos();">
        <h4>Agregar productos</h4>
        <form>
            <label for="idInput">ID: </label>
            <input id="idInput" type="number" placeholder="ID del producto"><br>
            <label for="nameInput">Nombre:</label>
            <input id="nameInput" type="text" placeholder="Nombre del producto"><br>
            <input type="reset" value="Limpiar">
            <button type="button" onclick="agregarProducto();">Agregar</button>
        </form>
        <div>
            <select id="productDropdown" name="productDropdown"></select>
        </div>
    </body>
    <script>
        function agregarProducto() {
            let dropdown = document.getElementById("productDropdown");
            let id = document.getElementById("idInput");
            let name = document.getElementById("nameInput");
            let dropdownOption = document.createElement("option");
            dropdownOption.setAttribute("value", id);
            dropdownOption.innerText = id.value + " | " + name.value;
            dropdown.appendChild(dropdownOption);
        }
    </script>
    <script>
        const apiResponse = {
            "status": 200,
            "message": "Productos obtenidos",
            "data": [{
                "id": "1",
                "name": "Clavos"
            }, {
                "id": "2",
                "name": "Papel"
            }]
        };

        function cargarProductos() {
            let dropdown = document.getElementById("productDropdown");
            apiResponse.data.forEach((producto) => {
                let dropdownOption = document.createElement("option");
                dropdownOption.setAttribute("value", producto.id);
                dropdownOption.innerText = producto.id + " | " + producto.name;
                dropdown.appendChild(dropdownOption);
            })
        }
    </script>
</html>