"""Comprueba el flujo local con Vault y bases de datos temporales."""
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
VAULT = os.getenv("VAULT_BIN", "vault")
SUFFIX = uuid.uuid4().hex[:12]
DBS = {k: f"el_asador_test_{SUFFIX}_{k}" for k in ("inventory", "identity", "clients", "orders", "payments")}
processes = []


def port():
    for _ in range(100):
        candidate = 20000 + secrets.randbelow(20000)
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    raise RuntimeError("No se encontró puerto de prueba")


ports = {}
for name in ("vault", "auth", "inventory", "clients", "orders", "payments", "graphql", "gateway"):
    value = port()
    while value in ports.values():
        value = port()
    ports[name] = value
urls = {k: f"http://127.0.0.1:{v}" for k, v in ports.items()}
keys = {k: secrets.token_hex(24) for k in ("root", "auth", "inventory", "clients", "orders", "payments", "graphql", "payments_orders")}


def spawn(args, cwd, env):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags)
    processes.append(p)
    return p


def wait(url, process, expected=(200,)):
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError(f"El servicio terminó antes de iniciar: {url}\n{process.stderr.read().decode(errors='replace')}")
        try:
            if httpx.get(url, timeout=0.5).status_code in expected:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.15)
    raise RuntimeError(f"El servicio no inició: {url}")


def check(response, expected, label):
    if response.status_code != expected:
        raise AssertionError(f"{label}: esperado {expected}, recibido {response.status_code}: {response.text[:300]}")
    print(label + ": OK", flush=True)
    return response


def main():
    env = os.environ.copy()
    env.update(VAULT_DEV_ROOT_TOKEN_ID=keys["root"], VAULT_DEV_LISTEN_ADDRESS=f"127.0.0.1:{ports['vault']}")
    vault = spawn([VAULT, "server", "-dev", "-dev-no-store-token"], ROOT, env)
    wait(urls["vault"] + "/v1/sys/health", vault)
    data = {"auth_introspection_secret": keys["auth"], **{k + "_secret": keys[k] for k in ("inventory", "clients", "orders", "payments", "graphql")}}
    check(httpx.post(urls["vault"] + "/v1/secret/data/gateway", headers={"X-Vault-Token": keys["root"]}, json={"data": data}), 200, "Configurar Vault")
    env = os.environ.copy(); env["MONGODB_URI"] = f"mongodb://127.0.0.1:27017/{DBS['inventory']}"
    subprocess.run(["node", "cargar-menu.js"], cwd=ROOT/"graphql", env=env, stdout=subprocess.DEVNULL, check=True)
    env.update(AUTH_INTROSPECTION_SECRET=keys["auth"], AUTH_MONGODB_URI=f"mongodb://127.0.0.1:27017/{DBS['identity']}",
        CLIENTS_MONGODB_URI=f"mongodb://127.0.0.1:27017/{DBS['clients']}", ORDERS_MONGODB_URI=f"mongodb://127.0.0.1:27017/{DBS['orders']}",
        PAYMENTS_MONGODB_URI=f"mongodb://127.0.0.1:27017/{DBS['payments']}", INTERNAL_GATEWAY_SECRET=keys["inventory"],
        INVENTORY_INTERNAL_SECRET=keys["inventory"], CLIENTS_INTERNAL_SECRET=keys["clients"], ORDERS_INTERNAL_SECRET=keys["orders"],
        PAYMENTS_INTERNAL_SECRET=keys["payments"], PAYMENTS_ORDERS_SECRET=keys["payments_orders"], GRAPHQL_INTERNAL_SECRET=keys["graphql"],
        ADMIN_CORREO="admin@example.invalid", ADMIN_PASSWORD="admin-"+SUFFIX,
        INVENTORY_URL=urls["inventory"], CLIENTS_URL=urls["clients"], ORDERS_URL=urls["orders"], PAYMENTS_URL=urls["payments"],
        AUTH_SERVICE_URL=urls["auth"], GRAPHQL_URL=urls["graphql"], VAULT_ADDR=urls["vault"], VAULT_TOKEN=keys["root"])
    running = {}
    for name, module, folder in [("auth", "auth_service:app", "services"), ("inventory", "main:app", "services/inventory"),
                                  ("clients", "main:app", "services/clients"), ("orders", "main:app", "services/orders"),
                                  ("payments", "main:app", "services/payments")]:
        proc = spawn([PYTHON, "-m", "uvicorn", module, "--app-dir", str(ROOT/folder), "--host", "127.0.0.1", "--port", str(ports[name])], ROOT, env)
        wait(urls[name] + "/health", proc)
        running[name] = proc
    nodeenv = env.copy(); nodeenv["PORT"] = str(ports["graphql"])
    graph = spawn(["node", "server.js"], ROOT/"graphql", nodeenv); wait(urls["graphql"] + "/graphql", graph, (403,))
    gateway = spawn([PYTHON, "-m", "uvicorn", "gateway:app", "--app-dir", str(ROOT/"gateway"), "--host", "127.0.0.1", "--port", str(ports["gateway"])], ROOT, env)
    wait(urls["gateway"] + "/health", gateway)
    user = httpx.Client(base_url=urls["gateway"], timeout=10); other = httpx.Client(base_url=urls["gateway"], timeout=10); admin = httpx.Client(base_url=urls["gateway"], timeout=10)
    products = check(user.get('/api/productos?disponible=true'), 200, 'Catálogo público').json(); assert len(products) == 6
    check(user.get('/api/pedidos'), 401, 'Pedidos requieren sesión')
    check(user.post('/api/graphql',json={'query':'{ __typename }'}), 401, 'GraphQL requiere sesión')
    check(httpx.post(urls['graphql']+'/graphql',json={'query':'{ __typename }'}),403,'GraphQL interno rechaza acceso directo')
    check(user.get('/api/internal/productos'), 404, 'Ruta interna no publicada')
    recuperable = {'correo':'recuperable@example.invalid','password':'password-'+SUFFIX,'nombre':'Perfil de prueba'}
    vault_headers = {'X-Vault-Token': keys['root']}
    bad_data = dict(data); bad_data['clients_secret'] = 'secreto-incorrecto'
    check(httpx.post(urls['vault']+'/v1/secret/data/gateway',headers=vault_headers,json={'data':bad_data}),200,'Simular fallo de clientes')
    check(user.post('/auth/register',json=recuperable),503,'Registro incompleto rechazado')
    check(user.post('/auth/login',json={'correo':recuperable['correo'],'password':recuperable['password']}),401,'Cuenta incompleta no inicia sesión')
    check(httpx.post(urls['vault']+'/v1/secret/data/gateway',headers=vault_headers,json={'data':data}),200,'Restaurar secreto de clientes')
    check(user.post('/auth/register',json=recuperable),201,'Reintentar registro incompleto')
    check(user.post('/auth/login',json={'correo':recuperable['correo'],'password':recuperable['password']}),200,'Cuenta recuperada inicia sesión')
    check(user.post('/auth/logout'),200,'Cerrar sesión de recuperación')
    password = 'cliente-' + SUFFIX
    check(user.post('/auth/register', json={'correo':'cliente@example.invalid','password':password,'nombre':'Cliente de prueba'}), 201, 'Registro')
    check(user.post('/auth/register', json={'correo':'cliente@example.invalid','password':password,'nombre':'Cliente de prueba'}), 409, 'Correo duplicado')
    check(user.post('/auth/login', json={'correo':'cliente@example.invalid','password':'incorrecta'}), 401, 'Contraseña incorrecta')
    login=check(user.post('/auth/login', json={'correo':'cliente@example.invalid','password':password}), 200, 'Login')
    assert 'httponly' in login.headers.get('set-cookie','').lower()
    check(user.get('/api/clientes/me'), 200, 'Perfil propio')
    check(user.post('/api/productos', json={}), 403, 'Cliente no administra catálogo')
    check(other.post('/auth/register', json={'correo':'otro@example.invalid','password':password,'nombre':'Otra persona'}), 201, 'Segundo registro')
    check(other.post('/auth/login', json={'correo':'otro@example.invalid','password':password}), 200, 'Segunda sesión')
    payload={'direccion_despacho':'Dirección de prueba 123','items':[{'product_id':products[0]['id'],'cantidad':2,'precio_unitario':1}]}
    headers={'Idempotency-Key':'test-order-1'}
    order=check(user.post('/api/pedidos',json=payload,headers=headers),201,'Crear pedido').json()
    assert order['total']==products[0]['precio']*2
    repeat=check(user.post('/api/pedidos',json=payload,headers=headers),201,'Reintento idempotente').json();assert repeat['id']==order['id']
    changed={**payload,'direccion_despacho':'Otra dirección 456'}
    check(user.post('/api/pedidos',json=changed,headers=headers),409,'Reuso de clave con otros datos')
    check(other.get('/api/pedidos/'+order['id'],headers={'X-Authenticated-User':'falsificado','X-Authenticated-Roles':'admin'}),404,'Cabeceras de identidad falsas ignoradas')
    query={'query':'query($id: ID!) { resumenPedido(id:$id) { id total items { cantidad precio_unitario producto { id nombre } } } }','variables':{'id':order['id']}}
    r=check(user.post('/api/graphql',json=query),200,'GraphQL agrega pedido y producto').json(); assert not r.get('errors'),r; assert r['data']['resumenPedido']['items'][0]['producto']['nombre']==products[0]['nombre']
    r=check(other.post('/api/graphql',json=query),200,'GraphQL consulta ajena').json(); assert r['data']['resumenPedido'] is None
    aliases=' '.join('p'+str(i)+':resumenPedido(id: \"'+order['id']+'\"){ id }' for i in range(101))
    limited=user.post('/api/graphql',json={'query':'{ '+aliases+' }'})
    assert limited.status_code in (200,400) and limited.json().get('errors'), limited.text
    print('Límite de consultas GraphQL: OK',flush=True)
    pago=check(user.post('/api/pagos',json={'pedido_id':order['id']}),201,'Pago de prueba').json();assert pago['modo']=='PRUEBA' and pago['monto']==order['total']
    pago2=check(user.post('/api/pagos',json={'pedido_id':order['id']}),201,'Pago repetido idempotente').json();assert pago['id']==pago2['id']
    assert check(user.get('/api/pedidos/'+order['id']),200,'Estado de pedido').json()['estado']=='PAGADO'
    check(user.patch('/api/pedidos/'+order['id']+'/estado',json={'estado':'EN_PREPARACION'}),403,'Cliente no cambia estado administrativo')
    check(user.post('/api/pedidos/'+order['id']+'/cancelar'),409,'Pedido pagado no se cancela')
    cancel_order=check(user.post('/api/pedidos',json=payload,headers={'Idempotency-Key':'to-cancel'}),201,'Crear pedido cancelable').json()
    assert check(user.post('/api/pedidos/'+cancel_order['id']+'/cancelar'),200,'Cancelar pedido propio').json()['estado']=='CANCELADO'
    check(user.post('/api/pagos',json={'pedido_id':cancel_order['id']}),409,'Pedido cancelado no admite pago')
    check(admin.post('/auth/login',json={'correo':'admin@example.invalid','password':'admin-'+SUFFIX}),200,'Sesión de administrador')
    check(admin.patch('/api/pedidos/'+order['id']+'/estado',json={'estado':'ENTREGADO'}),409,'Transición inválida de pedido')
    for estado in ('EN_PREPARACION','EN_CAMINO','ENTREGADO'):
        assert check(admin.patch('/api/pedidos/'+order['id']+'/estado',json={'estado':estado}),200,'Estado '+estado).json()['estado']==estado
    updated={k:products[0][k] for k in ('numero','nombre','descripcion','precio','imagen','disponible')};updated['disponible']=False
    check(admin.put('/api/productos/'+products[0]['id'],json=updated),200,'Administrar disponibilidad')
    assert len(check(user.get('/api/productos?disponible=false'),200,'Catálogo público no muestra deshabilitados').json())==5
    check(user.get('/api/productos/'+products[0]['id']),404,'Producto deshabilitado no es público')
    assert len(check(admin.get('/api/admin/productos'),200,'Administración ve productos completos').json())==6
    check(user.post('/api/pedidos',json=payload,headers={'Idempotency-Key':'unavailable'}),409,'Producto no disponible')
    running['auth'].terminate(); running['auth'].wait(timeout=10)
    proc = spawn([PYTHON, '-m', 'uvicorn', 'auth_service:app', '--app-dir', str(ROOT/'services'), '--host', '127.0.0.1', '--port', str(ports['auth'])], ROOT, env)
    wait(urls['auth']+'/health',proc)
    check(user.get('/auth/me'),200,'Sesión persiste tras reiniciar Auth Service')
    check(user.post('/auth/logout'),200,'Cerrar sesión');check(user.get('/auth/me'),401,'Sesión revocada')
    running['inventory'].terminate(); running['inventory'].wait(timeout=10)
    check(other.get('/api/productos'),503,'Inventario no disponible')
    proc.terminate(); proc.wait(timeout=10)
    check(other.get('/auth/me'),503,'Auth Service no disponible')
    vault.terminate(); vault.wait(timeout=10)
    check(other.get('/api/productos'),503,'Vault no disponible')
    for client in (user,other,admin):client.close()


if __name__ == '__main__':
    try:
        main()
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
        client = MongoClient('mongodb://127.0.0.1:27017', serverSelectionTimeoutMS=3000)
        for db in DBS.values(): client.drop_database(db)
        client.close()
