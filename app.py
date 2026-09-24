import base64
import os
import sqlite3
import urllib.parse
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from flask import Flask, jsonify, redirect, render_template_string, request, send_file

app = Flask(__name__)

DB_NAME = "creditos.db"


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                orden INTEGER NOT NULL DEFAULT 0,
                nombre TEXT NOT NULL,
                telefono TEXT,
                identificacion TEXT,
                monto REAL NOT NULL,
                interes_porcentaje REAL NOT NULL DEFAULT 20,
                monto_total REAL NOT NULL DEFAULT 0,
                cuotas INTEGER NOT NULL,
                frecuencia TEXT NOT NULL,
                valor_cuota REAL NOT NULL,
                fecha_inicio TEXT NOT NULL,
                saltado_hoy INTEGER NOT NULL DEFAULT 0,
                fecha_gestion TEXT DEFAULT ''
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pagos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id INTEGER NOT NULL,
                numero INTEGER NOT NULL,
                fecha TEXT NOT NULL,
                valor REAL NOT NULL,
                pagado INTEGER NOT NULL DEFAULT 0,
                valor_pagado REAL NOT NULL DEFAULT 0,
                fecha_pago_real TEXT DEFAULT '',
                FOREIGN KEY (cliente_id) REFERENCES clientes (id) ON DELETE CASCADE
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gastos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                categoria TEXT NOT NULL DEFAULT 'Otros',
                concepto TEXT NOT NULL,
                monto REAL NOT NULL,
                fecha TEXT NOT NULL,
                comprobante TEXT DEFAULT ''
            )
        """
        )

        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(clientes)")
        cols_clientes = [col[1] for col in cursor.fetchall()]

        if "interes_porcentaje" not in cols_clientes:
            cursor.execute("ALTER TABLE clientes ADD COLUMN interes_porcentaje REAL NOT NULL DEFAULT 20")
        if "monto_total" not in cols_clientes:
            cursor.execute("ALTER TABLE clientes ADD COLUMN monto_total REAL NOT NULL DEFAULT 0")
            cursor.execute("UPDATE clientes SET monto_total = round(monto * 1.20, 2) WHERE monto_total = 0")
        if "orden" not in cols_clientes:
            cursor.execute("ALTER TABLE clientes ADD COLUMN orden INTEGER NOT NULL DEFAULT 0")
            cursor.execute("UPDATE clientes SET orden = id WHERE orden = 0")
        if "saltado_hoy" not in cols_clientes:
            cursor.execute("ALTER TABLE clientes ADD COLUMN saltado_hoy INTEGER NOT NULL DEFAULT 0")
        if "fecha_gestion" not in cols_clientes:
            cursor.execute("ALTER TABLE clientes ADD COLUMN fecha_gestion TEXT DEFAULT ''")

        cursor.execute("PRAGMA table_info(pagos)")
        cols_pagos = [col[1] for col in cursor.fetchall()]
        if "valor_pagado" not in cols_pagos:
            cursor.execute("ALTER TABLE pagos ADD COLUMN valor_pagado REAL NOT NULL DEFAULT 0")
            cursor.execute("UPDATE pagos SET valor_pagado = valor WHERE pagado = 1")
        if "fecha_pago_real" not in cols_pagos:
            cursor.execute("ALTER TABLE pagos ADD COLUMN fecha_pago_real TEXT DEFAULT ''")

        cursor.execute("PRAGMA table_info(gastos)")
        cols_gastos = [col[1] for col in cursor.fetchall()]
        if "categoria" not in cols_gastos:
            cursor.execute("ALTER TABLE gastos ADD COLUMN categoria TEXT NOT NULL DEFAULT 'Otros'")
        if "comprobante" not in cols_gastos:
            cursor.execute("ALTER TABLE gastos ADD COLUMN comprobante TEXT DEFAULT ''")

        conn.commit()


init_db()


def obtener_clientes_completos():
    conn = get_db()
    clientes = conn.execute("SELECT * FROM clientes ORDER BY orden ASC, id DESC").fetchall()
    resultado = []
    hoy_str = date.today().isoformat()

    for c in clientes:
        cliente_dict = dict(c)
        pagos = conn.execute("SELECT * FROM pagos WHERE cliente_id = ? ORDER BY numero ASC", (c["id"],)).fetchall()
        cliente_dict["pagos"] = [dict(p) for p in pagos]

        atrasadas = 0
        for p in cliente_dict["pagos"]:
            if not p["pagado"] and p["fecha"] <= hoy_str:
                atrasadas += 1
        cliente_dict["cuotas_atrasadas"] = atrasadas
        
        pago_hoy = any(p.get("fecha_pago_real") == hoy_str for p in cliente_dict["pagos"])
        cliente_dict["gestionado_hoy"] = (cliente_dict.get("fecha_gestion") == hoy_str) or pago_hoy

        resultado.append(cliente_dict)

    conn.close()
    return resultado


def calcular_fecha(fecha_inicio, numero, frecuencia, omitir_domingos=True):
    actual = fecha_inicio
    pasos = 0
    while pasos < numero:
        if frecuencia == "Diaria":
            actual += timedelta(days=1)
            if omitir_domingos and actual.weekday() == 6:
                continue
        elif frecuencia == "Semanal":
            actual += timedelta(weeks=1)
        elif frecuencia == "Quincenal":
            actual += timedelta(days=15)
        elif frecuencia == "Mensual":
            actual += relativedelta(months=1)
        pasos += 1
    return actual


CONTENIDO_HTML = """
{% if vista == 'lista' %}
    <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:6px; margin-bottom:12px;">
        <div style="background:#ffffff; padding:10px 8px; border-radius:8px; border-left:4px solid #10b981; box-shadow:0 1px 3px rgba(0,0,0,0.08);">
            <div style="font-size:9px; color:#6b7280; font-weight:800; text-transform:uppercase;">Recaudado</div>
            <div style="font-size:14px; font-weight:800; color:#065f46; margin-top:2px;">${{ "%.2f"|format(total_cobrado_hoy) }}</div>
        </div>
        <div style="background:#ffffff; padding:10px 8px; border-radius:8px; border-left:4px solid #ef4444; box-shadow:0 1px 3px rgba(0,0,0,0.08);">
            <div style="font-size:9px; color:#6b7280; font-weight:800; text-transform:uppercase;">Gastos</div>
            <div style="font-size:14px; font-weight:800; color:#991b1b; margin-top:2px;">${{ "%.2f"|format(total_gastos_hoy) }}</div>
        </div>
        <div style="background:#ffffff; padding:10px 8px; border-radius:8px; border-left:4px solid #00a8cc; box-shadow:0 1px 3px rgba(0,0,0,0.08);">
            <div style="font-size:9px; color:#6b7280; font-weight:800; text-transform:uppercase;">En Calle</div>
            <div style="font-size:14px; font-weight:800; color:#0f2b5c; margin-top:2px;">${{ "%.2f"|format(capital_en_calle) }}</div>
        </div>
    </div>

    <div style="display:flex; gap:6px; margin-bottom:10px; overflow-x:auto;">
        <button onclick="filtrarEstado('todos')" class="btn-filtro active" id="f-todos">👥 Por Cobrar ({{ clientes | length }})</button>
        <button onclick="filtrarEstado('mora')" class="btn-filtro" id="f-mora">⚠️ Mora</button>
        <button onclick="filtrarEstado('aldia')" class="btn-filtro" id="f-aldia">✅ Al Día</button>
    </div>

    <input type="text" id="searchInput" class="search-box" placeholder="🔍 Buscar cliente, ID o negocio..." onkeyup="filtrarClientes()">

    <div id="clientesContainer">
        {% for c in clientes %}
            {% set pagado = c.pagos | map(attribute='valor_pagado') | sum %}
            {% set saldo = c.monto_total - pagado %}
            {% set cuota_pendiente = c.pagos | selectattr('pagado', 'equalto', 0) | list | first %}
            {% set es_mora = c.cuotas_atrasadas > 0 %}

            <div class="card cliente-card" id="cliente-card-{{ c.id }}" data-nombre="{{ c.nombre | lower }}" data-id="{{ c.identificacion or '' }}" data-mora="{{ 1 if es_mora else 0 }}">
                <div class="flex-between">
                    <div style="display:flex; align-items:center; gap:8px;">
                        <div style="display:flex; flex-direction:column; gap:2px;">
                            <a href="/mover/{{ c.id }}/subir" style="text-decoration:none; font-size:10px; background:#f1f5f9; padding:2px 4px; border-radius:3px;">⬆️</a>
                            <a href="/mover/{{ c.id }}/bajar" style="text-decoration:none; font-size:10px; background:#f1f5f9; padding:2px 4px; border-radius:3px;">⬇️</a>
                        </div>
                        <div>
                            <div style="display:flex; align-items:center; gap:6px;">
                                <span style="font-size:14px; font-weight:800; color:#0f2b5c;">{{ c.nombre }}</span>
                                {% if es_mora %}
                                    <span class="badge-mora">MORA ({{ c.cuotas_atrasadas }})</span>
                                {% else %}
                                    <span class="badge-al-dia">AL DÍA</span>
                                {% endif %}
                            </div>
                            <div style="font-size:11px; color:#6b7280; margin-top:2px;">
                                ID/Ref: <b>{{ c.identificacion or 'N/A' }}</b> | Tel: <b>{{ c.telefono or 'N/A' }}</b>
                            </div>
                        </div>
                    </div>

                    <div style="text-align:right;">
                        <div style="font-size:10px; color:#6b7280; font-weight:bold;">Cuota:</div>
                        <div style="font-size:16px; font-weight:800; color:#0f2b5c;">${{ "%.2f"|format(c.valor_cuota) }}</div>
                    </div>
                </div>

                <div style="background:#f8fafc; padding:8px; border-radius:6px; margin:8px 0; display:flex; justify-content:space-between; font-size:11px;">
                    <span>Capital: <b>${{ "%.2f"|format(c.monto) }}</b></span>
                    <span>Total: <b>${{ "%.2f"|format(c.monto_total) }}</b></span>
                    <span>Saldo: <b style="color:#ef4444;">${{ "%.2f"|format(saldo) }}</b></span>
                </div>

                <div style="display:flex; gap:6px; justify-content:space-between; align-items:center;">
                    {% if c.telefono and cuota_pendiente %}
                        {% set msg_recordatorio = "Hola " ~ c.nombre ~ ", recordatorio de pago TryController. Tu cuota pendiente es de $" ~ "%.2f"|format(cuota_pendiente.valor - cuota_pendiente.valor_pagado) ~ ". Saldo pendiente: $" ~ "%.2f"|format(saldo) ~ ". ¡Gracias!" %}
                        <a href="https://wa.me/{{ c.telefono }}?text={{ msg_recordatorio | urlencode }}" target="_blank" style="text-decoration:none; font-size:11px; color:#0284c7; font-weight:bold;">📩 Recordatorio</a>
                    {% else %}
                        <div></div>
                    {% endif %}

                    <div style="display:flex; gap:6px;">
                        {% if cuota_pendiente %}
                            <button class="btn-accion btn-pagar" onclick="ejecutarPago({{ c.id }}, {{ cuota_pendiente.numero }}, {{ cuota_pendiente.valor - cuota_pendiente.valor_pagado }})">💵 Recaudar</button>
                            <button class="btn-accion btn-abono" onclick="abrirModalAbono({{ c.id }}, {{ cuota_pendiente.numero }}, {{ cuota_pendiente.valor - cuota_pendiente.valor_pagado }})">✏️ Abono</button>
                            <button class="btn-accion btn-nopagar" onclick="ejecutarNoPago({{ c.id }})">❌ Saltar</button>
                        {% endif %}
                        {% if saldo <= 0.001 %}
                            <a href="/renovar/{{ c.id }}" style="text-decoration:none;"><button class="btn-accion" style="background:#f59e0b; color:white;">🔄 Renovar</button></a>
                        {% endif %}
                    </div>
                </div>
            </div>
        {% else %}
            <p style="text-align:center; color:#6b7280; margin-top:20px;">✅ ¡Excelente! No tienes cobranzas pendientes en este momento.</p>
        {% endfor %}
    </div>

{% elif vista == 'nuevo' or vista == 'renovar' %}
    <h3 style="margin-top:0; color:#0f2b5c;">{% if vista == 'renovar' %}🔄 Renovar Crédito a {{ cliente.nombre }}{% else %}👤 Registro de Crédito / Venta{% endif %}</h3>
    <div class="card" style="padding:16px;">
        <form action="{% if vista == 'renovar' %}/procesar_renovacion{% else %}/guardar{% endif %}" method="POST">
            {% if vista == 'renovar' %}<input type="hidden" name="cliente_id" value="{{ cliente.id }}">{% endif %}
            
            <label style="font-size:11px; font-weight:bold; color:#4b5563;">Nombre del Cliente</label>
            <input type="text" name="nombre" value="{{ cliente.nombre if cliente else '' }}" placeholder="Ej: Juan Perez" required>
            
            <label style="font-size:11px; font-weight:bold; color:#4b5563;">Teléfono WhatsApp</label>
            <input type="text" name="telefono" value="{{ cliente.telefono if cliente else '' }}" placeholder="Ej: 593991234567">
            
            <label style="font-size:11px; font-weight:bold; color:#4b5563;">Identificación / Centro de Negocio</label>
            <input type="text" name="identificacion" value="{{ cliente.identificacion if cliente else '' }}" placeholder="Ej: Cédula o RUC">
            
            <label style="font-size:11px; font-weight:bold; color:#4b5563;">Monto Financiado ($)</label>
            <input type="number" step="any" id="calcMonto" name="monto" placeholder="Ej: 300" oninput="calcularCuota()" required>
            
            <label style="font-size:11px; font-weight:bold; color:#4b5563;">Porcentaje de Interés (%)</label>
            <input type="number" step="any" id="calcInteres" name="interes_porcentaje" value="20" oninput="calcularCuota()" required>

            <label style="font-size:11px; font-weight:bold; color:#4b5563;">Número de Cuotas</label>
            <input type="number" id="calcCuotas" name="cuotas" placeholder="Ej: 24" oninput="calcularCuota()" required>
            
            <div style="background:#f0f9ff; border:1px solid #bae6fd; padding:10px; border-radius:6px; margin-bottom:10px;">
                <div style="font-size:11px; color:#0369a1; font-weight:bold;">Simulación de Cuota:</div>
                <div style="font-size:16px; font-weight:800; color:#0f2b5c;" id="simulacionText">$0.00 / cuota</div>
            </div>

            <label style="font-size:11px; font-weight:bold; color:#4b5563;">Frecuencia de Recaudo</label>
            <select name="frecuencia">
                <option value="Diaria">Diaria</option>
                <option value="Semanal">Semanal</option>
                <option value="Quincenal">Quincenal</option>
                <option value="Mensual">Mensual</option>
            </select>
            
            <label style="font-size:11px; font-weight:bold; color:#4b5563;">Fecha Primer Pago</label>
            <input type="date" name="fecha_inicio" value="{{ hoy }}" required>
            
            <button type="submit" class="btn-primary" style="background:#00a8cc; margin-top:10px;">💾 Confirmar y Guardar</button>
        </form>
    </div>

{% elif vista == 'cierre' %}
    <h3 style="margin-top:0; color:#0f2b5c;">💼 Cierre de Caja y Recaudo Diario</h3>
    <div class="card" style="padding:16px;">
        <div style="font-size:12px; color:#6b7280;">Fecha de Auditoría: <b>{{ hoy }}</b></div>
        
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin:12px 0;">
            <div style="background:#ecfdf5; padding:10px; border-radius:6px; border:1px solid #a7f3d0;">
                <div style="font-size:10px; color:#065f46; font-weight:bold;">+ Total Recaudado</div>
                <div style="font-size:18px; font-weight:800; color:#047857;">${{ "%.2f"|format(total_cobrado_hoy) }}</div>
            </div>
            <div style="background:#fef2f2; padding:10px; border-radius:6px; border:1px solid #fecaca;">
                <div style="font-size:10px; color:#991b1b; font-weight:bold;">- Total Gastos</div>
                <div style="font-size:18px; font-weight:800; color:#b91c1c;">${{ "%.2f"|format(total_gastos_hoy) }}</div>
            </div>
        </div>

        <div style="background:#f0f9ff; border:1px solid #bae6fd; padding:12px; border-radius:8px; text-align:center; margin-bottom:12px;">
            <div style="font-size:11px; color:#0369a1; font-weight:bold;">Efectivo Neto a Entregar (Caja Final)</div>
            <div style="font-size:24px; font-weight:800; color:#0f2b5c;">${{ "%.2f"|format(total_cobrado_hoy - total_gastos_hoy) }}</div>
        </div>

        <button onclick="alert('Cierre de caja generado correctamente')" class="btn-primary" style="background:#0f2b5c;">🔒 Finalizar y Cerrar Caja Hoy</button>
    </div>

{% elif vista == 'gastos' %}
    <h3 style="margin-top:0; color:#0f2b5c;">💸 Registro de Egresos y Gastos</h3>

    <div class="card" style="padding:16px;">
        <form action="/guardar_gasto" method="POST" enctype="multipart/form-data">
            <label style="font-size:11px; font-weight:bold; color:#4b5563;">Categoría del Egreso</label>
            <select name="categoria" required>
                <option value="Combustible">⛽ Combustible / Gasolina</option>
                <option value="Alimentación">🍔 Alimentación / Almuerzo</option>
                <option value="Mantenimiento">🛠️ Mantenimiento Vehículo</option>
                <option value="Viáticos">🎒 Viáticos de Ruta</option>
                <option value="Sueldos">💵 Sueldos / Pagos</option>
                <option value="Otros" selected>📦 Otros Egresos</option>
            </select>

            <label style="font-size:11px; font-weight:bold; color:#4b5563;">Descripción / Detalle <span style="font-weight:normal; color:#9ca3af;">(Opcional)</span></label>
            <input type="text" name="concepto" placeholder="Ej: Tanqueo de moto (Opcional)">
            
            <label style="font-size:11px; font-weight:bold; color:#4b5563;">Monto del Egreso ($)</label>
            <input type="number" step="any" name="monto" placeholder="Ej: 15.00" required>
            
            <label style="font-size:11px; font-weight:bold; color:#4b5563;">📸 Foto de Factura / Comprobante <span style="font-weight:normal; color:#9ca3af;">(Opcional)</span></label>
            <input type="file" name="foto_comprobante" accept="image/*" capture="environment" style="padding:6px;">

            <button type="submit" class="btn-primary" style="background:#ef4444; margin-top:8px;">➕ Registrar Egreso</button>
        </form>
    </div>

    <div style="background:#fef2f2; border:1px solid #fecaca; padding:12px; border-radius:8px; display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
        <span style="font-size:12px; font-weight:800; color:#991b1b;">Total Egresos Registrados Hoy:</span>
        <span style="font-size:18px; font-weight:800; color:#dc2626;">-${{ "%.2f"|format(total_gastos_hoy) }}</span>
    </div>

    <div class="card" style="padding:16px;">
        <h4 style="margin-top:0; font-size:13px; color:#0f2b5c;">📋 Historial de Egresos de Hoy</h4>
        {% if gastos_list %}
            <div style="overflow-x:auto;">
                <table style="width:100%; border-collapse:collapse; font-size:12px;">
                    <thead>
                        <tr style="background:#f1f5f9; text-align:left; color:#475569;">
                            <th style="padding:8px;">Categoría</th>
                            <th style="padding:8px;">Detalle</th>
                            <th style="padding:8px; text-align:right;">Monto</th>
                            <th style="padding:8px; text-align:center;">Foto</th>
                            <th style="padding:8px; text-align:center;">Borrar</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for g in gastos_list %}
                        <tr style="border-bottom:1px solid #e2e8f0;">
                            <td style="padding:8px;"><span style="background:#f3f4f6; padding:2px 6px; border-radius:4px; font-size:10px; font-weight:bold;">{{ g.categoria }}</span></td>
                            <td style="padding:8px;"><b>{{ g.concepto }}</b></td>
                            <td style="padding:8px; text-align:right; color:#dc2626; font-weight:bold;">-${{ "%.2f"|format(g.monto) }}</td>
                            <td style="padding:8px; text-align:center;">
                                {% if g.comprobante %}
                                    <button type="button" onclick="verFoto('{{ g.comprobante }}')" style="padding:2px 6px; font-size:10px; background:#00a8cc; color:white; border:none; border-radius:4px; cursor:pointer;">📷 Ver</button>
                                {% else %}
                                    <span style="color:#9ca3af; font-size:10px;">Sin foto</span>
                                {% endif %}
                            </td>
                            <td style="padding:8px; text-align:center;">
                                <a href="/eliminar_gasto/{{ g.id }}" onclick="return confirm('¿Eliminar este gasto?')" style="text-decoration:none; font-size:12px;">🗑️</a>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        {% else %}
            <p style="font-size:12px; color:#6b7280; text-align:center; margin:10px 0;">No hay egresos registrados el día de hoy.</p>
        {% endif %}
    </div>

{% elif vista == 'resumen' %}
    <h3 style="margin-top:0; color:#0f2b5c;">📊 Reportes y Copia de Seguridad</h3>
    <div class="card" style="padding:16px;">
        <p style="margin:6px 0;"><b>👥 Clientes en Sistema:</b> {{ clientes | length }}</p>
        <p style="margin:6px 0;"><b>🏙️ Capital Total Prestado:</b> ${{ "%.2f"|format(total_capital) }}</p>
        <p style="margin:6px 0;"><b>💰 Cartera Total con Interés:</b> ${{ "%.2f"|format(total_creditos) }}</p>
        <p style="margin:6px 0;"><b>✅ Total Recaudado:</b> <span style="color:#10b981; font-weight:bold;">${{ "%.2f"|format(total_cobrado) }}</span></p>
        <p style="margin:6px 0;"><b>📌 Pendiente de Recaudo:</b> <span style="color:#ef4444; font-weight:bold;">${{ "%.2f"|format(total_creditos - total_cobrado) }}</span></p>
    </div>

    <div class="card" style="padding:16px;">
        <h4 style="margin-top:0;">💾 Copia de Seguridad</h4>
        <a href="/respaldo" style="text-decoration:none;"><button class="btn-primary" style="background:#0f2b5c;">📥 Descargar Base de Datos (.db)</button></a>
    </div>
{% endif %}
"""

HTML_TEMPLATE = f"""
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AVANTA PAGOS</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; font-family: 'Inter', sans-serif; }}
        body {{ background: #f1f5f9; margin: 0; padding: 10px; color: #0f172a; }}
        
        .header {{ background: #0f2b5c; padding: 10px 14px; border-radius: 8px; margin-bottom: 12px; display: flex; align-items: center; justify-content: space-between; color: white; box-shadow: 0 4px 10px rgba(15, 43, 92, 0.2); }}
        .btn-menu {{ background: rgba(255,255,255,0.15); color: white; border: none; padding: 6px 12px; border-radius: 6px; font-weight: 700; font-size: 13px; cursor: pointer; display: flex; align-items: center; gap: 6px; }}
        .brand-title {{ font-size: 14px; font-weight: 800; letter-spacing: 0.5px; color: #ffffff; display: flex; align-items: center; gap: 6px; }}
        .badge-status {{ background: #00a8cc; color: white; padding: 3px 8px; border-radius: 12px; font-size: 10px; font-weight: 800; }}

        .drawer-overlay {{ display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(15,23,42,0.6); z-index: 300; }}
        .drawer-overlay.active {{ display: block; }}
        
        .drawer {{ position: fixed; top: 0; left: -280px; width: 280px; height: 100%; background: #ffffff; z-index: 301; transition: left 0.3s ease; box-shadow: 4px 0 20px rgba(0,0,0,0.2); display: flex; flex-direction: column; }}
        .drawer.active {{ left: 0; }}
        .drawer-header {{ background: #0f2b5c; color: white; padding: 18px 16px; }}
        .drawer-menu {{ list-style: none; padding: 0; margin: 0; flex: 1; }}
        .drawer-menu li a {{ display: flex; align-items: center; gap: 10px; padding: 13px 18px; color: #334155; text-decoration: none; font-weight: 700; font-size: 13px; border-bottom: 1px solid #f1f5f9; }}
        .drawer-menu li a:active {{ background: #e0f2fe; color: #00a8cc; }}

        .card {{ background: #ffffff; padding: 12px; border-radius: 8px; margin-bottom: 10px; border: 1px solid #e2e8f0; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
        .badge-mora {{ background: #fee2e2; color: #dc2626; font-size: 9px; font-weight: 800; padding: 2px 6px; border-radius: 4px; }}
        .badge-al-dia {{ background: #d1fae5; color: #059669; font-size: 9px; font-weight: 800; padding: 2px 6px; border-radius: 4px; }}

        .btn-filtro {{ background: #e2e8f0; border: none; padding: 6px 12px; border-radius: 20px; font-size: 11px; font-weight: bold; color: #475569; cursor: pointer; white-space: nowrap; }}
        .btn-filtro.active {{ background: #0f2b5c; color: white; }}

        .flex-between {{ display: flex; justify-content: space-between; align-items: center; }}
        .search-box {{ width: 100%; padding: 10px 12px; border: 1px solid #cbd5e1; border-radius: 6px; margin-bottom: 10px; font-size: 13px; outline: none; background: white; }}

        .btn-accion {{ border: none; padding: 6px 10px; border-radius: 6px; font-weight: 800; font-size: 11px; cursor: pointer; display: inline-flex; align-items: center; gap: 4px; text-decoration: none; }}
        .btn-pagar {{ background: #10b981; color: white; }}
        .btn-abono {{ background: #00a8cc; color: white; }}
        .btn-nopagar {{ background: #ef4444; color: white; }}

        input, select, button {{ width: 100%; padding: 10px; margin: 4px 0 10px 0; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 13px; }}
        button.btn-primary {{ color: white; font-weight: 800; border: none; cursor: pointer; }}

        .modal {{ display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(15,23,42,0.6); z-index: 400; justify-content: center; align-items: center; padding: 16px; }}
        .modal-content {{ background: white; border-radius: 10px; padding: 20px; width: 100%; max-width: 360px; text-align: center; }}
        .btn-ws {{ background: #25d366; color: white; text-decoration: none; display: block; padding: 10px; border-radius: 6px; font-weight: bold; margin-top: 8px; }}

        @media print {{
            body * {{ visibility: hidden; }}
            #ticketPrint, #ticketPrint * {{ visibility: visible; }}
            #ticketPrint {{ position: absolute; left: 0; top: 0; width: 58mm; font-family: monospace; font-size: 11px; color: black; background: white; }}
        }}
    </style>
</head>
<body>

<div class="header">
    <button class="btn-menu" onclick="toggleDrawer()">☰ Menú</button>
    <div class="brand-title">🌐AVANTA PAGOS</div>
    <span class="badge-status">EN LÍNEA</span>
</div>

<div class="drawer-overlay" id="drawerOverlay" onclick="toggleDrawer()"></div>
<div class="drawer" id="drawer">
    <div class="drawer-header">
        <div style="font-weight:800; font-size:15px;">🌐AVANTA PAGOS</div>
        <div style="font-size:11px; opacity:0.8; margin-top:2px;">Centro de Negocio (CN-01)</div>
    </div>
    <ul class="drawer-menu">
        <li><a href="#" onclick="navegarRuta('/')">📍 Ruta de Cobranza</a></li>
        <li><a href="#" onclick="navegarRuta('/nuevo')">👤 Nuevo Crédito / Venta</a></li>
        <li><a href="#" onclick="navegarRuta('/gastos')">💸 Registrar Gastos</a></li>
        <li><a href="#" onclick="navegarRuta('/cierre')">💼 Cierre y Recaudo Diario</a></li>
        <li><a href="#" onclick="navegarRuta('/resumen')">📊 Reporte y Backup</a></li>
    </ul>
</div>

<div id="mainContent">
    {CONTENIDO_HTML}
</div>

<div class="modal" id="modalWs">
    <div class="modal-content">
        <h3 style="margin-top:0; color:#10b981;">✅ Recaudo Exitoso</h3>
        <p id="modalMsg">Se ha procesado el pago correctamente.</p>
        <a href="#" id="modalWsBtn" target="_blank" class="btn-ws">📲 Enviar Comprobante WhatsApp</a>
        <button onclick="imprimirTicket()" class="btn-primary" style="background:#0f2b5c; margin-top:8px;">🖨️ Imprimir Ticket POS</button>
        <button onclick="cerrarModal('modalWs')" style="margin-top:4px; background:#e2e8f0; border:none; color:#0f172a;">Cerrar</button>
    </div>
</div>

<div class="modal" id="modalAbono">
    <div class="modal-content">
        <h3>✏️ Registrar Abono Parcial</h3>
        <p style="font-size:12px; color:#6b7280;">Monto restante de la cuota: <b id="abonoPendienteText">$0.00</b></p>
        <input type="hidden" id="abonoClienteId">
        <input type="hidden" id="abonoNumCuota">
        <input type="number" step="any" id="abonoMontoInput" placeholder="Ingresa valor del abono ($)">
        <button onclick="confirmarAbono()" class="btn-primary" style="background:#00a8cc;">💾 Guardar Abono</button>
        <button onclick="cerrarModal('modalAbono')" style="margin-top:4px; background:#e2e8f0; border:none; color:#0f172a;">Cancelar</button>
    </div>
</div>

<!-- Modal para ver Foto Factura -->
<div class="modal" id="modalFoto">
    <div class="modal-content" style="max-width:90%;">
        <h3 style="margin-top:0; font-size:14px;">📸 Comprobante de Egreso</h3>
        <img id="imgComprobante" src="" style="width:100%; max-height:60vh; object-fit:contain; border-radius:8px; border:1px solid #cbd5e1;">
        <button onclick="cerrarModal('modalFoto')" style="margin-top:10px; background:#0f2b5c; color:white; font-weight:bold; border:none;">Cerrar Imagen</button>
    </div>
</div>

<div id="ticketPrint" style="display:none;">
    ==============================<br>
    &nbsp;&nbsp;<b>AVANTA PAGOS RECAUDOS</b><br>
    ==============================<br>
    Fecha: <span id="tFecha"></span><br>
    Cliente: <span id="tCliente"></span><br>
    Cuota #: <span id="tCuota"></span><br>
    Cobrado: $<span id="tMonto"></span><br>
    Saldo: $<span id="tSaldo"></span><br>
    ==============================<br>
    &nbsp;&nbsp;¡Gracias por su pago!<br>
    ==============================
</div>

<script>
function toggleDrawer() {{
    document.getElementById('drawer').classList.toggle('active');
    document.getElementById('drawerOverlay').classList.toggle('active');
}}

function navegarRuta(url) {{
    const overlay = document.getElementById('drawerOverlay');
    if (overlay.classList.contains('active')) {{
        toggleDrawer();
    }}
    fetch(url, {{ headers: {{ 'X-Requested-With': 'XMLHttpRequest' }} }})
        .then(res => res.text())
        .then(html => {{
            document.getElementById('mainContent').innerHTML = html;
        }});
}}

function filtrarClientes() {{
    const query = document.getElementById('searchInput').value.toLowerCase();
    const cards = document.querySelectorAll('.cliente-card');
    cards.forEach(card => {{
        const nombre = card.getAttribute('data-nombre');
        const id = card.getAttribute('data-id');
        if (nombre.includes(query) || id.includes(query)) {{
            card.style.display = "block";
        }} else {{
            card.style.display = "none";
        }}
    }});
}}

function filtrarEstado(tipo) {{
    document.querySelectorAll('.btn-filtro').forEach(b => b.classList.remove('active'));
    document.getElementById(`f-${{tipo}}`).classList.add('active');

    const cards = document.querySelectorAll('.cliente-card');
    cards.forEach(card => {{
        const esMora = card.getAttribute('data-mora') === '1';
        if (tipo === 'todos') card.style.display = "block";
        else if (tipo === 'mora' && esMora) card.style.display = "block";
        else if (tipo === 'aldia' && !esMora) card.style.display = "block";
        else card.style.display = "none";
    }});
}}

function calcularCuota() {{
    const monto = parseFloat(document.getElementById('calcMonto').value) || 0;
    const interes = parseFloat(document.getElementById('calcInteres').value) || 0;
    const cuotas = parseInt(document.getElementById('calcCuotas').value) || 0;

    if (monto > 0 && cuotas > 0) {{
        const total = monto * (1 + (interes / 100));
        const valorCuota = total / cuotas;
        document.getElementById('simulacionText').innerText = `$${{valorCuota.toFixed(2)}} / cuota (Total: $${{total.toFixed(2)}})`;
    }} else {{
        document.getElementById('simulacionText').innerText = "$0.00 / cuota";
    }}
}}

function ejecutarPago(clienteId, numCuota, monto) {{
    procesarPagoAPI(clienteId, numCuota, monto);
}}

function abrirModalAbono(clienteId, numCuota, pendiente) {{
    document.getElementById('abonoClienteId').value = clienteId;
    document.getElementById('abonoNumCuota').value = numCuota;
    document.getElementById('abonoPendienteText').innerText = `$${{pendiente.toFixed(2)}}`;
    document.getElementById('abonoMontoInput').value = pendiente;
    document.getElementById('modalAbono').style.display = 'flex';
}}

function confirmarAbono() {{
    const clienteId = document.getElementById('abonoClienteId').value;
    const numCuota = document.getElementById('abonoNumCuota').value;
    const monto = parseFloat(document.getElementById('abonoMontoInput').value);

    if (isNaN(monto) || monto <= 0) {{
        alert("Ingresa un monto válido");
        return;
    }}
    cerrarModal('modalAbono');
    procesarPagoAPI(clienteId, numCuota, monto);
}}

function procesarPagoAPI(clienteId, numCuota, monto) {{
    fetch(`/api/marcar_pago/${{clienteId}}/${{numCuota}}?monto=${{monto}}`)
        .then(res => res.json())
        .then(data => {{
            if (data.status === 'ok') {{
                const card = document.getElementById(`cliente-card-${{clienteId}}`);
                if (card) {{
                    card.style.display = 'none';
                }}

                if (data.recibo && data.recibo.telefono) {{
                    document.getElementById('modalMsg').innerText = `Recaudo de $${{data.recibo.monto.toFixed(2)}} registrado para ${{data.recibo.cliente}}.`;
                    document.getElementById('modalWsBtn').href = `https://wa.me/${{data.recibo.telefono}}?text=${{data.recibo.mensaje_ws}}`;
                    
                    document.getElementById('tFecha').innerText = new Date().toLocaleDateString();
                    document.getElementById('tCliente').innerText = data.recibo.cliente;
                    document.getElementById('tCuota').innerText = data.recibo.cuota;
                    document.getElementById('tMonto').innerText = data.recibo.monto.toFixed(2);
                    document.getElementById('tSaldo').innerText = data.recibo.saldo.toFixed(2);

                    document.getElementById('modalWs').style.display = 'flex';
                }}
            }}
        }});
}}

function ejecutarNoPago(clienteId) {{
    fetch(`/api/marcar_no_pago/${{clienteId}}`)
        .then(res => res.json())
        .then(data => {{
            if (data.status === 'ok') {{
                const card = document.getElementById(`cliente-card-${{clienteId}}`);
                if (card) {{
                    card.style.display = 'none';
                }}
            }}
        }});
}}

function verFoto(srcBase64) {{
    document.getElementById('imgComprobante').src = srcBase64;
    document.getElementById('modalFoto').style.display = 'flex';
}}

function imprimirTicket() {{
    const printContent = document.getElementById('ticketPrint');
    printContent.style.display = 'block';
    window.print();
    printContent.style.display = 'none';
}}

function cerrarModal(id) {{
    document.getElementById(id).style.display = 'none';
}}
</script>

</body>
</html>
"""


@app.route("/")
def lista():
    todos_clientes = obtener_clientes_completos()
    hoy_str = date.today().isoformat()

    conn = get_db()
    total_cobrado_hoy = conn.execute(
        "SELECT COALESCE(SUM(valor_pagado), 0) FROM pagos WHERE fecha_pago_real = ?",
        (hoy_str,),
    ).fetchone()[0]

    total_gastos_hoy = conn.execute(
        "SELECT COALESCE(SUM(monto), 0) FROM gastos WHERE fecha = ?",
        (hoy_str,),
    ).fetchone()[0]
    conn.close()

    clientes_filtrados = [
        c for c in todos_clientes
        if any(not p["pagado"] for p in c["pagos"]) and not c.get("gestionado_hoy", False)
    ]

    capital_en_calle = sum(
        max(0.0, c["monto_total"] - sum(p["valor_pagado"] for p in c["pagos"]))
        for c in todos_clientes
    )

    contexto = dict(
        vista="lista",
        clientes=clientes_filtrados,
        total_cobrado_hoy=total_cobrado_hoy,
        total_gastos_hoy=total_gastos_hoy,
        capital_en_calle=capital_en_calle,
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template_string(CONTENIDO_HTML, **contexto)

    return render_template_string(HTML_TEMPLATE, **contexto)


@app.route("/api/marcar_pago/<int:cliente_id>/<int:num_cuota>")
def api_marcar_pago(cliente_id, num_cuota):
    monto_ingresado = float(request.args.get("monto", 0))
    hoy_str = date.today().isoformat()

    conn = get_db()
    pago = conn.execute(
        "SELECT * FROM pagos WHERE cliente_id = ? AND numero = ?",
        (cliente_id, num_cuota),
    ).fetchone()

    if pago:
        nuevo_valor_pagado = pago["valor_pagado"] + monto_ingresado
        esta_pagado = 1 if nuevo_valor_pagado >= (pago["valor"] - 0.01) else 0

        conn.execute(
            "UPDATE pagos SET valor_pagado = ?, pagado = ?, fecha_pago_real = ? WHERE cliente_id = ? AND numero = ?",
            (nuevo_valor_pagado, esta_pagado, hoy_str, cliente_id, num_cuota),
        )
        conn.execute(
            "UPDATE clientes SET saltado_hoy = 0, fecha_gestion = ? WHERE id = ?",
            (hoy_str, cliente_id),
        )
        conn.commit()

    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    pagos = conn.execute("SELECT * FROM pagos WHERE cliente_id = ?", (cliente_id,)).fetchall()

    pagado_total = sum(p["valor_pagado"] for p in pagos)
    saldo_restante = max(0.0, cliente["monto_total"] - pagado_total)

    texto_ws = (
        f"🌐 *TRYCONTROLLER - COMPROBANTE DE RECAUDO*\n\n"
        f"Cliente: *{cliente['nombre']}*\n"
        f"🔹 Cuota: {num_cuota}/{len(pagos)}\n"
        f"🔹 Recaudado: ${monto_ingresado:.2f}\n"
        f"🔹 Saldo Pendiente: ${saldo_restante:.2f}\n\n"
        f"¡Gracias por su pago!"
    )

    recibo = {
        "cliente": cliente["nombre"],
        "cuota": num_cuota,
        "monto": monto_ingresado,
        "saldo": saldo_restante,
        "telefono": cliente["telefono"],
        "mensaje_ws": urllib.parse.quote(texto_ws),
    }
    conn.close()

    return jsonify({"status": "ok", "recibo": recibo})


@app.route("/api/marcar_no_pago/<int:cliente_id>")
def api_marcar_no_pago(cliente_id):
    hoy_str = date.today().isoformat()
    conn = get_db()
    conn.execute("UPDATE clientes SET saltado_hoy = 1, fecha_gestion = ? WHERE id = ?", (hoy_str, cliente_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/mover/<int:cliente_id>/<string:direccion>")
def mover(cliente_id, direccion):
    conn = get_db()
    clientes = conn.execute("SELECT id, orden FROM clientes ORDER BY orden ASC, id DESC").fetchall()
    index = next((i for i, c in enumerate(clientes) if c["id"] == cliente_id), None)

    if index is not None:
        if direccion == "subir" and index > 0:
            actual, anterior = clientes[index], clientes[index - 1]
            conn.execute("UPDATE clientes SET orden = ? WHERE id = ?", (anterior["orden"], actual["id"]))
            conn.execute("UPDATE clientes SET orden = ? WHERE id = ?", (actual["orden"], anterior["id"]))
        elif direccion == "bajar" and index < len(clientes) - 1:
            actual, siguiente = clientes[index], clientes[index + 1]
            conn.execute("UPDATE clientes SET orden = ? WHERE id = ?", (siguiente["orden"], actual["id"]))
            conn.execute("UPDATE clientes SET orden = ? WHERE id = ?", (actual["orden"], siguiente["id"]))
        conn.commit()
    conn.close()
    return redirect(request.referrer or "/")


@app.route("/nuevo")
def nuevo():
    contexto = dict(vista="nuevo", hoy=date.today().isoformat(), cliente=None)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template_string(CONTENIDO_HTML, **contexto)
    return render_template_string(HTML_TEMPLATE, **contexto)


@app.route("/guardar", methods=["POST"])
def guardar():
    try:
        nombre = request.form.get("nombre", "").strip()
        telefono = request.form.get("telefono", "").replace("+", "").replace(" ", "").strip()
        identificacion = request.form.get("identificacion", "").strip()
        monto = float(request.form.get("monto", "0").replace(",", "."))
        interes_porcentaje = float(request.form.get("interes_porcentaje", "20").replace(",", "."))
        cuotas = int(request.form.get("cuotas", "0"))
        frecuencia = request.form.get("frecuencia")
        fecha_inicio = date.fromisoformat(request.form.get("fecha_inicio"))

        monto_total = round(monto * (1 + (interes_porcentaje / 100)), 2)
        valor_base = round(monto_total / cuotas, 2)
        acumulado = 0.0

        conn = get_db()
        cursor = conn.cursor()

        max_orden = cursor.execute("SELECT COALESCE(MAX(orden), 0) FROM clientes").fetchone()[0]
        cursor.execute(
            """
            INSERT INTO clientes (orden, nombre, telefono, identificacion, monto, interes_porcentaje, monto_total, cuotas, frecuencia, valor_cuota, fecha_inicio, saltado_hoy, fecha_gestion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '')
        """,
            (max_orden + 1, nombre, telefono, identificacion, monto, interes_porcentaje, monto_total, cuotas, frecuencia, valor_base, fecha_inicio.isoformat()),
        )
        cliente_id = cursor.lastrowid

        for num in range(1, cuotas + 1):
            valor_cuota = round(monto_total - acumulado, 2) if num == cuotas else valor_base
            acumulado += valor_cuota
            f_cuota = calcular_fecha(fecha_inicio, num - 1, frecuencia)
            cursor.execute(
                "INSERT INTO pagos (cliente_id, numero, fecha, valor, pagado, valor_pagado, fecha_pago_real) VALUES (?, ?, ?, ?, 0, 0, '')",
                (cliente_id, num, f_cuota.isoformat(), valor_cuota),
            )

        conn.commit()
        conn.close()
        return redirect("/")
    except Exception:
        return redirect("/")


@app.route("/renovar/<int:cliente_id>")
def renovar(cliente_id):
    conn = get_db()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    conn.close()
    if not cliente:
        return redirect("/")

    contexto = dict(vista="renovar", cliente=dict(cliente), hoy=date.today().isoformat())
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template_string(CONTENIDO_HTML, **contexto)
    return render_template_string(HTML_TEMPLATE, **contexto)


@app.route("/procesar_renovacion", methods=["POST"])
def procesar_renovacion():
    try:
        cliente_id = int(request.form.get("cliente_id"))
        monto = float(request.form.get("monto", "0").replace(",", "."))
        interes_porcentaje = float(request.form.get("interes_porcentaje", "20").replace(",", "."))
        cuotas = int(request.form.get("cuotas", "0"))
        frecuencia = request.form.get("frecuencia")
        fecha_inicio = date.fromisoformat(request.form.get("fecha_inicio"))

        monto_total = round(monto * (1 + (interes_porcentaje / 100)), 2)
        valor_base = round(monto_total / cuotas, 2)
        acumulado = 0.0

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE clientes 
            SET monto = ?, interes_porcentaje = ?, monto_total = ?, cuotas = ?, frecuencia = ?, valor_cuota = ?, fecha_inicio = ?, saltado_hoy = 0, fecha_gestion = ''
            WHERE id = ?
        """,
            (monto, interes_porcentaje, monto_total, cuotas, frecuencia, valor_base, fecha_inicio.isoformat(), cliente_id),
        )

        cursor.execute("DELETE FROM pagos WHERE cliente_id = ?", (cliente_id,))

        for num in range(1, cuotas + 1):
            valor_cuota = round(monto_total - acumulado, 2) if num == cuotas else valor_base
            acumulado += valor_cuota
            f_cuota = calcular_fecha(fecha_inicio, num - 1, frecuencia)
            cursor.execute(
                "INSERT INTO pagos (cliente_id, numero, fecha, valor, pagado, valor_pagado, fecha_pago_real) VALUES (?, ?, ?, ?, 0, 0, '')",
                (cliente_id, num, f_cuota.isoformat(), valor_cuota),
            )

        conn.commit()
        conn.close()
        return redirect("/")
    except Exception:
        return redirect("/")


@app.route("/gastos")
def gastos():
    hoy_str = date.today().isoformat()
    conn = get_db()
    gastos_list = conn.execute(
        "SELECT * FROM gastos WHERE fecha = ? ORDER BY id DESC", (hoy_str,)
    ).fetchall()
    total_gastos_hoy = sum(g["monto"] for g in gastos_list)
    conn.close()

    contexto = dict(
        vista="gastos",
        gastos_list=[dict(g) for g in gastos_list],
        total_gastos_hoy=total_gastos_hoy,
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template_string(CONTENIDO_HTML, **contexto)
    return render_template_string(HTML_TEMPLATE, **contexto)


@app.route("/guardar_gasto", methods=["POST"])
def guardar_gasto():
    categoria = request.form.get("categoria", "Otros")
    concepto = request.form.get("concepto", "").strip()
    monto = float(request.form.get("monto", "0").replace(",", "."))
    hoy_str = date.today().isoformat()

    if not concepto:
        concepto = f"Gasto de {categoria}"

    comprobante_b64 = ""
    file = request.files.get("foto_comprobante")
    if file and file.filename:
        file_bytes = file.read()
        if file_bytes:
            encoded = base64.b64encode(file_bytes).decode("utf-8")
            comprobante_b64 = f"data:{file.content_type};base64,{encoded}"

    if monto > 0:
        conn = get_db()
        conn.execute(
            "INSERT INTO gastos (categoria, concepto, monto, fecha, comprobante) VALUES (?, ?, ?, ?, ?)",
            (categoria, concepto, monto, hoy_str, comprobante_b64),
        )
        conn.commit()
        conn.close()

    return redirect("/gastos")


@app.route("/eliminar_gasto/<int:gasto_id>")
def eliminar_gasto(gasto_id):
    conn = get_db()
    conn.execute("DELETE FROM gastos WHERE id = ?", (gasto_id,))
    conn.commit()
    conn.close()
    return redirect("/gastos")


@app.route("/cierre")
def cierre():
    hoy_str = date.today().isoformat()
    conn = get_db()
    
    pagos_hoy = conn.execute("SELECT valor_pagado FROM pagos WHERE fecha_pago_real = ?", (hoy_str,)).fetchall()
    total_cobrado_hoy = sum(p["valor_pagado"] for p in pagos_hoy)

    gastos_hoy = conn.execute("SELECT COALESCE(SUM(monto), 0) FROM gastos WHERE fecha = ?", (hoy_str,)).fetchone()[0]

    conn.close()

    contexto = dict(
        vista="cierre",
        hoy=hoy_str,
        total_cobrado_hoy=total_cobrado_hoy,
        total_gastos_hoy=gastos_hoy,
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template_string(CONTENIDO_HTML, **contexto)
    return render_template_string(HTML_TEMPLATE, **contexto)


@app.route("/resumen")
def resumen():
    clientes = obtener_clientes_completos()
    total_capital = sum(c["monto"] for c in clientes)
    total_creditos = sum(c["monto_total"] for c in clientes)
    total_cobrado = sum(p["valor_pagado"] for c in clientes for p in c["pagos"])

    contexto = dict(
        vista="resumen",
        clientes=clientes,
        total_capital=total_capital,
        total_creditos=total_creditos,
        total_cobrado=total_cobrado,
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template_string(CONTENIDO_HTML, **contexto)
    return render_template_string(HTML_TEMPLATE, **contexto)


@app.route("/respaldo")
def respaldo():
    if os.path.exists(DB_NAME):
        return send_file(DB_NAME, as_attachment=True)
    return redirect("/resumen")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)