## ./__init__.py
```py
from . import models
```

## ./__manifest__.py
```py
{
    'name': 'Stone Workshop',
    'version': '19.0.1.0.0',
    'category': 'Manufacturing',
    'summary': 'Taller de piedra - Procesos de acabado y corte de placas',
    'description': 'Módulo especializado para talleres de piedra natural. '
                   'Gestión de procesos de acabado y corte con interfaz visual simplificada.',
    'author': 'Alphaqueb Consulting',
    'website': 'https://www.alphaqueb.com',
    'license': 'LGPL-3',
    'depends': [
        'mrp',
        'stock',
        'product',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'views/workshop_order_views.xml',
        'views/workshop_process_views.xml',
        'views/workshop_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'stone_workshop/static/src/css/workshop.css',
            'stone_workshop/static/src/js/workshop_kanban.js',
            'stone_workshop/static/src/xml/workshop_templates.xml',
        ],
    },
    'installable': True,
    'application': False,
}
```

## ./data/sequence_data.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <data noupdate="1">
        <record id="seq_workshop_order" model="ir.sequence">
            <field name="name">Workshop Order</field>
            <field name="code">workshop.order</field>
            <field name="prefix">WS/%(year)s/</field>
            <field name="padding">4</field>
        </record>
    </data>
</odoo>
```

## ./models/__init__.py
```py
from . import workshop_process
from . import workshop_order
```

## ./models/workshop_order.py
```py
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class WorkshopOrder(models.Model):
    _name = 'workshop.order'
    _description = 'Orden de Taller'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Referencia', readonly=True, default='Nuevo', copy=False)
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('confirmed', 'Confirmada'),
        ('in_progress', 'En Proceso'),
        ('done', 'Terminada'),
        ('cancel', 'Cancelada'),
    ], string='Estado', default='draft', tracking=True)

    process_id = fields.Many2one('workshop.process', string='Proceso', required=True,
                                  states={'done': [('readonly', True)]})
    process_type = fields.Selection(related='process_id.process_type', store=True)

    # Producto y lote de entrada
    product_in_id = fields.Many2one('product.product', string='Producto Entrada', required=True,
                                     domain=[('tracking', '=', 'lot')])
    lot_in_id = fields.Many2one('stock.lot', string='Lote Entrada', required=True)
    qty_in = fields.Float(string='Cantidad Entrada', digits=(12, 4))

    # Producto y lote de salida
    product_out_id = fields.Many2one('product.product', string='Producto Salida', required=True)
    lot_out_name = fields.Char(string='Lote Salida', compute='_compute_lot_out_name', store=True)
    qty_out = fields.Float(string='Cantidad Salida', digits=(12, 4))

    # Para cortes / formatos - Char para permitir valores como "LL"
    format_width = fields.Char(string='Ancho (cm)')
    format_height = fields.Char(string='Alto (cm)')
    format_qty = fields.Integer(string='Piezas')

    # Costos
    area_sqm = fields.Float(string='Área m²', compute='_compute_area', store=True, digits=(12, 4))
    process_cost = fields.Float(string='Costo Proceso', compute='_compute_costs', store=True, digits=(12, 2))
    labor_cost = fields.Float(string='Costo M.O.', digits=(12, 2))
    total_cost = fields.Float(string='Costo Total', compute='_compute_costs', store=True, digits=(12, 2))

    notes = fields.Html(string='Notas')
    production_id = fields.Many2one('mrp.production', string='Orden Producción', readonly=True, copy=False)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    user_id = fields.Many2one('res.users', string='Responsable', default=lambda self: self.env.user)
    date_planned = fields.Datetime(string='Fecha Planeada')
    date_done = fields.Datetime(string='Fecha Terminado', readonly=True)

    def _parse_dimension(self, val):
        """Intenta parsear una dimensión a float. Retorna 0 si no es numérico."""
        if not val:
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    @api.depends('lot_in_id', 'process_id')
    def _compute_lot_out_name(self):
        for rec in self:
            if rec.lot_in_id and rec.process_id:
                rec.lot_out_name = f"{rec.lot_in_id.name}-{rec.process_id.code}"
            else:
                rec.lot_out_name = False

    @api.depends('process_type', 'qty_in', 'format_width', 'format_height', 'format_qty')
    def _compute_area(self):
        for rec in self:
            if rec.process_type == 'cut':
                w = rec._parse_dimension(rec.format_width)
                h = rec._parse_dimension(rec.format_height)
                qty = rec.format_qty or 0
                if w and h and qty:
                    rec.area_sqm = (w / 100) * (h / 100) * qty
                else:
                    rec.area_sqm = 0
            elif rec.qty_in:
                rec.area_sqm = rec.qty_in
            else:
                rec.area_sqm = 0

    @api.depends('area_sqm', 'process_id.cost_per_sqm', 'labor_cost')
    def _compute_costs(self):
        for rec in self:
            rec.process_cost = rec.area_sqm * (rec.process_id.cost_per_sqm or 0)
            rec.total_cost = rec.process_cost + (rec.labor_cost or 0)

    @api.onchange('lot_in_id')
    def _onchange_lot_in(self):
        if self.lot_in_id:
            quants = self.env['stock.quant'].search([
                ('lot_id', '=', self.lot_in_id.id),
                ('location_id.usage', '=', 'internal'),
            ])
            self.qty_in = sum(quants.mapped('quantity'))
            self.qty_out = self.qty_in

    @api.onchange('qty_in')
    def _onchange_qty_in(self):
        if self.qty_in and not self.qty_out:
            self.qty_out = self.qty_in

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'Nuevo') == 'Nuevo':
                vals['name'] = self.env['ir.sequence'].next_by_code('workshop.order') or 'Nuevo'
        return super().create(vals_list)

    def action_confirm(self):
        for rec in self:
            if not rec.product_in_id or not rec.lot_in_id:
                raise UserError(_('Debe seleccionar producto y lote de entrada.'))
            if not rec.product_out_id:
                raise UserError(_('Debe seleccionar producto de salida.'))
            rec.state = 'confirmed'

    def action_start(self):
        self.write({'state': 'in_progress'})

    def action_done(self):
        for rec in self:
            rec._create_production()
            rec.write({
                'state': 'done',
                'date_done': fields.Datetime.now(),
            })

    def action_cancel(self):
        self.write({'state': 'cancel'})

    def action_draft(self):
        self.write({'state': 'draft'})

    def _create_production(self):
        """Crea la orden de producción MRP vinculada."""
        self.ensure_one()
        bom = self.env['mrp.bom'].search([
            ('product_tmpl_id', '=', self.product_out_id.product_tmpl_id.id),
        ], limit=1)
        if not bom:
            bom = self.env['mrp.bom'].create({
                'product_tmpl_id': self.product_out_id.product_tmpl_id.id,
                'product_qty': 1,
                'type': 'normal',
                'bom_line_ids': [(0, 0, {
                    'product_id': self.product_in_id.id,
                    'product_qty': 1,
                })],
            })

        qty = self.qty_out or self.qty_in or 1
        lot_out = self.env['stock.lot'].create({
            'name': self.lot_out_name,
            'product_id': self.product_out_id.id,
            'company_id': self.company_id.id,
        })

        production = self.env['mrp.production'].create({
            'product_id': self.product_out_id.id,
            'product_qty': qty,
            'bom_id': bom.id,
            'lot_producing_id': lot_out.id,
            'company_id': self.company_id.id,
        })
        self.production_id = production


class WorkshopOrderLine(models.Model):
    """Líneas para cuando el proceso genera múltiples formatos."""
    _name = 'workshop.order.line'
    _description = 'Línea de Orden de Taller'

    order_id = fields.Many2one('workshop.order', ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Formato')
    width = fields.Char(string='Ancho (cm)')
    height = fields.Char(string='Alto (cm)')
    qty = fields.Integer(string='Piezas', default=1)
    area_sqm = fields.Float(string='Área m²', compute='_compute_area', store=True, digits=(12, 4))

    @api.depends('width', 'height', 'qty')
    def _compute_area(self):
        for line in self:
            try:
                w = float(line.width) if line.width else 0
                h = float(line.height) if line.height else 0
                line.area_sqm = (w / 100) * (h / 100) * (line.qty or 0) if w and h else 0
            except (ValueError, TypeError):
                line.area_sqm = 0```

## ./models/workshop_process.py
```py
from odoo import models, fields, api


class WorkshopProcess(models.Model):
    _name = 'workshop.process'
    _description = 'Tipo de proceso de taller'
    _order = 'sequence, name'

    name = fields.Char(string='Nombre', required=True)
    code = fields.Char(string='Código', required=True, help='Código corto, ej: ACB, CRT')
    sequence = fields.Integer(default=10)
    process_type = fields.Selection([
        ('finish', 'Acabado'),
        ('cut', 'Corte / Formato'),
        ('other', 'Otro'),
    ], string='Tipo', required=True, default='finish')
    active = fields.Boolean(default=True)
    description = fields.Text(string='Descripción')
    cost_per_sqm = fields.Float(string='Costo por m²', digits=(12, 2))
    labor_cost = fields.Float(string='Costo mano de obra', digits=(12, 2))
    color = fields.Integer(string='Color', default=0)

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'El código del proceso debe ser único.'),
    ]
```

## ./static/src/js/workshop_kanban.js
```js
/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const STATE_LABELS = {
    draft: 'Borrador',
    confirmed: 'Confirmada',
    in_progress: 'En Proceso',
    done: 'Terminada',
    cancel: 'Cancelada',
};

class WorkshopDashboard extends Component {
    static template = "stone_workshop.Dashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            processes: [],
            products: [],
            lots: [],
            selectedProcess: null,
            selectedLot: null,
            form: this._emptyForm(),
            cart: this._loadCart(),
            recentOrders: [],
            stats: { in_progress: 0, done: 0, finish: 0, cut: 0 },
        });

        onWillStart(async () => {
            await this.loadProcesses();
            await this.loadProducts();
            await this.loadOrders();
            await this.loadStats();
        });
    }

    get cartTotal() {
        return this.state.cart.reduce((sum, item) => sum + (item.total_cost || 0), 0);
    }

    _emptyForm() {
        return {
            product_in_id: false,
            product_in_name: '',
            lot_in_id: false,
            lot_in_name: '',
            qty_in: 0,
            product_out_id: false,
            product_out_name: '',
            qty_out: 0,
            lot_out_name: '',
            format_width: '',
            format_height: '',
            format_qty: 1,
            labor_cost: 0,
            area_sqm: 0,
            process_cost: 0,
            total_cost: 0,
        };
    }

    _loadCart() {
        try {
            const saved = localStorage.getItem('ws_cart');
            return saved ? JSON.parse(saved) : [];
        } catch {
            return [];
        }
    }

    _saveCart() {
        try {
            localStorage.setItem('ws_cart', JSON.stringify(this.state.cart));
        } catch { /* ignore */ }
    }

    async loadProcesses() {
        this.state.processes = await this.orm.searchRead(
            "workshop.process",
            [["active", "=", true]],
            ["name", "code", "process_type", "cost_per_sqm", "labor_cost"],
            { order: "sequence, name" }
        );
    }

    async loadProducts() {
        this.state.products = await this.orm.searchRead(
            "product.product",
            [["tracking", "=", "lot"], ["type", "=", "product"]],
            ["name", "display_name"],
            { order: "name", limit: 200 }
        );
    }

    async loadOrders() {
        const orders = await this.orm.searchRead(
            "workshop.order",
            [],
            ["name", "process_id", "product_in_id", "lot_in_id", "product_out_id",
             "lot_out_name", "total_cost", "state"],
            { order: "create_date desc", limit: 20 }
        );
        this.state.recentOrders = orders.map(o => ({
            ...o,
            state_label: STATE_LABELS[o.state] || o.state,
        }));
    }

    async loadStats() {
        const all = await this.orm.searchRead(
            "workshop.order",
            [["state", "not in", ["cancel"]]],
            ["state", "process_type"],
        );
        this.state.stats = {
            in_progress: all.filter(o => o.state === 'in_progress').length,
            done: all.filter(o => o.state === 'done').length,
            finish: all.filter(o => o.process_type === 'finish').length,
            cut: all.filter(o => o.process_type === 'cut').length,
        };
    }

    selectProcess(proc) {
        this.state.selectedProcess = proc;
        this.state.selectedLot = null;
        this.state.lots = [];
        Object.assign(this.state.form, this._emptyForm());
    }

    async onProductInChange(ev) {
        const productId = parseInt(ev.target.value) || false;
        this.state.form.product_in_id = productId;
        const prod = this.state.products.find(p => p.id === productId);
        this.state.form.product_in_name = prod ? prod.display_name : '';
        this.state.selectedLot = null;

        if (productId) {
            const quants = await this.orm.searchRead(
                "stock.quant",
                [["product_id", "=", productId], ["location_id.usage", "=", "internal"], ["quantity", ">", 0]],
                ["lot_id", "quantity"],
            );
            const lotMap = {};
            for (const q of quants) {
                if (q.lot_id) {
                    const lid = q.lot_id[0];
                    if (!lotMap[lid]) {
                        lotMap[lid] = { id: lid, name: q.lot_id[1], qty: 0 };
                    }
                    lotMap[lid].qty += q.quantity;
                }
            }
            this.state.lots = Object.values(lotMap);
        } else {
            this.state.lots = [];
        }
    }

    onLotChange(ev) {
        const lotId = parseInt(ev.target.value) || false;
        const lot = this.state.lots.find(l => l.id === lotId);
        this.state.selectedLot = lot || null;
        if (lot) {
            this.state.form.lot_in_id = lot.id;
            this.state.form.lot_in_name = lot.name;
            this.state.form.qty_in = lot.qty;
            // Auto-set qty_out = qty_in
            this.state.form.qty_out = lot.qty;
            this._updateLotOutName();
            this._recalcCosts();
        }
    }

    onProductOutChange(ev) {
        const productId = parseInt(ev.target.value) || false;
        this.state.form.product_out_id = productId;
        const prod = this.state.products.find(p => p.id === productId);
        this.state.form.product_out_name = prod ? prod.display_name : '';
        this._recalcCosts();
    }

    updateFormNum(field, value) {
        const num = parseFloat(value) || 0;
        this.state.form[field] = num;
        this._recalcCosts();
    }

    updateFormText(field, value) {
        this.state.form[field] = value;
        this._recalcCosts();
    }

    _updateLotOutName() {
        const f = this.state.form;
        const proc = this.state.selectedProcess;
        if (f.lot_in_name && proc) {
            f.lot_out_name = `${f.lot_in_name}-${proc.code}`;
        }
    }

    _parseDimension(val) {
        if (!val) return 0;
        const num = parseFloat(val);
        return isNaN(num) ? 0 : num;
    }

    _recalcCosts() {
        const f = this.state.form;
        const proc = this.state.selectedProcess;
        if (!proc) return;

        if (proc.process_type === 'cut') {
            const w = this._parseDimension(f.format_width);
            const h = this._parseDimension(f.format_height);
            const qty = parseInt(f.format_qty) || 0;
            if (w && h && qty) {
                f.area_sqm = (w / 100) * (h / 100) * qty;
            } else {
                f.area_sqm = 0;
            }
        } else {
            f.area_sqm = f.qty_in || 0;
        }

        f.process_cost = f.area_sqm * (proc.cost_per_sqm || 0);
        f.total_cost = f.process_cost + (parseFloat(f.labor_cost) || 0);
    }

    addToCart() {
        const f = this.state.form;
        const proc = this.state.selectedProcess;
        if (!f.product_in_id || !f.lot_in_id || !f.product_out_id || !proc) {
            this.notification.add("Completa todos los campos antes de agregar.", { type: "warning" });
            return;
        }

        const item = {
            key: Date.now() + '_' + Math.random().toString(36).substr(2, 5),
            process_id: proc.id,
            process_name: proc.name,
            process_type: proc.process_type,
            process_code: proc.code,
            product_in_id: f.product_in_id,
            product_in_name: f.product_in_name,
            lot_in_id: f.lot_in_id,
            lot_in_name: f.lot_in_name,
            qty_in: f.qty_in,
            qty_out: f.qty_out,
            product_out_id: f.product_out_id,
            product_out_name: f.product_out_name,
            lot_out_name: f.lot_out_name,
            format_width: f.format_width,
            format_height: f.format_height,
            format_qty: f.format_qty,
            labor_cost: parseFloat(f.labor_cost) || 0,
            area_sqm: f.area_sqm,
            process_cost: f.process_cost,
            total_cost: f.total_cost,
        };

        this.state.cart.push(item);
        this._saveCart();
        this.notification.add(`${proc.name} agregado al carrito`, { type: "success" });

        Object.assign(this.state.form, this._emptyForm());
        this.state.selectedLot = null;
        this.state.lots = [];
    }

    removeFromCart(key) {
        this.state.cart = this.state.cart.filter(i => i.key !== key);
        this._saveCart();
    }

    clearCart() {
        this.state.cart = [];
        this._saveCart();
    }

    async processCart() {
        if (!this.state.cart.length) return;

        try {
            for (const item of this.state.cart) {
                await this.orm.create("workshop.order", [{
                    process_id: item.process_id,
                    product_in_id: item.product_in_id,
                    lot_in_id: item.lot_in_id,
                    qty_in: item.qty_in,
                    product_out_id: item.product_out_id,
                    qty_out: item.qty_out,
                    format_width: item.format_width || '',
                    format_height: item.format_height || '',
                    format_qty: item.format_qty || 0,
                    labor_cost: item.labor_cost || 0,
                }]);
            }
            this.notification.add(
                `${this.state.cart.length} orden(es) creada(s) exitosamente`,
                { type: "success" }
            );
            this.clearCart();
            await this.loadOrders();
            await this.loadStats();
        } catch (e) {
            this.notification.add(`Error al crear órdenes: ${e.message}`, { type: "danger" });
        }
    }

    openOrder(orderId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "workshop.order",
            res_id: orderId,
            views: [[false, "form"]],
        });
    }

    openOdooForm() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "workshop.order",
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("workshop_dashboard", WorkshopDashboard);```

## ./static/src/xml/workshop_templates.xml
```xml
<?xml version="1.0" encoding="UTF-8"?>
<templates xml:space="preserve">

    <t t-name="stone_workshop.Dashboard">
        <div class="ws-dashboard">
            <!-- Header -->
            <div class="ws-header">
                <div class="ws-header-left">
                    <h1>🪨 Taller de Piedra</h1>
                    <p>Panel de producción — Gestión de acabados y cortes</p>
                </div>
                <div style="display:flex;gap:10px;">
                    <button class="ws-btn ws-btn-secondary" t-on-click="loadOrders">
                        ↻ Actualizar
                    </button>
                    <button class="ws-btn ws-btn-primary" t-on-click="openOdooForm">
                        + Nueva Orden
                    </button>
                </div>
            </div>

            <!-- Stats -->
            <div class="ws-stats">
                <div class="ws-stat-card" data-type="progress">
                    <div class="ws-stat-label">En Proceso</div>
                    <div class="ws-stat-value"><t t-esc="state.stats.in_progress"/></div>
                </div>
                <div class="ws-stat-card" data-type="done">
                    <div class="ws-stat-label">Terminadas</div>
                    <div class="ws-stat-value"><t t-esc="state.stats.done"/></div>
                </div>
                <div class="ws-stat-card" data-type="finish">
                    <div class="ws-stat-label">Acabados</div>
                    <div class="ws-stat-value"><t t-esc="state.stats.finish"/></div>
                </div>
                <div class="ws-stat-card" data-type="cut">
                    <div class="ws-stat-label">Cortes</div>
                    <div class="ws-stat-value"><t t-esc="state.stats.cut"/></div>
                </div>
            </div>

            <!-- Process Selection -->
            <h3 class="ws-section-title">Selecciona un proceso</h3>
            <div class="ws-process-grid">
                <t t-foreach="state.processes" t-as="proc" t-key="proc.id">
                    <div class="ws-process-card"
                         t-att-data-type="proc.process_type"
                         t-att-class="{'selected': state.selectedProcess and state.selectedProcess.id === proc.id}"
                         t-on-click="() => this.selectProcess(proc)">
                        <div class="ws-process-icon">
                            <t t-if="proc.process_type === 'finish'">✦</t>
                            <t t-elif="proc.process_type === 'cut'">◫</t>
                            <t t-else="">⚙</t>
                        </div>
                        <div class="ws-process-name"><t t-esc="proc.name"/></div>
                        <div class="ws-process-code"><t t-esc="proc.code"/></div>
                    </div>
                </t>
            </div>

            <!-- Main Area: Form + Cart -->
            <div class="ws-main">
                <!-- Form -->
                <div class="ws-form-panel ws-animate" t-if="state.selectedProcess">
                    <div class="ws-form-title">
                        Configurar Orden —
                        <span style="color:var(--ws-primary)"><t t-esc="state.selectedProcess.name"/></span>
                    </div>

                    <!-- Producto entrada -->
                    <div class="ws-form-group">
                        <label>Producto de Entrada</label>
                        <select t-on-change="onProductInChange" t-ref="productInSelect">
                            <option value="">— Seleccionar producto —</option>
                            <t t-foreach="state.products" t-as="p" t-key="p.id">
                                <option t-att-value="p.id"><t t-esc="p.name"/></option>
                            </t>
                        </select>
                    </div>

                    <!-- Lote -->
                    <div class="ws-form-group" t-if="state.lots.length">
                        <label>Lote / Placa</label>
                        <select t-on-change="onLotChange" t-ref="lotSelect">
                            <option value="">— Seleccionar lote —</option>
                            <t t-foreach="state.lots" t-as="lot" t-key="lot.id">
                                <option t-att-value="lot.id">
                                    <t t-esc="lot.name"/> — <t t-esc="lot.qty"/> uds
                                </option>
                            </t>
                        </select>
                    </div>

                    <!-- Cantidades entrada/salida -->
                    <div class="ws-form-row" t-if="state.selectedLot">
                        <div class="ws-form-group">
                            <label>Cantidad Entrada</label>
                            <input type="number" step="0.01"
                                   t-att-value="state.form.qty_in"
                                   t-on-input="(ev) => this.updateFormNum('qty_in', ev.target.value)"/>
                        </div>
                        <div class="ws-form-group">
                            <label>Cantidad Salida</label>
                            <input type="number" step="0.01"
                                   t-att-value="state.form.qty_out"
                                   t-on-input="(ev) => this.updateFormNum('qty_out', ev.target.value)"/>
                        </div>
                    </div>

                    <!-- Lote salida auto -->
                    <div class="ws-form-group" t-if="state.selectedLot">
                        <label>Lote salida (auto)</label>
                        <input type="text" readonly="1" t-att-value="state.form.lot_out_name"
                               style="background:#FAF8F5;"/>
                    </div>

                    <!-- Producto salida -->
                    <div class="ws-form-group" t-if="state.selectedLot">
                        <label>Producto de Salida</label>
                        <select t-on-change="onProductOutChange" t-ref="productOutSelect">
                            <option value="">— Seleccionar producto —</option>
                            <t t-foreach="state.products" t-as="p" t-key="p.id">
                                <option t-att-value="p.id"><t t-esc="p.name"/></option>
                            </t>
                        </select>
                    </div>

                    <!-- Dimensiones formato (SOLO corte) -->
                    <div t-if="state.selectedProcess.process_type === 'cut' and state.selectedLot">
                        <div class="ws-form-divider"/>
                        <h4 style="font-size:14px;font-weight:700;margin-bottom:14px;">Formato de Corte</h4>
                        <div class="ws-form-row-3">
                            <div class="ws-form-group">
                                <label>Ancho (cm)</label>
                                <input type="text" placeholder="ej: 60, LL"
                                       t-att-value="state.form.format_width"
                                       t-on-input="(ev) => this.updateFormText('format_width', ev.target.value)"/>
                            </div>
                            <div class="ws-form-group">
                                <label>Alto (cm)</label>
                                <input type="text" placeholder="ej: 40, LL"
                                       t-att-value="state.form.format_height"
                                       t-on-input="(ev) => this.updateFormText('format_height', ev.target.value)"/>
                            </div>
                            <div class="ws-form-group">
                                <label>Piezas</label>
                                <input type="number" step="1" min="1"
                                       t-att-value="state.form.format_qty"
                                       t-on-input="(ev) => this.updateFormNum('format_qty', ev.target.value)"/>
                            </div>
                        </div>
                    </div>

                    <!-- Costos -->
                    <div t-if="state.selectedLot">
                        <div class="ws-form-divider"/>
                        <div class="ws-form-row">
                            <div class="ws-form-group">
                                <label>Costo M.O.</label>
                                <input type="number" step="0.01"
                                       t-att-value="state.form.labor_cost"
                                       t-on-input="(ev) => this.updateFormNum('labor_cost', ev.target.value)"/>
                            </div>
                            <div class="ws-form-group">
                                <label>Área estimada (m²)</label>
                                <input type="text" readonly="1"
                                       t-att-value="state.form.area_sqm.toFixed(4)"
                                       style="background:#FAF8F5;"/>
                            </div>
                        </div>
                    </div>

                    <!-- Preview -->
                    <div class="ws-preview" t-if="state.form.product_out_id">
                        <div class="ws-preview-title">Vista previa</div>
                        <div class="ws-preview-row">
                            <span>Lote salida</span>
                            <span><t t-esc="state.form.lot_out_name"/></span>
                        </div>
                        <div class="ws-preview-row">
                            <span>Cant. entrada</span>
                            <span><t t-esc="state.form.qty_in"/></span>
                        </div>
                        <div class="ws-preview-row">
                            <span>Cant. salida</span>
                            <span><t t-esc="state.form.qty_out"/></span>
                        </div>
                        <div class="ws-preview-row" t-if="state.selectedProcess.process_type === 'cut' and state.form.format_width">
                            <span>Formato</span>
                            <span><t t-esc="state.form.format_width"/> x <t t-esc="state.form.format_height"/> — <t t-esc="state.form.format_qty"/> pzas</span>
                        </div>
                        <div class="ws-preview-row">
                            <span>Costo proceso</span>
                            <span>$<t t-esc="state.form.process_cost.toFixed(2)"/></span>
                        </div>
                        <div class="ws-preview-row">
                            <span>M.O.</span>
                            <span>$<t t-esc="(parseFloat(state.form.labor_cost) || 0).toFixed(2)"/></span>
                        </div>
                        <div class="ws-preview-row" style="font-weight:700;font-size:16px;border-top:1px solid rgba(184,134,11,0.2);padding-top:10px;">
                            <span>Total</span>
                            <span style="color:var(--ws-primary)">$<t t-esc="state.form.total_cost.toFixed(2)"/></span>
                        </div>
                    </div>

                    <!-- Add to cart -->
                    <button class="ws-btn ws-btn-primary" style="width:100%;justify-content:center;margin-top:20px;"
                            t-on-click="addToCart"
                            t-att-disabled="!state.form.product_out_id">
                        + Agregar al carrito
                    </button>
                </div>

                <!-- Empty form -->
                <div class="ws-form-panel" t-if="!state.selectedProcess">
                    <div class="ws-empty">
                        <div class="ws-empty-icon">🪨</div>
                        <div class="ws-empty-text">Selecciona un proceso para comenzar</div>
                    </div>
                </div>

                <!-- Cart -->
                <div class="ws-cart">
                    <div class="ws-cart-header">
                        <h3>🛒 Carrito</h3>
                        <span class="ws-cart-badge"><t t-esc="state.cart.length"/></span>
                    </div>

                    <div class="ws-cart-items" t-if="state.cart.length">
                        <t t-foreach="state.cart" t-as="item" t-key="item.key">
                            <div class="ws-cart-item" t-att-data-type="item.process_type">
                                <div class="ws-cart-item-header">
                                    <span class="ws-cart-item-process"><t t-esc="item.process_name"/></span>
                                    <button class="ws-cart-item-remove" t-on-click="() => this.removeFromCart(item.key)">✕</button>
                                </div>
                                <div class="ws-cart-item-product"><t t-esc="item.product_in_name"/></div>
                                <div class="ws-cart-item-lot">Lote: <t t-esc="item.lot_in_name"/> (<t t-esc="item.qty_in"/> → <t t-esc="item.qty_out"/>)</div>
                                <div class="ws-cart-item-arrow">
                                    <span class="arrow">→</span>
                                    <span><t t-esc="item.product_out_name"/></span>
                                </div>
                                <div class="ws-cart-item-lot">Lote salida: <t t-esc="item.lot_out_name"/></div>
                                <div class="ws-cart-item-format" t-if="item.process_type === 'cut' and item.format_width">
                                    Formato: <t t-esc="item.format_width"/> x <t t-esc="item.format_height"/> — <t t-esc="item.format_qty"/> pzas
                                </div>
                                <div class="ws-cart-item-cost">$<t t-esc="item.total_cost.toFixed(2)"/></div>
                            </div>
                        </t>
                    </div>

                    <div class="ws-empty" t-if="!state.cart.length" style="padding:30px 10px;">
                        <div class="ws-empty-icon">📋</div>
                        <div class="ws-empty-text">Sin órdenes en el carrito</div>
                    </div>

                    <div class="ws-cart-footer" t-if="state.cart.length">
                        <div class="ws-cart-total">
                            <span class="ws-cart-total-label">Costo Total</span>
                            <span class="ws-cart-total-value">$<t t-esc="cartTotal.toFixed(2)"/></span>
                        </div>
                        <div class="ws-cart-actions">
                            <button class="ws-btn ws-btn-primary" t-on-click="processCart">
                                ✓ Crear Órdenes (<t t-esc="state.cart.length"/>)
                            </button>
                            <button class="ws-btn ws-btn-secondary" t-on-click="clearCart">
                                Limpiar carrito
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Recent Orders -->
            <div class="ws-recent">
                <h3 class="ws-section-title">Órdenes Recientes</h3>
                <div class="ws-table" t-if="state.recentOrders.length">
                    <table>
                        <thead>
                            <tr>
                                <th>Ref</th>
                                <th>Proceso</th>
                                <th>Entrada</th>
                                <th>Lote</th>
                                <th>Salida</th>
                                <th>Lote Salida</th>
                                <th>Costo</th>
                                <th>Estado</th>
                            </tr>
                        </thead>
                        <tbody>
                            <t t-foreach="state.recentOrders" t-as="order" t-key="order.id">
                                <tr style="cursor:pointer;" t-on-click="() => this.openOrder(order.id)">
                                    <td style="font-weight:600"><t t-esc="order.name"/></td>
                                    <td><t t-esc="order.process_id[1]"/></td>
                                    <td><t t-esc="order.product_in_id[1]"/></td>
                                    <td><t t-esc="order.lot_in_id and order.lot_in_id[1]"/></td>
                                    <td><t t-esc="order.product_out_id[1]"/></td>
                                    <td><t t-esc="order.lot_out_name"/></td>
                                    <td style="font-weight:600">$<t t-esc="order.total_cost.toFixed(2)"/></td>
                                    <td>
                                        <span t-att-class="'ws-badge ws-badge-' + (order.state === 'in_progress' ? 'progress' : order.state)">
                                            <t t-esc="order.state_label"/>
                                        </span>
                                    </td>
                                </tr>
                            </t>
                        </tbody>
                    </table>
                </div>
                <div class="ws-empty" t-if="!state.recentOrders.length">
                    <div class="ws-empty-text">No hay órdenes recientes</div>
                </div>
            </div>
        </div>
    </t>

</templates>```

## ./views/workshop_menus.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <!-- Menú principal bajo Manufactura -->
    <menuitem id="menu_workshop_root"
              name="Taller de Piedra"
              parent="mrp.menu_mrp_root"
              sequence="50"/>

    <menuitem id="menu_workshop_dashboard"
              name="Panel de Taller"
              parent="menu_workshop_root"
              action="action_workshop_dashboard"
              sequence="5"/>

    <menuitem id="menu_workshop_orders"
              name="Órdenes de Taller"
              parent="menu_workshop_root"
              action="action_workshop_order"
              sequence="10"/>

    <!-- Configuración -->
    <menuitem id="menu_workshop_config"
              name="Configuración"
              parent="menu_workshop_root"
              sequence="90"/>

    <menuitem id="menu_workshop_process"
              name="Procesos"
              parent="menu_workshop_config"
              action="action_workshop_process"
              sequence="10"/>
</odoo>
```

## ./views/workshop_order_views.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
<data>
    <!-- Formulario Orden -->
    <record id="view_workshop_order_form" model="ir.ui.view">
        <field name="name">workshop.order.form</field>
        <field name="model">workshop.order</field>
        <field name="arch" type="xml">
            <form string="Orden de Taller">
                <header>
                    <button name="action_confirm" string="Confirmar" type="object"
                            class="btn-primary" invisible="state != 'draft'"/>
                    <button name="action_start" string="Iniciar" type="object"
                            class="btn-primary" invisible="state != 'confirmed'"/>
                    <button name="action_done" string="Terminar" type="object"
                            class="btn-success" invisible="state != 'in_progress'"/>
                    <button name="action_cancel" string="Cancelar" type="object"
                            invisible="state in ('done', 'cancel')"/>
                    <button name="action_draft" string="A Borrador" type="object"
                            invisible="state != 'cancel'"/>
                    <field name="state" widget="statusbar"
                           statusbar_visible="draft,confirmed,in_progress,done"/>
                </header>
                <sheet>
                    <div class="oe_title">
                        <h1><field name="name"/></h1>
                    </div>
                    <group>
                        <group string="Proceso">
                            <field name="process_id"/>
                            <field name="process_type" invisible="1"/>
                            <field name="user_id"/>
                            <field name="date_planned"/>
                        </group>
                        <group string="Entrada">
                            <field name="product_in_id"/>
                            <field name="lot_in_id" domain="[('product_id', '=', product_in_id)]"/>
                            <field name="qty_in"/>
                        </group>
                    </group>
                    <group>
                        <group string="Salida">
                            <field name="product_out_id"/>
                            <field name="lot_out_name"/>
                            <field name="qty_out"/>
                        </group>
                        <group string="Formato" invisible="process_type != 'cut'">
                            <field name="format_width"/>
                            <field name="format_height"/>
                            <field name="format_qty"/>
                        </group>
                    </group>
                    <notebook>
                        <page string="Costos">
                            <group>
                                <group>
                                    <field name="area_sqm"/>
                                    <field name="process_cost"/>
                                </group>
                                <group>
                                    <field name="labor_cost"/>
                                    <field name="total_cost"/>
                                </group>
                            </group>
                        </page>
                        <page string="Notas">
                            <field name="notes"/>
                        </page>
                        <page string="Producción" invisible="not production_id">
                            <group>
                                <field name="production_id"/>
                                <field name="date_done"/>
                            </group>
                        </page>
                    </notebook>
                </sheet>
                <chatter/>
            </form>
        </field>
    </record>

    <!-- Lista Órdenes -->
    <record id="view_workshop_order_list" model="ir.ui.view">
        <field name="name">workshop.order.list</field>
        <field name="model">workshop.order</field>
        <field name="arch" type="xml">
            <list string="Órdenes de Taller" decoration-info="state == 'draft'"
                  decoration-warning="state == 'in_progress'"
                  decoration-success="state == 'done'"
                  decoration-muted="state == 'cancel'">
                <field name="name"/>
                <field name="process_id"/>
                <field name="product_in_id"/>
                <field name="lot_in_id"/>
                <field name="product_out_id"/>
                <field name="lot_out_name"/>
                <field name="total_cost" sum="Total"/>
                <field name="state" widget="badge"
                       decoration-info="state == 'draft'"
                       decoration-warning="state == 'in_progress'"
                       decoration-success="state == 'done'"/>
            </list>
        </field>
    </record>

    <!-- Kanban Órdenes -->
    <record id="view_workshop_order_kanban" model="ir.ui.view">
        <field name="name">workshop.order.kanban</field>
        <field name="model">workshop.order</field>
        <field name="arch" type="xml">
            <kanban default_group_by="state" class="o_workshop_kanban">
                <field name="name"/>
                <field name="state"/>
                <field name="process_id"/>
                <field name="product_in_id"/>
                <field name="lot_in_id"/>
                <field name="product_out_id"/>
                <field name="lot_out_name"/>
                <field name="total_cost"/>
                <field name="process_type"/>
                <field name="user_id"/>
                <templates>
                    <t t-name="card" class="flex-row">
                        <aside>
                            <field name="user_id" widget="many2one_avatar_user"/>
                        </aside>
                        <main>
                            <field name="name" class="fw-bold fs-5"/>
                            <field name="process_id" class="text-primary"/>
                            <div class="d-flex gap-2 mt-1">
                                <span class="badge text-bg-light">
                                    <field name="product_in_id"/>
                                </span>
                                <span class="text-muted">→</span>
                                <span class="badge text-bg-light">
                                    <field name="product_out_id"/>
                                </span>
                            </div>
                            <div class="mt-1">
                                <span class="text-muted">Lote: </span>
                                <field name="lot_in_id"/>
                                <span class="text-muted"> → </span>
                                <field name="lot_out_name"/>
                            </div>
                            <footer class="pt-2">
                                <field name="total_cost" widget="monetary"/>
                            </footer>
                        </main>
                    </t>
                </templates>
            </kanban>
        </field>
    </record>

    <!-- Search -->
    <record id="view_workshop_order_search" model="ir.ui.view">
        <field name="name">workshop.order.search</field>
        <field name="model">workshop.order</field>
        <field name="arch" type="xml">
            <search string="Buscar Órdenes">
                <field name="name"/>
                <field name="process_id"/>
                <field name="product_in_id"/>
                <field name="lot_in_id"/>
                <field name="product_out_id"/>
                <searchpanel>
                    <field name="state" icon="fa-tasks" enable_counters="1"/>
                    <field name="process_type" icon="fa-cogs" enable_counters="1"/>
                </searchpanel>
            </search>
        </field>
    </record>

    <!-- Acción principal -->
    <record id="action_workshop_order" model="ir.actions.act_window">
        <field name="name">Órdenes de Taller</field>
        <field name="res_model">workshop.order</field>
        <field name="view_mode">kanban,list,form</field>
        <field name="search_view_id" ref="view_workshop_order_search"/>
    </record>

    <!-- Acción para vista personalizada JS -->
    <record id="action_workshop_dashboard" model="ir.actions.client">
        <field name="name">Panel de Taller</field>
        <field name="tag">workshop_dashboard</field>
    </record>
</data>
</odoo>```

## ./views/workshop_process_views.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <!-- Formulario Proceso -->
    <record id="view_workshop_process_form" model="ir.ui.view">
        <field name="name">workshop.process.form</field>
        <field name="model">workshop.process</field>
        <field name="arch" type="xml">
            <form string="Proceso de Taller">
                <sheet>
                    <div class="oe_title">
                        <h1><field name="name" placeholder="Nombre del proceso"/></h1>
                    </div>
                    <group>
                        <group>
                            <field name="code"/>
                            <field name="process_type"/>
                            <field name="sequence"/>
                        </group>
                        <group>
                            <field name="cost_per_sqm"/>
                            <field name="labor_cost"/>
                            <field name="active"/>
                        </group>
                    </group>
                    <field name="description" placeholder="Descripción del proceso..."/>
                </sheet>
            </form>
        </field>
    </record>

    <!-- Lista Procesos -->
    <record id="view_workshop_process_list" model="ir.ui.view">
        <field name="name">workshop.process.list</field>
        <field name="model">workshop.process</field>
        <field name="arch" type="xml">
            <list string="Procesos" editable="bottom">
                <field name="sequence" widget="handle"/>
                <field name="name"/>
                <field name="code"/>
                <field name="process_type"/>
                <field name="cost_per_sqm"/>
                <field name="labor_cost"/>
            </list>
        </field>
    </record>

    <!-- Acción Procesos -->
    <record id="action_workshop_process" model="ir.actions.act_window">
        <field name="name">Procesos</field>
        <field name="res_model">workshop.process</field>
        <field name="view_mode">list,form</field>
    </record>
</odoo>
```

