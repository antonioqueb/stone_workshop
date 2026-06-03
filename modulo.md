## ./__init__.py
```py
from . import models
```

## ./__manifest__.py
```py
{
    'name': 'Stone Workshop',
    'version': '19.0.9.1.0',
    'category': 'Manufacturing',
    'summary': 'Taller de piedra en 3 pasos; panel con cola priorizada y bitácora declarativa',
    'description': '''
Stone Workshop rediseñado para negocio de piedra natural.

Flujo simplificado a tres pasos: borrador, confirmar taller (consume material y
pre-llena salidas sugeridas) y declarar resultado (cuadra la merma residual,
materializa producción y cierra la orden).

Durante el paso "en taller" el usuario puede:
- Registrar la bitácora diaria de avance (fecha, actividad, cantidad, área, notas)
  para órdenes que tomen varios días.
- Marcar como no usadas las placas que no se procesaron; al declarar el resultado
  se devuelven íntegras al stock de origen.

Soporta:
- Acabado masivo de placas.
- Corte de placas en múltiples salidas.
- Procesamiento agregado de formatos / pallets.
- Reproceso o reparación.
- Trazabilidad lote origen / lote resultado.
- Cuadre automático de merma como residual.
''',
    'author': 'Alphaqueb Consulting',
    'website': 'https://www.alphaqueb.com',
    'license': 'LGPL-3',
    'depends': [
        'mrp',
        'stock',
        'product',
        'mail',
        'web',
    ],
    'data': [
        'security/workshop_security.xml',
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'views/workshop_process_views.xml',
        'views/workshop_order_views.xml',
        'views/workshop_menus.xml',
        'reports/workshop_pick_report.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'stone_workshop/static/src/css/workshop.css',
            'stone_workshop/static/src/scss/workshop_lot_selector.scss',
            'stone_workshop/static/src/js/workshop_dashboard.js',
            'stone_workshop/static/src/components/workshop_lot_selector/workshop_lot_selector.xml',
            'stone_workshop/static/src/components/workshop_lot_selector/workshop_lot_selector.js',
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
    <data noupdate="0">
        <record id="seq_workshop_order" model="ir.sequence">
            <field name="name">Orden de Taller</field>
            <field name="code">workshop.order</field>
            <field name="prefix">T-TALLER/%(year)s/</field>
            <field name="padding">4</field>
            <field name="company_id" eval="False"/>
        </record>

        <function model="workshop.order" name="_normalize_workshop_references"/>
    </data>
</odoo>
```

## ./models/__init__.py
```py
from . import workshop_process
from . import workshop_order
from . import stock_quant
```

## ./models/stock_quant.py
```py
from odoo import models, api
from odoo.osv import expression
import logging

_logger = logging.getLogger(__name__)

ACTIVE_WORKSHOP_STATES = (
    'in_workshop',
)


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    @api.model
    def _workshop_safe_int_list(self, values):
        result = []
        for value in values or []:
            try:
                result.append(int(value))
            except (TypeError, ValueError):
                continue
        return result

    @api.model
    def _workshop_get_committed_lot_ids(self, product_id, current_lot_ids=None, order_id=False):
        current_lot_ids = set(self._workshop_safe_int_list(current_lot_ids))
        domain = [
            ('product_id', '=', int(product_id)),
            ('lot_id', '!=', False),
            ('state', 'not in', ('done', 'cancelled')),
            ('order_id.state', 'in', ACTIVE_WORKSHOP_STATES),
        ]
        if order_id:
            try:
                domain.append(('order_id', '!=', int(order_id)))
            except (TypeError, ValueError):
                pass

        lines = self.env['workshop.input.line'].search(domain)
        committed_ids = set(lines.mapped('lot_id').ids)
        return list(committed_ids - current_lot_ids)

    @api.model
    def _workshop_lot_field_exists(self, field_name):
        return field_name in self.env['stock.lot']._fields

    @api.model
    def _build_workshop_lot_domain(self, product_id, filters=None, current_lot_ids=None, location_id=False, order_id=False):
        filters = filters or {}
        current_lot_ids = self._workshop_safe_int_list(current_lot_ids)
        excluded_lot_ids = self._workshop_get_committed_lot_ids(product_id, current_lot_ids, order_id=order_id)

        base_domain = [
            ('product_id', '=', int(product_id)),
            ('lot_id', '!=', False),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ]

        if location_id:
            try:
                base_domain.append(('location_id', 'child_of', int(location_id)))
            except (TypeError, ValueError):
                pass

        if excluded_lot_ids:
            base_domain.append(('lot_id', 'not in', excluded_lot_ids))

        free_domain = []
        if 'reserved_quantity' in self._fields:
            free_domain.append(('reserved_quantity', '=', 0))
        if 'x_tiene_hold' in self._fields:
            free_domain.append(('x_tiene_hold', '=', False))

        if current_lot_ids and free_domain:
            domain = expression.AND([
                base_domain,
                expression.OR([[('lot_id', 'in', current_lot_ids)], free_domain]),
            ])
        else:
            domain = base_domain + free_domain

        if filters.get('lot_name'):
            domain.append(('lot_id.name', 'ilike', filters['lot_name']))
        if filters.get('bloque') and self._workshop_lot_field_exists('x_bloque'):
            domain.append(('lot_id.x_bloque', 'ilike', filters['bloque']))
        if filters.get('atado') and self._workshop_lot_field_exists('x_atado'):
            domain.append(('lot_id.x_atado', 'ilike', filters['atado']))
        if filters.get('alto_min') and self._workshop_lot_field_exists('x_alto'):
            try:
                domain.append(('lot_id.x_alto', '>=', float(filters['alto_min'])))
            except (TypeError, ValueError):
                pass
        if filters.get('ancho_min') and self._workshop_lot_field_exists('x_ancho'):
            try:
                domain.append(('lot_id.x_ancho', '>=', float(filters['ancho_min'])))
            except (TypeError, ValueError):
                pass
        if filters.get('tipo') and self._workshop_lot_field_exists('x_tipo'):
            domain.append(('lot_id.x_tipo', '=', filters['tipo']))

        return domain

    @api.model
    def _workshop_safe_lot_value(self, lot, field_name, default=False):
        if lot and field_name in lot._fields:
            value = lot[field_name]
            if hasattr(value, 'display_name'):
                return value.display_name or default
            return value if value not in (False, None) else default
        return default

    @api.model
    def _build_workshop_lots_data(self, lot_ids):
        lots_data = {}
        if not lot_ids:
            return lots_data

        for lot in self.env['stock.lot'].browse(lot_ids).exists():
            lots_data[lot.id] = {
                'name': lot.name or '',
                'x_grosor': self._workshop_safe_lot_value(lot, 'x_grosor', 0) or self._workshop_safe_lot_value(lot, 'thickness_cm', 0) or 0,
                'x_alto': self._workshop_safe_lot_value(lot, 'x_alto', 0) or self._workshop_safe_lot_value(lot, 'marble_height', 0) or 0,
                'x_ancho': self._workshop_safe_lot_value(lot, 'x_ancho', 0) or self._workshop_safe_lot_value(lot, 'marble_width', 0) or 0,
                'x_tipo': self._workshop_safe_lot_value(lot, 'x_tipo', '') or '',
                'x_bloque': self._workshop_safe_lot_value(lot, 'x_bloque', '') or self._workshop_safe_lot_value(lot, 'lot_general', '') or '',
                'x_atado': self._workshop_safe_lot_value(lot, 'x_atado', '') or '',
                'x_color': self._workshop_safe_lot_value(lot, 'x_color', '') or '',
                'x_origen': self._workshop_safe_lot_value(lot, 'x_origen', '') or '',
                'x_pedimento': self._workshop_safe_lot_value(lot, 'x_pedimento', '') or '',
                'x_fotografia_principal': self._workshop_safe_lot_value(lot, 'x_fotografia_principal', False) or False,
                'x_cantidad_fotos': self._workshop_safe_lot_value(lot, 'x_cantidad_fotos', 0) or 0,
                'x_detalles_placa': self._workshop_safe_lot_value(lot, 'x_detalles_placa', '') or '',
            }
        return lots_data

    @api.model
    def _workshop_quants_to_result(self, quants, lots_data):
        result = []
        for quant in quants:
            lot_id = quant.lot_id.id if quant.lot_id else False
            lot_info = lots_data.get(lot_id, {})
            reserved_qty = quant.reserved_quantity if 'reserved_quantity' in quant._fields else 0.0
            available_qty = (quant.quantity or 0.0) - (reserved_qty or 0.0)
            result.append({
                'id': quant.id,
                'lot_id': [lot_id, lot_info.get('name', '')] if lot_id else False,
                'location_id': [quant.location_id.id, quant.location_id.display_name] if quant.location_id else False,
                'quantity': quant.quantity or 0.0,
                'reserved_quantity': reserved_qty or 0.0,
                'available_quantity': available_qty,
                'x_grosor': lot_info.get('x_grosor', 0) or 0,
                'x_alto': lot_info.get('x_alto', 0) or 0,
                'x_ancho': lot_info.get('x_ancho', 0) or 0,
                'x_tipo': lot_info.get('x_tipo', '') or '',
                'x_bloque': lot_info.get('x_bloque', '') or '',
                'x_atado': lot_info.get('x_atado', '') or '',
                'x_color': lot_info.get('x_color', '') or '',
                'x_origen': lot_info.get('x_origen', '') or '',
                'x_pedimento': lot_info.get('x_pedimento', '') or '',
                'x_fotografia_principal': lot_info.get('x_fotografia_principal', False),
                'x_cantidad_fotos': lot_info.get('x_cantidad_fotos', 0) or 0,
                'x_detalles_placa': lot_info.get('x_detalles_placa', '') or '',
            })
        return result

    @api.model
    def search_workshop_lot_inventory(self, product_id, filters=None, current_lot_ids=None, location_id=False, order_id=False):
        filters = filters or {}
        domain = self._build_workshop_lot_domain(
            product_id=product_id,
            filters=filters,
            current_lot_ids=current_lot_ids,
            location_id=location_id,
            order_id=order_id,
        )
        quants = self.search(domain, limit=300, order='lot_id, location_id, id')
        lots_data = self._build_workshop_lots_data(quants.mapped('lot_id').ids)
        result = self._workshop_quants_to_result(quants, lots_data)
        _logger.info('[WORKSHOP LOT SELECTOR] product=%s result=%s', product_id, len(result))
        return result

    @api.model
    def search_workshop_lot_inventory_paginated(self, product_id, filters=None, current_lot_ids=None, page=0, page_size=35, location_id=False, order_id=False):
        filters = filters or {}
        page = int(page or 0)
        page_size = int(page_size or 35)
        domain = self._build_workshop_lot_domain(
            product_id=product_id,
            filters=filters,
            current_lot_ids=current_lot_ids,
            location_id=location_id,
            order_id=order_id,
        )
        total = self.search_count(domain)
        quants = self.search(domain, limit=page_size, offset=page * page_size, order='lot_id, location_id, id')
        lots_data = self._build_workshop_lots_data(quants.mapped('lot_id').ids)
        return {
            'items': self._workshop_quants_to_result(quants, lots_data),
            'total': total,
        }
```

## ./models/workshop_order.py
```py
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare, float_is_zero
from html import escape
import logging

_logger = logging.getLogger(__name__)

ACTIVE_WORKSHOP_STATES = (
    'in_workshop',
)

# Marca usada en finish_result para distinguir la línea de merma calculada
# automáticamente como residual (entrada − útil − retazos − merma manual)
# de cualquier línea de merma capturada manualmente por el usuario.
RESIDUAL_SCRAP_TAG = 'Merma residual (auto)'


class WorkshopOrder(models.Model):
    _name = 'workshop.order'
    _description = 'Orden de Taller de Piedra'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Referencia', readonly=True, default='Nuevo', copy=False)
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('in_workshop', 'En taller'),
        ('done', 'Terminada'),
        ('cancel', 'Cancelada'),
    ], string='Estado', default='draft', tracking=True)
    priority = fields.Selection([
        ('0', 'Normal'),
        ('1', 'Alta'),
        ('2', 'Urgente'),
    ], string='Prioridad', default='0', tracking=True,
        help='Prioridad de ejecución en el taller. El panel ordena la cola de borradores por prioridad descendente.')

    operation_mode = fields.Selection([
        ('slab_finish', 'Acabado de placas'),
        ('slab_cut', 'Corte de placas'),
        ('format_process', 'Formatos / pallets'),
        ('rework', 'Reproceso / reparación'),
    ], string='Modo operativo', compute='_compute_operation_mode', store=True, readonly=False, tracking=True)

    process_id = fields.Many2one('workshop.process', string='Proceso', required=True, tracking=True)
    process_type = fields.Selection(related='process_id.process_type', store=True, readonly=True)
    default_product_out_id = fields.Many2one(
        'product.product',
        string='Producto salida principal',
        
        domain=[('tracking', '!=', 'none')],
        help='Producto principal que se producirá. Para corte/formato representa el pallet, formato o material terminado objetivo.',
    )
    remnant_product_id = fields.Many2one(
        'product.product',
        string='Producto para retazos',
        domain=[('tracking', '!=', 'none')],
        help='Producto que se usará para ingresar retazos aprovechables. Si se deja vacío, se usa el producto de entrada.',
    )
    production_target_sqm = fields.Float(
        string='Demanda objetivo m²',
        digits=(12, 4),
        tracking=True,
        help='Área útil que se desea producir. Ejemplo: pallet de 100 m².',
    )
    target_pieces = fields.Integer(
        string='Piezas / pallets objetivo',
        default=1,
        help='Cantidad física del resultado principal cuando el producto de salida no se maneja por m².',
    )
    expected_yield_percent = fields.Float(
        string='Rendimiento esperado (%)',
        default=90.0,
        help='Rendimiento esperado del proceso. Sirve para planeación y KPI; no fuerza salidas si se captura una merma manual.',
    )
    planned_loss_percent = fields.Float(
        string='Merma planeada (%)',
        default=0.0,
        help='Porcentaje de merma a generar automáticamente sobre el área total de entrada.',
    )
    planned_loss_sqm = fields.Float(
        string='Merma planeada m²',
        digits=(12, 4),
        help='Merma absoluta a generar automáticamente. Si se captura, tiene prioridad sobre el porcentaje.',
    )
    input_product_id = fields.Many2one(
        'product.product',
        string='Producto entrada',
        domain=[('tracking', '!=', 'none')],
        help='Producto base para filtrar el selector visual de lotes de entrada.',
    )
    input_selector_anchor = fields.Boolean(
        string='Selector visual de lotes',
        compute='_compute_input_selector_anchor',
    )

    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company, required=True)
    warehouse_id = fields.Many2one('stock.warehouse', string='Almacén', default=lambda self: self._default_warehouse())
    location_src_id = fields.Many2one('stock.location', string='Ubicación origen', domain=[('usage', '=', 'internal')])
    location_workshop_id = fields.Many2one('stock.location', string='Ubicación taller / producción')
    location_dest_id = fields.Many2one('stock.location', string='Ubicación destino', domain=[('usage', '=', 'internal')])

    responsible_id = fields.Many2one('res.users', string='Responsable', default=lambda self: self.env.user, tracking=True)
    date_planned = fields.Datetime(string='Fecha planeada')
    date_start = fields.Datetime(string='Fecha inicio', readonly=True, copy=False)
    date_done = fields.Datetime(string='Fecha terminación', readonly=True, copy=False)
    notes = fields.Html(string='Notas')

    area_tolerance_percent = fields.Float(string='Tolerancia de área (%)', default=2.0)

    input_line_ids = fields.One2many('workshop.input.line', 'order_id', string='Entradas')
    output_line_ids = fields.One2many('workshop.output.line', 'order_id', string='Salidas')
    progress_log_ids = fields.One2many('workshop.progress.log', 'order_id', string='Bitácora de avance')
    trace_ids = fields.One2many('workshop.transformation.trace', 'order_id', string='Trazabilidad')

    consume_picking_ids = fields.Many2many(
        'stock.picking',
        'workshop_order_consume_picking_rel',
        'order_id',
        'picking_id',
        string='Pickings de consumo',
        copy=False,
        readonly=True,
    )
    produce_picking_ids = fields.Many2many(
        'stock.picking',
        'workshop_order_produce_picking_rel',
        'order_id',
        'picking_id',
        string='Pickings de producción',
        copy=False,
        readonly=True,
    )
    return_picking_ids = fields.Many2many(
        'stock.picking',
        'workshop_order_return_picking_rel',
        'order_id',
        'picking_id',
        string='Pickings de devolución',
        copy=False,
        readonly=True,
    )

    input_count = fields.Integer(string='Entradas', compute='_compute_counts')
    output_count = fields.Integer(string='Salidas', compute='_compute_counts')
    trace_count = fields.Integer(string='Trazas', compute='_compute_counts')
    progress_log_count = fields.Integer(string='Avances', compute='_compute_counts')
    consume_picking_count = fields.Integer(string='Consumos', compute='_compute_counts')
    produce_picking_count = fields.Integer(string='Producciones', compute='_compute_counts')
    return_picking_count = fields.Integer(string='Devoluciones', compute='_compute_counts')

    qty_in_total = fields.Float(string='Cantidad entrada total', compute='_compute_totals', store=True, digits=(12, 4))
    qty_out_total = fields.Float(string='Cantidad salida total', compute='_compute_totals', store=True, digits=(12, 4))
    area_in_total = fields.Float(string='Área entrada total m²', compute='_compute_totals', store=True, digits=(12, 4))
    area_out_total = fields.Float(string='Área útil salida m²', compute='_compute_totals', store=True, digits=(12, 4))
    area_remnant_total = fields.Float(string='Área retazos m²', compute='_compute_totals', store=True, digits=(12, 4))
    area_loss_total = fields.Float(string='Área merma m²', compute='_compute_totals', store=True, digits=(12, 4))
    total_accounted_area_sqm = fields.Float(string='Área contabilizada m²', compute='_compute_totals', store=True, digits=(12, 4))
    area_balance_delta = fields.Float(string='Diferencia balance m²', compute='_compute_totals', store=True, digits=(12, 4))
    yield_percent = fields.Float(string='Rendimiento real (%)', compute='_compute_totals', store=True, digits=(12, 2))
    remnant_percent = fields.Float(string='Retazo (%)', compute='_compute_totals', store=True, digits=(12, 2))
    loss_percent = fields.Float(string='Merma real (%)', compute='_compute_totals', store=True, digits=(12, 2))
    target_coverage_percent = fields.Float(string='Cumplimiento objetivo (%)', compute='_compute_totals', store=True, digits=(12, 2))
    planned_input_required_sqm = fields.Float(string='Entrada requerida estimada m²', compute='_compute_totals', store=True, digits=(12, 4))


    material_cost = fields.Float(string='Costo material', digits=(12, 2))
    process_cost = fields.Float(string='Costo proceso', compute='_compute_costs', store=True, digits=(12, 2))
    labor_cost = fields.Float(string='Costo M.O.', digits=(12, 2))
    machine_cost = fields.Float(string='Costo máquina', digits=(12, 2))
    overhead_cost = fields.Float(string='Costo indirecto', digits=(12, 2))
    loss_cost = fields.Float(string='Costo merma', digits=(12, 2))
    total_cost = fields.Float(string='Costo total', compute='_compute_costs', store=True, digits=(12, 2))
    cost_per_sqm = fields.Float(string='Costo por m² útil', compute='_compute_costs', store=True, digits=(12, 2))

    @api.model
    def _default_warehouse(self):
        return self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'Nuevo') == 'Nuevo':
                vals['name'] = self.env['ir.sequence'].next_by_code('workshop.order') or 'Nuevo'
            # El modo operativo siempre lo dicta el proceso. Si el contexto
            # del dashboard o cualquier `default_operation_mode` externo trajo
            # otro valor en vals, lo sobrescribimos con el del proceso para
            # evitar inconsistencias (ej. proceso de corte que genera salidas
            # como si fuera acabado porque el default del contexto era distinto).
            if vals.get('process_id'):
                process = self.env['workshop.process'].browse(vals['process_id'])
                if process.exists() and process.default_operation_mode:
                    vals['operation_mode'] = process.default_operation_mode
        orders = super().create(vals_list)
        for order in orders:
            order._ensure_default_locations()
        return orders

    @api.model
    def _normalize_workshop_references(self):
        """Normaliza folios históricos WS/* al nuevo prefijo T-TALLER/*.

        Este método se llama desde data/sequence_data.xml durante la actualización
        del módulo para que el cambio de nomenclatura sea visible también en
        órdenes ya creadas en ambientes de prueba.
        """
        orders = self.sudo().search([('name', '=like', 'WS/%')])
        for order in orders:
            order.name = order.name.replace('WS/', 'T-TALLER/', 1)
        return True

    @api.depends('input_line_ids')
    def _compute_input_selector_anchor(self):
        for rec in self:
            rec.input_selector_anchor = bool(rec.input_line_ids)

    @api.onchange('input_line_ids')
    def _onchange_input_line_ids_sync_product(self):
        for rec in self:
            if not rec.input_product_id and rec.input_line_ids:
                first_line = rec.input_line_ids.filtered(lambda l: l.product_id)[:1]
                if first_line:
                    rec.input_product_id = first_line.product_id

    def _get_lot_metadata_value(self, lot, *field_names):
        self.ensure_one()
        for fname in field_names:
            if lot and fname in lot._fields:
                value = lot[fname]
                if hasattr(value, 'display_name'):
                    return value.display_name
                return value
        return False

    def _get_lot_best_quant(self, product, lot, location=False):
        self.ensure_one()
        domain = [
            ('product_id', '=', product.id),
            ('lot_id', '=', lot.id),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ]
        if location:
            domain.append(('location_id', 'child_of', location.id))
        quant = self.env['stock.quant'].search(domain, limit=1, order='quantity desc, reserved_quantity asc, id')
        if not quant and location:
            fallback_domain = [
                ('product_id', '=', product.id),
                ('lot_id', '=', lot.id),
                ('location_id.usage', '=', 'internal'),
                ('quantity', '>', 0),
            ]
            quant = self.env['stock.quant'].search(fallback_domain, limit=1, order='quantity desc, reserved_quantity asc, id')
        return quant

    def _product_uom_is_area(self, product):
        self.ensure_one()
        if not product or not product.uom_id:
            return False

        uom = product.uom_id
        text_parts = [
            uom.name or '',
            uom.display_name or '',
        ]

        # Odoo 19 puede no exponer category_id en uom.uom. Se lee por
        # introspección para evitar AttributeError y mantener compatibilidad.
        for field_name in (
            'category_id',
            'uom_category_id',
            'measure_type',
            'uom_type',
            'quantity_type',
        ):
            if field_name not in uom._fields:
                continue
            value = uom[field_name]
            if hasattr(value, 'display_name'):
                text_parts.append(value.display_name or '')
            elif value not in (False, None):
                text_parts.append(str(value))

        text = ' '.join(text_parts).lower()
        area_tokens = (
            'm²',
            'm2',
            'm^2',
            'sqm',
            'sq m',
            'metro cuadrado',
            'metros cuadrados',
            'superficie',
            'area',
            'área',
        )
        return any(token in text for token in area_tokens)

    def _safe_float(self, value, default=0.0):
        try:
            if value in (False, None, ''):
                return default
            if isinstance(value, str):
                value = value.replace(',', '.')
            return float(value)
        except (TypeError, ValueError):
            return default

    def _area_from_dimensions_sqm(self, width, height, pieces=1):
        """Calcula m² detectando si ancho/alto vienen en metros o centímetros."""
        width = self._safe_float(width)
        height = self._safe_float(height)
        pieces = int(self._safe_float(pieces, 1.0) or 1)
        if width <= 0.0 or height <= 0.0 or pieces <= 0:
            return 0.0
        if max(width, height) <= 20.0:
            return width * height * pieces
        return (width / 100.0) * (height / 100.0) * pieces

    def _resolve_area_sqm(self, product=False, explicit_area=False, width=False, height=False, pieces=1, fallback_qty=False):
        explicit_area = self._safe_float(explicit_area)
        fallback_qty = self._safe_float(fallback_qty)
        dim_area = self._area_from_dimensions_sqm(width, height, pieces)

        if product and self._product_uom_is_area(product) and fallback_qty > 0.0:
            return fallback_qty
        if explicit_area > 0.0:
            if fallback_qty > 0.0 and explicit_area < (fallback_qty * 0.25):
                return fallback_qty
            return explicit_area
        if dim_area > 0.0:
            return dim_area
        return fallback_qty or 0.0

    def _stock_qty_from_area(self, product, area_sqm, pieces=1, fallback_qty=False):
        self.ensure_one()
        area_sqm = float(area_sqm or 0.0)
        pieces = int(pieces or 0)
        if product and self._product_uom_is_area(product):
            return area_sqm
        if fallback_qty not in (False, None):
            try:
                fallback = float(fallback_qty or 0.0)
                if fallback:
                    return fallback
            except (TypeError, ValueError):
                pass
        return float(pieces or 1)

    def _input_line_area(self, line):
        self.ensure_one()
        area = self._safe_float(line.area_sqm)
        qty = self._safe_float(line.qty_in)
        if qty > 0.0 and self._product_uom_is_area(line.product_id) and (not area or area < (qty * 0.25)):
            return qty
        if area > 0.0:
            return area
        return qty or 0.0

    def _output_line_area(self, line):
        self.ensure_one()
        area = self._safe_float(line.area_sqm)
        qty = self._safe_float(line.qty_out)
        if qty > 0.0 and line.product_id and self._product_uom_is_area(line.product_id) and (not area or area < (qty * 0.25)):
            return qty
        if area > 0.0:
            return area
        return qty or 0.0

    def _normalize_input_area_values(self):
        """Corrige líneas guardadas con área diminuta por mezcla metro/centímetro."""
        for rec in self:
            for line in rec._get_active_input_lines():
                expected_area = rec._input_line_area(line)
                stored_area = rec._safe_float(line.area_sqm)
                if expected_area > 0.0 and (not stored_area or stored_area < (expected_area * 0.25)):
                    line.write({'area_sqm': expected_area})

    def _compact_result_code(self, value=False, fallback='CRT'):
        raw = (value or fallback or 'CRT')
        code = ''.join(ch for ch in str(raw).upper() if ch.isalnum())
        return (code or fallback or 'CRT')[:8]

    def _get_result_lot_suffix(self, output_type):
        self.ensure_one()
        if output_type == 'remnant':
            return 'RET'
        if output_type in ('scrap', 'rejected'):
            return 'MER'
        return self._compact_result_code(self.process_id.code if self.process_id else False, fallback='CRT')

    def _get_result_lot_source_line(self, output_type='format_piece', target_area=0.0):
        self.ensure_one()
        active_inputs = self._get_active_input_lines().filtered(lambda l: l.lot_id)
        if not active_inputs:
            return False

        target_area = self._safe_float(target_area)
        if output_type == 'remnant' and target_area > 0.0:
            fitting = []
            for line in active_inputs:
                area = self._input_line_area(line)
                if area >= target_area:
                    fitting.append((area, line.id, line))
            if fitting:
                fitting.sort(key=lambda item: (item[0], item[1]))
                return fitting[0][2]

        ordered = [(self._input_line_area(line), line.sequence or 0, line.id, line) for line in active_inputs]
        if output_type == 'remnant':
            ordered.sort(key=lambda item: (-item[0], item[1], item[2]))
        else:
            ordered.sort(key=lambda item: (item[1], item[2]))
        return ordered[0][3] if ordered else active_inputs[:1]

    def _fallback_compact_order_lot_name(self):
        self.ensure_one()
        raw = self.name or 'TALLER'
        if '/' in raw:
            return 'T%s' % raw.split('/')[-1]
        return ''.join(ch for ch in raw.upper() if ch.isalnum())[:12] or 'TALLER'

    def _get_compact_result_lot_name(self, output_type='format_piece', product=False, target_area=0.0, exclude_output=False, exclude_lot=False):
        self.ensure_one()
        source_line = self._get_result_lot_source_line(output_type=output_type, target_area=target_area)
        if source_line and source_line.lot_id:
            base = source_line.lot_id.name
        else:
            base = self._fallback_compact_order_lot_name()
        suffix = self._get_result_lot_suffix(output_type)
        return self._make_unique_lot_name(
            '%s-%s' % (base, suffix),
            product=product,
            exclude_output=exclude_output,
            exclude_lot=exclude_lot,
        )

    def _get_active_input_lines(self):
        self.ensure_one()
        return self.input_line_ids.filtered(lambda l: l.state != 'cancelled')

    def _get_used_input_lines(self):
        """Entradas que efectivamente se procesaron en el taller.

        Las placas se consideran usadas cuando se registran en alguna corrida
        de la bitácora. Antes de que existan corridas (típicamente en draft o
        recién pasada a in_workshop) se devuelven todas las activas, para que
        el balance de área y la merma residual no queden vacíos durante la
        configuración inicial.
        """
        self.ensure_one()
        active = self._get_active_input_lines()
        if not self.progress_log_ids:
            return active
        return active.filtered(lambda l: l.is_used)

    def _get_active_output_lines(self):
        self.ensure_one()
        return self.output_line_ids.filtered(lambda l: l.state != 'cancelled')

    def _get_live_input_area(self):
        self.ensure_one()
        return sum(self._input_line_area(line) for line in self._get_active_input_lines())

    def _get_main_output_product(self):
        self.ensure_one()
        active_inputs = self._get_active_input_lines()
        return (
            self.default_product_out_id
            or self.input_product_id
            or (active_inputs[:1].product_id if active_inputs else False)
        )

    def _get_remnant_product(self):
        self.ensure_one()
        active_inputs = self._get_active_input_lines()
        return (
            self.remnant_product_id
            or self.input_product_id
            or (active_inputs[:1].product_id if active_inputs else False)
            or self._get_main_output_product()
        )

    def _map_lot_material_type(self, lot):
        self.ensure_one()
        raw_type = ''
        for fname in ('x_tipo', 'tipo', 'material_type'):
            if lot and fname in lot._fields and lot[fname]:
                raw_type = str(lot[fname]).lower()
                break
        if raw_type in ('formato', 'format', 'pieza', 'piece'):
            return 'format'
        if raw_type in ('pallet', 'palet'):
            return 'pallet'
        if raw_type in ('retazo', 'remnant'):
            return 'remnant'
        return 'slab'

    @api.model
    def prepare_input_line_vals_from_lots(self, product_id, lot_ids, location_id=False):
        """
        Construye valores de líneas de entrada desde el selector visual.

        Se mantiene como API de modelo para que funcione también en órdenes no guardadas:
        el widget solo necesita producto, lotes y ubicación origen opcional.
        """
        product = self.env['product.product'].browse(int(product_id)) if product_id else self.env['product.product']
        if not product or not product.exists():
            raise UserError(_('Selecciona un producto de entrada antes de agregar lotes.'))

        safe_lot_ids = []
        for lot_id in lot_ids or []:
            try:
                safe_lot_ids.append(int(lot_id))
            except (TypeError, ValueError):
                continue

        if not safe_lot_ids:
            return []

        location = self.env['stock.location'].browse(int(location_id)) if location_id else False
        lot_map = {lot.id: lot for lot in self.env['stock.lot'].browse(safe_lot_ids).exists()}
        line_vals = []

        order_stub = self.new({
            'company_id': self.env.company.id,
            'location_src_id': location.id if location else False,
        })

        for lot_id in safe_lot_ids:
            lot = lot_map.get(lot_id)
            if not lot:
                continue

            line_product = lot.product_id if lot.product_id else product
            if lot.product_id and lot.product_id != product:
                raise UserError(_(
                    'El lote %(lot)s pertenece al producto %(lot_product)s, no al producto %(product)s.'
                ) % {
                    'lot': lot.name,
                    'lot_product': lot.product_id.display_name,
                    'product': product.display_name,
                })

            quant = order_stub._get_lot_best_quant(line_product, lot, location=location)
            reserved = quant.reserved_quantity if quant and 'reserved_quantity' in quant._fields else 0.0
            available_qty = ((quant.quantity or 0.0) - (reserved or 0.0)) if quant else 0.0

            width = order_stub._get_lot_metadata_value(lot, 'marble_width', 'x_ancho', 'width_cm', 'width', 'stone_width', 'x_width_cm')
            height = order_stub._get_lot_metadata_value(lot, 'marble_height', 'x_alto', 'height_cm', 'height', 'stone_height', 'x_height_cm')
            thickness = order_stub._get_lot_metadata_value(lot, 'thickness_cm', 'x_grosor', 'thickness', 'marble_thickness', 'x_thickness_cm')
            area = order_stub._get_lot_metadata_value(lot, 'marble_sqm', 'area_sqm', 'sqm', 'x_area_sqm')
            block = order_stub._get_lot_metadata_value(lot, 'lot_general', 'x_bloque', 'block_name', 'block', 'bloque', 'x_block', 'x_bloque')
            tone = order_stub._get_lot_metadata_value(lot, 'tone', 'tono', 'x_tone', 'x_tono')
            finish = order_stub._get_lot_metadata_value(lot, 'current_finish', 'finish', 'finish_id', 'x_finish')

            width_float = order_stub._safe_float(width)
            height_float = order_stub._safe_float(height)
            thickness_float = order_stub._safe_float(thickness)
            area_float = order_stub._resolve_area_sqm(
                product=line_product,
                explicit_area=area,
                width=width_float,
                height=height_float,
                pieces=1,
                fallback_qty=available_qty,
            )

            qty_in = available_qty or area_float or 1.0
            area_sqm = area_float or qty_in

            line_vals.append({
                'material_type': order_stub._map_lot_material_type(lot),
                'product_id': line_product.id,
                'lot_id': lot.id,
                'qty_in': qty_in,
                'area_sqm': area_sqm,
                'width_cm': width_float,
                'height_cm': height_float,
                'thickness_cm': thickness_float,
                'pieces': 1,
                'block_name': block or '',
                'tone': tone or '',
                'current_finish': finish or '',
                'location_id': quant.location_id.id if quant else False,
                'state': 'pending',
            })

        return line_vals

    @api.depends('input_line_ids', 'output_line_ids', 'trace_ids', 'progress_log_ids')
    def _compute_counts(self):
        for rec in self:
            rec.input_count = len(rec.input_line_ids)
            rec.output_count = len(rec.output_line_ids)
            rec.trace_count = len(rec.trace_ids)
            rec.progress_log_count = len(rec.progress_log_ids)
            rec.consume_picking_count = len(rec.consume_picking_ids)
            rec.produce_picking_count = len(rec.produce_picking_ids)
            rec.return_picking_count = len(rec.return_picking_ids)

    @api.depends(
        'input_line_ids.qty_in',
        'input_line_ids.area_sqm',
        'input_line_ids.state',
        'input_line_ids.is_used',
        'progress_log_ids',
        'output_line_ids.qty_out',
        'output_line_ids.area_sqm',
        'output_line_ids.output_type',
        'output_line_ids.state',
        'production_target_sqm',
        'expected_yield_percent',
    )
    def _compute_totals(self):
        for rec in self:
            has_logs = bool(rec.progress_log_ids)
            active_inputs = rec.input_line_ids.filtered(
                lambda l: l.state != 'cancelled' and (not has_logs or l.is_used)
            )
            active_outputs = rec.output_line_ids.filtered(lambda l: l.state != 'cancelled')
            useful_outputs = active_outputs.filtered(lambda l: l.output_type in ('finished_slab', 'format_piece'))
            remnant_outputs = active_outputs.filtered(lambda l: l.output_type == 'remnant')
            scrap_outputs = active_outputs.filtered(lambda l: l.output_type in ('scrap', 'rejected'))

            rec.qty_in_total = sum(active_inputs.mapped('qty_in'))
            rec.qty_out_total = sum(useful_outputs.mapped('qty_out'))
            rec.area_in_total = sum(rec._input_line_area(l) for l in active_inputs)
            rec.area_out_total = sum(rec._output_line_area(l) for l in useful_outputs)
            rec.area_remnant_total = sum(rec._output_line_area(l) for l in remnant_outputs)
            rec.area_loss_total = sum(rec._output_line_area(l) for l in scrap_outputs)
            rec.total_accounted_area_sqm = rec.area_out_total + rec.area_remnant_total + rec.area_loss_total
            rec.area_balance_delta = rec.area_in_total - rec.total_accounted_area_sqm

            if rec.area_in_total:
                rec.yield_percent = (rec.area_out_total / rec.area_in_total) * 100.0
                rec.remnant_percent = (rec.area_remnant_total / rec.area_in_total) * 100.0
                rec.loss_percent = (rec.area_loss_total / rec.area_in_total) * 100.0
            else:
                rec.yield_percent = 0.0
                rec.remnant_percent = 0.0
                rec.loss_percent = 0.0

            if rec.production_target_sqm:
                rec.target_coverage_percent = (rec.area_out_total / rec.production_target_sqm) * 100.0
            else:
                rec.target_coverage_percent = 0.0

            if rec.production_target_sqm and rec.expected_yield_percent:
                rec.planned_input_required_sqm = rec.production_target_sqm / (rec.expected_yield_percent / 100.0)
            else:
                rec.planned_input_required_sqm = 0.0

    @api.depends(
        'area_in_total',
        'area_out_total',
        'process_id.cost_per_sqm',
        'material_cost',
        'labor_cost',
        'machine_cost',
        'overhead_cost',
        'loss_cost',
    )
    def _compute_costs(self):
        for rec in self:
            cost_base_area = rec.area_in_total or rec.area_out_total
            rec.process_cost = cost_base_area * (rec.process_id.cost_per_sqm or 0.0)
            rec.total_cost = (
                (rec.material_cost or 0.0)
                + (rec.process_cost or 0.0)
                + (rec.labor_cost or 0.0)
                + (rec.machine_cost or 0.0)
                + (rec.overhead_cost or 0.0)
                + (rec.loss_cost or 0.0)
            )
            useful_area = rec.area_out_total or 0.0
            rec.cost_per_sqm = rec.total_cost / useful_area if useful_area else 0.0

    @api.depends('process_id.default_operation_mode')
    def _compute_operation_mode(self):
        """El modo operativo lo dicta el proceso seleccionado.

        El proceso ya define su `default_operation_mode` (acabado, corte,
        formato o reproceso). Aquí se replica al guardar la orden para evitar
        que el usuario tenga que capturarlo de nuevo: si el proceso cambia,
        el modo se ajusta automáticamente.
        """
        for rec in self:
            if rec.process_id and rec.process_id.default_operation_mode:
                rec.operation_mode = rec.process_id.default_operation_mode
            elif not rec.operation_mode:
                rec.operation_mode = 'slab_finish'

    @api.onchange('process_id')
    def _onchange_process_id(self):
        for rec in self:
            if rec.process_id:
                rec.labor_cost = rec.process_id.labor_cost or rec.labor_cost
                rec.machine_cost = rec.process_id.machine_cost or rec.machine_cost
                rec.overhead_cost = rec.process_id.overhead_cost or rec.overhead_cost
                if 'expected_yield_percent' in rec.process_id._fields and rec.process_id.expected_yield_percent:
                    rec.expected_yield_percent = rec.process_id.expected_yield_percent
                if 'default_loss_percent' in rec.process_id._fields:
                    rec.planned_loss_percent = rec.process_id.default_loss_percent or 0.0
                    rec._onchange_planned_loss_percent()

    @api.onchange('planned_loss_percent', 'input_line_ids', 'operation_mode')
    def _onchange_planned_loss_percent(self):
        for rec in self:
            if rec.operation_mode in ('slab_cut', 'format_process') and rec.planned_loss_percent:
                input_area = sum(rec._input_line_area(line) for line in rec.input_line_ids if line.state != 'cancelled')
                rec.planned_loss_sqm = input_area * (rec.planned_loss_percent / 100.0)

    @api.onchange('warehouse_id')
    def _onchange_warehouse_id(self):
        self._ensure_default_locations()

    def _ensure_default_locations(self):
        for rec in self:
            warehouse = rec.warehouse_id or rec._default_warehouse()
            if warehouse and not rec.location_src_id:
                rec.location_src_id = warehouse.lot_stock_id.id
            if warehouse and not rec.location_dest_id:
                rec.location_dest_id = warehouse.lot_stock_id.id
            if not rec.location_workshop_id:
                rec.location_workshop_id = rec._get_default_workshop_location().id

    def _get_default_workshop_location(self):
        self.ensure_one()
        Location = self.env['stock.location']
        location = Location.search([
            ('usage', '=', 'production'),
            '|', ('company_id', '=', self.company_id.id), ('company_id', '=', False),
        ], limit=1)
        if not location:
            location = self.env.ref('stock.location_production', raise_if_not_found=False)
        if not location:
            raise UserError(_('No se encontró una ubicación de producción/taller.'))
        return location

    def _get_internal_picking_type(self):
        self.ensure_one()
        PickingType = self.env['stock.picking.type']
        picking_type = False
        if self.warehouse_id:
            picking_type = PickingType.search([
                ('warehouse_id', '=', self.warehouse_id.id),
                ('code', '=', 'internal'),
            ], limit=1)
        if not picking_type:
            picking_type = PickingType.search([
                ('company_id', '=', self.company_id.id),
                ('code', '=', 'internal'),
            ], limit=1)
        if not picking_type:
            raise UserError(_('No se encontró un tipo de operación interna para la compañía.'))
        return picking_type

    def _get_available_qty_for_lot(self, product, lot, location=False):
        self.ensure_one()
        if not product or not lot:
            return 0.0
        location = location or self.location_src_id
        domain = [
            ('product_id', '=', product.id),
            ('lot_id', '=', lot.id),
            ('location_id.usage', '=', 'internal'),
        ]
        if location:
            domain.append(('location_id', 'child_of', location.id))
        quants = self.env['stock.quant'].search(domain)
        qty = 0.0
        for quant in quants:
            reserved = quant.reserved_quantity if 'reserved_quantity' in quant._fields else 0.0
            qty += (quant.quantity or 0.0) - (reserved or 0.0)
        return qty

    def _make_unique_lot_name(self, base_name, product=False, exclude_output=False, exclude_lot=False):
        self.ensure_one()
        if not base_name:
            base_name = self.name
        base_name = str(base_name).strip()
        candidate = base_name
        index = 2
        Lot = self.env['stock.lot']
        while True:
            lot_domain = [('name', '=', candidate)]
            if product:
                lot_domain.append(('product_id', '=', product.id))
            if exclude_lot:
                lot_domain.append(('id', '!=', exclude_lot.id))
            lot_exists = Lot.search_count(lot_domain)
            output_exists = False
            if exclude_output:
                output_exists = bool(self.output_line_ids.filtered(lambda l: l.lot_name == candidate and l.id != exclude_output.id))
            else:
                output_exists = bool(self.output_line_ids.filtered(lambda l: l.lot_name == candidate))
            if not lot_exists and not output_exists:
                return candidate
            candidate = '%s-%02d' % (base_name, index)
            index += 1

    def _default_output_lot_name(self, input_line):
        self.ensure_one()
        source = input_line.lot_id.name if input_line.lot_id else input_line.product_id.display_name
        code = self.process_id.code if self.process_id else 'PROC'
        return self._make_unique_lot_name('%s-%s' % (source, code), product=(self.default_product_out_id or input_line.product_id))

    def _unlink_regenerable_outputs(self):
        self.ensure_one()
        protected = self.output_line_ids.filtered(
            lambda line: line.state in ('produced', 'received', 'scrapped') or line.produce_picking_id
        )
        if protected:
            raise UserError(_(
                'No se pueden regenerar salidas porque ya existen salidas producidas/recibidas. '
                'Cancela o revierte primero los movimientos generados.'
            ))
        self.output_line_ids.filtered(lambda line: line.state != 'cancelled').unlink()

    def _create_output_line(self, vals):
        self.ensure_one()
        clean_vals = dict(vals or {})
        clean_vals.setdefault('order_id', self.id)
        clean_vals.setdefault('location_dest_id', self.location_dest_id.id if self.location_dest_id else False)
        return self.env['workshop.output.line'].create(clean_vals)

    def _generate_finish_like_outputs(self):
        self.ensure_one()
        created = 0
        active_inputs = self._get_active_input_lines()

        for input_line in active_inputs:
            existing = self.output_line_ids.filtered(lambda o: o.input_line_id == input_line and o.state != 'cancelled')
            if existing:
                continue

            product_out = self.default_product_out_id or input_line.product_id
            output_type = 'finished_slab' if self.operation_mode in ('slab_finish', 'rework') else 'format_piece'
            lot_name = self._make_unique_lot_name(
                '%s-%s' % (
                    input_line.lot_id.name if input_line.lot_id else input_line.product_id.display_name,
                    self.process_id.code or 'PROC',
                ),
                product=product_out,
            )
            qty_out = input_line.qty_in
            input_area = self._input_line_area(input_line)
            if self._product_uom_is_area(product_out):
                qty_out = input_area

            self._create_output_line({
                'input_line_id': input_line.id,
                'output_type': output_type,
                'product_id': product_out.id,
                'lot_name': lot_name,
                'qty_out': qty_out,
                'area_sqm': input_area,
                'width_cm': input_line.width_cm,
                'height_cm': input_line.height_cm,
                'thickness_cm': input_line.thickness_cm,
                'pieces': input_line.pieces or 1,
                'finish_result': self.process_id.name,
            })
            created += 1

        return created

    def _sync_finish_outputs_with_used_inputs(self):
        """Garantiza el contrato 1:1 entre placas usadas y salidas de acabado.

        Al declarar el resultado en modo `slab_finish`/`rework`:
        - cancela las salidas huérfanas (sin entrada ligada) o ligadas a una
          placa que nunca se registró en la bitácora;
        - colapsa duplicados quedándose con la primera salida activa por placa;
        - re-crea la salida 1:1 si la placa quedó sin ninguna salida activa;
        - rellena `qty_out` y `area_sqm` desde la entrada cuando vienen en cero
          (edición manual, valores no propagados por onchange) para que la
          validación final no rechace placas con cantidades vacías.
        """
        self.ensure_one()
        used_inputs = self._get_used_input_lines()
        used_input_ids = set(used_inputs.ids)
        protected_states = ('produced', 'received', 'scrapped')

        orphans = self._get_active_output_lines().filtered(
            lambda l: l.output_type in ('finished_slab', 'format_piece')
            and l.state not in protected_states
            and (not l.input_line_id or l.input_line_id.id not in used_input_ids)
        )
        if orphans:
            orphans.write({'state': 'cancelled'})

        precision = self.env['decimal.precision'].precision_get('Product Unit of Measure') or 4
        for input_line in used_inputs:
            outputs = self.output_line_ids.filtered(
                lambda o: o.input_line_id == input_line and o.state != 'cancelled'
            )
            if not outputs:
                self._generate_finish_like_outputs()
                outputs = self.output_line_ids.filtered(
                    lambda o: o.input_line_id == input_line and o.state != 'cancelled'
                )
                if not outputs:
                    continue

            primary = outputs[:1]
            duplicates = (outputs - primary).filtered(lambda d: d.state not in protected_states)
            if duplicates:
                duplicates.write({'state': 'cancelled'})

            if primary.state in protected_states:
                continue

            input_area = self._input_line_area(input_line)
            update_vals = {}
            current_area = self._safe_float(primary.area_sqm)
            if float_compare(current_area, 0.0, precision_digits=precision) <= 0 and input_area > 0.0:
                update_vals['area_sqm'] = input_area
            current_qty = self._safe_float(primary.qty_out)
            if float_compare(current_qty, 0.0, precision_digits=precision) <= 0:
                product = primary.product_id or self.default_product_out_id
                target_area = update_vals.get('area_sqm', current_area) or input_area
                if product and self._product_uom_is_area(product) and target_area > 0.0:
                    update_vals['qty_out'] = target_area
                else:
                    fallback_qty = self._safe_float(input_line.qty_in) or target_area or float(primary.pieces or 1)
                    if fallback_qty > 0.0:
                        update_vals['qty_out'] = fallback_qty
            if update_vals:
                primary.write(update_vals)

        return True

    def _generate_cut_or_format_outputs(self):
        self.ensure_one()
        self._normalize_input_area_values()
        active_inputs = self._get_active_input_lines()
        if not active_inputs:
            raise UserError(_('Agrega al menos una línea de entrada antes de generar salidas.'))

        input_area = self._get_live_input_area()
        precision = self.env['decimal.precision'].precision_get('Product Unit of Measure') or 4

        if float_compare(input_area, 0.0, precision_digits=precision) <= 0:
            raise UserError(_('Las entradas deben tener área m² mayor a cero para generar salidas de corte/formato.'))

        product_out = self._get_main_output_product()
        if not product_out:
            raise UserError(_('Define el producto de salida principal para generar la salida productiva.'))

        target_area = float(self.production_target_sqm or 0.0)
        if float_compare(target_area, 0.0, precision_digits=precision) <= 0:
            target_area = input_area

        tolerance = input_area * ((self.area_tolerance_percent or 0.0) / 100.0)
        if target_area - input_area > tolerance:
            raise UserError(_(
                'La demanda objetivo excede el área de entrada. Entrada: %(input).4f m², objetivo: %(target).4f m².'
            ) % {
                'input': input_area,
                'target': target_area,
            })

        loss_area = float(self.planned_loss_sqm or 0.0)
        if not loss_area and self.planned_loss_percent:
            loss_area = input_area * (self.planned_loss_percent / 100.0)

        if loss_area < 0:
            loss_area = 0.0

        if target_area + loss_area - input_area > tolerance:
            raise UserError(_(
                'La salida objetivo más la merma planeada exceden el área disponible. '
                'Entrada: %(input).4f m², objetivo: %(target).4f m², merma: %(loss).4f m².'
            ) % {
                'input': input_area,
                'target': target_area,
                'loss': loss_area,
            })

        remnant_area = input_area - target_area - loss_area
        if remnant_area < 0 and abs(remnant_area) <= tolerance:
            remnant_area = 0.0

        self._unlink_regenerable_outputs()

        main_pieces = self.target_pieces or 1
        main_lot_name = self._get_compact_result_lot_name(
            output_type='format_piece',
            product=product_out,
            target_area=target_area,
        )
        self._create_output_line({
            'input_line_id': False,
            'output_type': 'format_piece',
            'product_id': product_out.id,
            'lot_name': main_lot_name,
            'qty_out': self._stock_qty_from_area(product_out, target_area, pieces=main_pieces),
            'area_sqm': target_area,
            'pieces': main_pieces,
            'finish_result': self.process_id.name,
        })
        created = 1

        if remnant_area and float_compare(remnant_area, 0.0, precision_digits=precision) > 0:
            remnant_product = self._get_remnant_product()
            if not remnant_product:
                raise UserError(_('Define un producto de entrada o producto para retazos antes de generar retazos aprovechables.'))

            remnant_lot_name = self._get_compact_result_lot_name(
                output_type='remnant',
                product=remnant_product,
                target_area=remnant_area,
            )
            self._create_output_line({
                'input_line_id': False,
                'output_type': 'remnant',
                'product_id': remnant_product.id,
                'lot_name': remnant_lot_name,
                'qty_out': self._stock_qty_from_area(remnant_product, remnant_area, pieces=1),
                'area_sqm': remnant_area,
                'pieces': 1,
                'finish_result': _('Retazo aprovechable'),
            })
            created += 1

        if loss_area and float_compare(loss_area, 0.0, precision_digits=precision) > 0:
            self._create_output_line({
                'input_line_id': False,
                'output_type': 'scrap',
                'product_id': False,
                'lot_name': False,
                'qty_out': 0.0,
                'area_sqm': loss_area,
                'pieces': 0,
                'finish_result': _('Merma planeada'),
            })
            created += 1

        return created

    def _apply_progress_log_to_main_output(self):
        """Alinea las salidas productivas con lo declarado en la bitácora.

        - Modo `slab_finish` / `rework` (1:1): cancela las salidas huérfanas
          (sin entrada vinculada o asociadas a placas no registradas en la
          bitácora) y los duplicados. Para cada placa usada se garantiza una
          única salida activa con `qty_out` y `area_sqm` válidos, recalculados
          desde la entrada cuando vienen en cero (típico tras edición manual
          o cuando el flujo creó salidas sin onchange).
        - Modo `slab_cut` / `format_process` (agregado): suma `area_sqm` de
          todas las corridas y la escribe en la salida principal; cancela el
          resto de salidas útiles para evitar duplicidad.
        """
        self.ensure_one()
        if self.operation_mode in ('slab_finish', 'rework'):
            if not self.progress_log_ids:
                return False
            return self._sync_finish_outputs_with_used_inputs()

        total_log_area = sum(log.area_sqm for log in self.progress_log_ids)
        if total_log_area <= 0.0:
            return False

        main_outputs = self._get_active_output_lines().filtered(
            lambda l: l.output_type in ('format_piece', 'finished_slab')
            and l.state not in ('produced', 'received', 'scrapped')
        )
        if not main_outputs:
            return False

        primary = main_outputs[:1]
        product = primary.product_id
        qty_out = total_log_area if (product and self._product_uom_is_area(product)) else primary.qty_out
        primary.write({
            'area_sqm': total_log_area,
            'qty_out': qty_out,
        })
        extras = main_outputs - primary
        if extras:
            extras.write({'state': 'cancelled'})
        return total_log_area

    def _ensure_residual_scrap_line(self):
        """Cierra el balance de m² creando/actualizando una línea scrap automática.

        Calcula delta = área_entrada − área_útil − área_retazos − área_merma_manual
        y materializa esa diferencia como una línea de salida scrap marcada con
        RESIDUAL_SCRAP_TAG (para distinguirla de la merma capturada a mano).

        - Si delta > 0 y no existe línea residual: la crea.
        - Si delta > 0 y existe: actualiza su área.
        - Si delta <= 0 y existe: la borra.
        - No toca líneas ya consolidadas (state producido/recibido/scrap).
        - No aplica en modo acabado/reproceso (esos modos son 1:1).
        """
        self.ensure_one()
        if self.operation_mode in ('slab_finish', 'rework'):
            return 0.0

        precision = self.env['decimal.precision'].precision_get('Product Unit of Measure') or 4
        self._normalize_input_area_values()
        used_inputs = self._get_used_input_lines()
        active_outputs = self._get_active_output_lines()

        input_area = sum(self._input_line_area(line) for line in used_inputs)
        useful_area = sum(
            self._output_line_area(line)
            for line in active_outputs
            if line.output_type in ('finished_slab', 'format_piece')
        )
        remnant_area = sum(
            self._output_line_area(line)
            for line in active_outputs
            if line.output_type == 'remnant'
        )
        manual_scrap = active_outputs.filtered(
            lambda l: l.output_type in ('scrap', 'rejected')
            and (l.finish_result or '') != RESIDUAL_SCRAP_TAG
        )
        manual_scrap_area = sum(self._output_line_area(line) for line in manual_scrap)

        delta = input_area - useful_area - remnant_area - manual_scrap_area

        residual = active_outputs.filtered(
            lambda l: l.output_type == 'scrap'
            and (l.finish_result or '') == RESIDUAL_SCRAP_TAG
        )
        locked = residual.filtered(lambda l: l.state in ('produced', 'received', 'scrapped'))
        if locked:
            return delta

        if float_compare(delta, 0.0, precision_digits=precision) > 0:
            if residual:
                residual.write({
                    'area_sqm': delta,
                    'qty_out': 0.0,
                    'pieces': 0,
                })
            else:
                self._create_output_line({
                    'output_type': 'scrap',
                    'product_id': False,
                    'lot_name': False,
                    'qty_out': 0.0,
                    'area_sqm': delta,
                    'pieces': 0,
                    'finish_result': RESIDUAL_SCRAP_TAG,
                })
        elif residual:
            residual.unlink()

        return delta

    def _auto_generate_outputs(self):
        """Genera salidas sugeridas automáticamente al confirmar la orden.

        Acabado/reproceso: una salida 1:1 por cada entrada.
        Corte/formato: una salida útil + retazo + merma planeada (si aplica).
        El usuario editará las salidas reales antes de declarar el resultado.
        """
        self.ensure_one()
        if not self._get_active_input_lines():
            raise UserError(_('Agrega al menos una línea de entrada antes de confirmar la orden.'))
        self._ensure_default_locations()
        # Re-sincronizar el modo operativo con el proceso por si el campo
        # quedó desactualizado (órdenes creadas antes del fix, o defaults de
        # contexto que sobrescribieron el compute).
        if self.process_id and self.process_id.default_operation_mode and self.operation_mode != self.process_id.default_operation_mode:
            self.operation_mode = self.process_id.default_operation_mode
        if self.operation_mode in ('slab_cut', 'format_process'):
            return self._generate_cut_or_format_outputs()
        return self._generate_finish_like_outputs()


    def _validate_input_lines(self):
        precision = self.env['decimal.precision'].precision_get('Product Unit of Measure') or 4
        for rec in self:
            if not rec.input_line_ids.filtered(lambda l: l.state != 'cancelled'):
                raise ValidationError(_('La orden %s debe tener al menos una línea de entrada.') % rec.name)

            seen_lots = set()
            for line in rec.input_line_ids.filtered(lambda l: l.state != 'cancelled'):
                if not line.product_id:
                    raise ValidationError(_('Todas las líneas de entrada deben tener producto.'))
                if not line.lot_id:
                    raise ValidationError(_('Todas las líneas de entrada deben tener lote/placa.'))
                if line.lot_id.product_id and line.lot_id.product_id != line.product_id:
                    raise ValidationError(_(
                        'El lote %(lot)s pertenece al producto %(lot_product)s, no al producto %(line_product)s.'
                    ) % {
                        'lot': line.lot_id.name,
                        'lot_product': line.lot_id.product_id.display_name,
                        'line_product': line.product_id.display_name,
                    })
                if float_compare(line.qty_in, 0.0, precision_digits=precision) <= 0:
                    raise ValidationError(_('La línea %s debe tener cantidad de entrada mayor a cero.') % line.display_name)
                if line.lot_id.id in seen_lots:
                    raise ValidationError(_('El lote/placa %s está duplicado dentro de la misma orden.') % line.lot_id.name)
                seen_lots.add(line.lot_id.id)

                if not line.is_consumed:
                    available = rec._get_available_qty_for_lot(line.product_id, line.lot_id, rec.location_src_id)
                    if float_compare(available, line.qty_in, precision_digits=precision) < 0:
                        raise ValidationError(_(
                            'Disponibilidad insuficiente para %(lot)s. Disponible real: %(available)s. Requerido: %(required)s.'
                        ) % {
                            'lot': line.lot_id.name,
                            'available': available,
                            'required': line.qty_in,
                        })

            lot_ids = list(seen_lots)
            if lot_ids:
                conflict = self.search([
                    ('id', '!=', rec.id),
                    ('state', 'in', ACTIVE_WORKSHOP_STATES),
                    ('input_line_ids.lot_id', 'in', lot_ids),
                ], limit=1)
                if conflict:
                    conflict_lots = conflict.input_line_ids.filtered(lambda l: l.lot_id.id in lot_ids).mapped('lot_id.name')
                    raise ValidationError(_(
                        'Hay placa(s)/lote(s) ya activos en otra orden de taller: %(lots)s. Orden conflictiva: %(order)s.'
                    ) % {
                        'lots': ', '.join(conflict_lots),
                        'order': conflict.name,
                    })

    def _validate_output_lines(self):
        """Valida salidas con criterio declarativo.

        En modo declarativo (corte/formato), la merma se calcula como el residual
        entre entrada y útil+retazos, así que ya NO se exige que el balance cuadre
        ni que la salida útil coincida con production_target_sqm. La merma residual
        se materializa después con _ensure_residual_scrap_line(). El requisito de
        "al menos una salida" se valida puntualmente en action_declare_result.
        """
        precision = self.env['decimal.precision'].precision_get('Product Unit of Measure') or 4
        for rec in self:
            active_inputs = rec._get_active_input_lines()
            active_outputs = rec._get_active_output_lines()

            if not active_outputs:
                continue

            if rec.operation_mode in ('slab_finish', 'rework'):
                # Con bitácora: validar solo las placas efectivamente usadas;
                # las no usadas ya fueron devueltas al stock y sus salidas
                # canceladas en _apply_progress_log_to_main_output.
                relevant_inputs = rec._get_used_input_lines() if rec.progress_log_ids else active_inputs
                for input_line in relevant_inputs:
                    outputs = active_outputs.filtered(lambda o: o.input_line_id == input_line)
                    if not outputs:
                        raise ValidationError(_('La entrada %s no tiene ninguna salida esperada.') % input_line.display_name)
            else:
                input_area = sum(rec._input_line_area(line) for line in active_inputs)
                if float_compare(input_area, 0.0, precision_digits=precision) <= 0:
                    raise ValidationError(_('Para corte/formato, las entradas deben tener área m² mayor a cero.'))

            for output in active_outputs:
                if output.input_line_id and output.input_line_id.order_id != rec:
                    raise ValidationError(_('La salida %s está ligada a una entrada de otra orden.') % output.display_name)

                if output.output_type not in ('scrap', 'rejected'):
                    if not output.product_id:
                        raise ValidationError(_('Las salidas productivas y retazos deben tener producto.'))
                    if float_compare(output.qty_out, 0.0, precision_digits=precision) <= 0:
                        raise ValidationError(_('La salida %s debe tener cantidad mayor a cero.') % output.display_name)
                    if float_compare(output.area_sqm or output.qty_out, 0.0, precision_digits=precision) <= 0:
                        raise ValidationError(_('La salida %s debe tener área m² mayor a cero.') % output.display_name)
                    if not output.lot_name and not output.lot_id:
                        if output.input_line_id:
                            base_name = rec._default_output_lot_name(output.input_line_id)
                            output.lot_name = rec._make_unique_lot_name(
                                base_name,
                                product=output.product_id,
                                exclude_output=output,
                            )
                        else:
                            output.lot_name = rec._get_compact_result_lot_name(
                                output_type=output.output_type,
                                product=output.product_id,
                                target_area=rec._output_line_area(output),
                                exclude_output=output,
                            )
                else:
                    if float_is_zero(output.area_sqm, precision_digits=4) and float_is_zero(output.qty_out, precision_digits=precision):
                        raise ValidationError(_('Las salidas de merma/rechazo deben indicar área o cantidad.'))

    def _validate_business_rules(self):
        for rec in self:
            rec._ensure_default_locations()
            if not rec.process_id:
                raise ValidationError(_('Selecciona un proceso.'))
            if not rec.location_src_id or not rec.location_workshop_id or not rec.location_dest_id:
                raise ValidationError(_('Define ubicación origen, ubicación taller y ubicación destino.'))
            rec._normalize_input_area_values()
            rec._validate_input_lines()
            rec._validate_output_lines()

    def action_confirm_workshop(self):
        """Paso 2: pre-llena salidas si faltan, valida reglas y consume el material.

        Consolida lo que antes eran cuatro botones (Validar, Confirmar, Enviar a
        taller, Iniciar): de borrador pasa directo a `in_workshop` creando el
        picking de consumo. La merma residual se calculará al declarar el
        resultado.
        """
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('Solo puedes confirmar al taller órdenes en borrador.'))
            if not rec._get_active_output_lines():
                rec._auto_generate_outputs()
            rec._validate_business_rules()
            pending_inputs = rec.input_line_ids.filtered(lambda l: l.state not in ('cancelled',) and not l.is_consumed)
            if pending_inputs:
                picking = rec._create_consume_picking(pending_inputs)
                rec.consume_picking_ids = [(4, picking.id)]
                pending_inputs.write({
                    'state': 'in_progress',
                    'is_consumed': True,
                    'consume_picking_id': picking.id,
                })
            rec.write({
                'state': 'in_workshop',
                'date_start': rec.date_start or fields.Datetime.now(),
            })
            rec.message_post(body=_('Orden confirmada y material enviado a taller.'))
        return True

    def action_declare_result(self):
        """Paso 3: declara el resultado real del taller y cierra la orden.

        Devuelve al stock origen las placas marcadas como no usadas, cuadra la
        merma residual sobre las realmente usadas, valida salidas, materializa
        el picking de producción y deja la orden en `done`.
        """
        for rec in self:
            if rec.state != 'in_workshop':
                raise UserError(_('Solo puedes declarar el resultado de órdenes en taller.'))

            if not rec.progress_log_ids:
                raise UserError(_(
                    'No puedes declarar el resultado sin registrar al menos una corrida '
                    'en la bitácora. Captura los lotes procesados y los m² obtenidos.'
                ))

            unused_inputs = rec.input_line_ids.filtered(
                lambda l: l.state not in ('cancelled',) and l.is_consumed and not l.is_used
            )
            if unused_inputs:
                return_picking = rec._create_return_picking(unused_inputs)
                rec.return_picking_ids = [(4, return_picking.id)]
                unused_inputs.write({
                    'state': 'pending',
                    'is_consumed': False,
                    'return_picking_id': return_picking.id,
                })

            if not rec.input_line_ids.filtered(lambda l: l.state not in ('cancelled',) and l.is_used and l.is_consumed):
                raise UserError(_(
                    'No puedes declarar el resultado: registra al menos un lote en la bitácora. '
                    'Si ninguna placa se procesó, cancela la orden en su lugar.'
                ))

            rec._apply_progress_log_to_main_output()
            rec._ensure_residual_scrap_line()
            rec._validate_business_rules()
            if not rec._get_active_output_lines():
                raise ValidationError(_('La orden %s debe tener al menos una salida registrada para declarar el resultado.') % rec.name)
            pending_outputs = rec.output_line_ids.filtered(lambda l: l.state not in ('produced', 'received', 'scrapped', 'cancelled'))
            stock_outputs = pending_outputs.filtered(lambda l: l.output_type not in ('scrap', 'rejected'))
            scrap_outputs = pending_outputs.filtered(lambda l: l.output_type in ('scrap', 'rejected'))

            if stock_outputs:
                picking = rec._create_produce_picking(stock_outputs)
                rec.produce_picking_ids = [(4, picking.id)]
                for output in stock_outputs:
                    output.write({
                        'state': 'received',
                        'produce_picking_id': picking.id,
                    })
                    rec._create_or_update_trace(output)

            for output in scrap_outputs:
                output.write({'state': 'scrapped'})
                rec._create_or_update_trace(output)

            rec._refresh_line_states()
            rec.write({'state': 'done', 'date_done': fields.Datetime.now()})
            if unused_inputs:
                rec.message_post(body=_(
                    '%(count)s placa(s) marcadas como no usadas fueron devueltas íntegras al stock origen.'
                ) % {'count': len(unused_inputs)})
            rec.message_post(body=_('Resultado declarado y orden terminada.'))
        return True

    def action_cancel(self):
        for rec in self:
            done_pickings = (rec.consume_picking_ids | rec.produce_picking_ids).filtered(lambda p: p.state == 'done')
            if done_pickings:
                raise UserError(_(
                    'No se puede cancelar la orden porque ya tiene movimientos de inventario validados. '
                    'Cancela o revierte los pickings manualmente si necesitas anular la operación.'
                ))
            rec.input_line_ids.write({'state': 'cancelled'})
            rec.output_line_ids.write({'state': 'cancelled'})
            rec.write({'state': 'cancel'})
        return True

    def action_draft(self):
        for rec in self:
            if rec.state != 'cancel':
                raise UserError(_('Solo puedes regresar a borrador una orden cancelada.'))
            rec.input_line_ids.write({'state': 'pending'})
            rec.output_line_ids.write({'state': 'draft'})
            rec.write({'state': 'draft'})
        return True

    def _refresh_line_states(self):
        for rec in self:
            active_outputs = rec.output_line_ids.filtered(lambda o: o.state != 'cancelled')

            if rec.operation_mode in ('slab_cut', 'format_process'):
                if active_outputs and all(o.output_type in ('scrap', 'rejected') for o in active_outputs):
                    rec.input_line_ids.filtered(lambda l: l.state not in ('cancelled',)).write({'state': 'rejected'})
                else:
                    rec.input_line_ids.filtered(lambda l: l.state not in ('cancelled',)).write({'state': 'done'})
                continue

            for input_line in rec.input_line_ids.filtered(lambda l: l.state not in ('cancelled',)):
                outputs = active_outputs.filtered(lambda o: o.input_line_id == input_line)
                if outputs and all(o.output_type in ('scrap', 'rejected') for o in outputs):
                    input_line.state = 'rejected'
                else:
                    input_line.state = 'done'

    def _create_consume_picking(self, input_lines):
        self.ensure_one()
        move_specs = []
        for line in input_lines:
            move_specs.append({
                'product': line.product_id,
                'qty': line.qty_in,
                'lot': line.lot_id,
                'name': '%s - Consumo %s' % (self.name, line.lot_id.name),
            })
        return self._create_stock_picking(
            move_specs=move_specs,
            location_src=self.location_src_id,
            location_dest=self.location_workshop_id,
            origin='%s - Consumo taller' % self.name,
        )

    def _create_produce_picking(self, output_lines):
        self.ensure_one()
        move_specs = []
        for line in output_lines:
            lot = line._ensure_result_lot()
            move_specs.append({
                'product': line.product_id,
                'qty': line.qty_out,
                'lot': lot,
                'name': '%s - Producción %s' % (self.name, lot.name),
            })
        return self._create_stock_picking(
            move_specs=move_specs,
            location_src=self.location_workshop_id,
            location_dest=self.location_dest_id,
            origin='%s - Producción taller' % self.name,
        )

    def _create_return_picking(self, input_lines):
        """Devuelve placas no usadas del taller al stock origen.

        Crea un picking inverso (taller → origen) por las cantidades capturadas
        en las líneas de entrada marcadas como no usadas. Se invoca al declarar
        el resultado para que las placas que entraron al taller pero no se
        procesaron vuelvan al inventario disponible.

        Se pasa `skip_duplicate_lot_validation` porque el lote a devolver
        legítimamente vivió antes en otra operación (la OT que lo creó o que
        lo consumió); el guardia de duplicidad de lote no debe bloquear esta
        devolución planeada del propio flujo de taller.
        """
        self.ensure_one()
        move_specs = []
        for line in input_lines:
            move_specs.append({
                'product': line.product_id,
                'qty': line.qty_in,
                'lot': line.lot_id,
                'name': '%s - Devolución %s' % (self.name, line.lot_id.name),
            })
        return self.with_context(
            skip_duplicate_lot_validation=True,
            skip_hold_validation=True,
        )._create_stock_picking(
            move_specs=move_specs,
            location_src=self.location_workshop_id,
            location_dest=self.location_src_id,
            origin='%s - Devolución taller' % self.name,
        )

    def _create_stock_picking(self, move_specs, location_src, location_dest, origin):
        self.ensure_one()
        if not move_specs:
            raise UserError(_('No hay movimientos para crear.'))
        picking_type = self._get_internal_picking_type()
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': location_src.id,
            'location_dest_id': location_dest.id,
            'origin': origin,
            'company_id': self.company_id.id,
        })
        _logger.info('WORKSHOP picking created: %s', picking.name)

        move_fields = self.env['stock.move'].fields_get()
        moves_with_specs = []

        for spec in move_specs:
            product = spec['product']
            qty = spec['qty']
            move_vals = {
                'picking_id': picking.id,
                'product_id': product.id,
                'location_id': location_src.id,
                'location_dest_id': location_dest.id,
                'company_id': self.company_id.id,
            }
            if 'name' in move_fields:
                move_vals['name'] = spec.get('name') or product.display_name
            elif 'description' in move_fields:
                move_vals['description'] = spec.get('name') or product.display_name

            if 'product_uom_id' in move_fields:
                move_vals['product_uom_id'] = product.uom_id.id
            elif 'product_uom' in move_fields:
                move_vals['product_uom'] = product.uom_id.id

            if 'product_uom_qty' in move_fields:
                move_vals['product_uom_qty'] = qty
            elif 'quantity' in move_fields:
                move_vals['quantity'] = qty

            move = self.env['stock.move'].create(move_vals)
            moves_with_specs.append((move, spec))

        # Confirmar SIN merge (evita que _merge_moves borre stock.move y deje
        # referencias muertas → "Record does not exist") y SIN que la estrategia
        # WholeLot auto-reserve lotes arbitrarios en este picking interno.
        moves = self.env['stock.move'].concat(*[m for m, _s in moves_with_specs])
        moves.with_context(skip_whole_lot=True)._action_confirm(merge=False)

        move_line_fields = self.env['stock.move.line'].fields_get()

        for move, spec in moves_with_specs:
            # Limpiamos cualquier línea auto-reservada y forzamos el lote exacto de taller.
            move.move_line_ids.unlink()
            lot = spec.get('lot')
            qty = spec.get('qty')
            ml_vals = {
                'move_id': move.id,
                'picking_id': picking.id,
                'product_id': spec['product'].id,
                'lot_id': lot.id if lot else False,
                'location_id': location_src.id,
                'location_dest_id': location_dest.id,
                'company_id': self.company_id.id,
            }
            if 'product_uom_id' in move_line_fields:
                ml_vals['product_uom_id'] = spec['product'].uom_id.id
            # Odoo 19: la cantidad realizada se captura en quantity (qty_done quedó obsoleto).
            if 'quantity' in move_line_fields:
                ml_vals['quantity'] = qty
            elif 'qty_done' in move_line_fields:
                ml_vals['qty_done'] = qty
            if 'picked' in move_line_fields:
                ml_vals['picked'] = True

            self.env['stock.move.line'].create(ml_vals)
            if 'picked' in move._fields:
                move.picked = True

        self._validate_picking(picking)
        _logger.info('WORKSHOP picking validated: %s state=%s', picking.name, picking.state)
        return picking

    def _validate_picking(self, picking):
        try:
            res = picking.with_context(
                skip_whole_lot=True,
                skip_backorder=True,
                skip_immediate=True,
                skip_sms=True,
                cancel_backorder=True,
            ).button_validate()
            if isinstance(res, dict) and res.get('res_model'):
                wizard_model = res['res_model']
                wizard_id = res.get('res_id')
                wizard = self.env[wizard_model].browse(wizard_id) if wizard_id else self.env[wizard_model].with_context(
                    **res.get('context', {})
                ).create({})
                if wizard_model == 'stock.immediate.transfer' and hasattr(wizard, 'process'):
                    wizard.process()
                elif wizard_model == 'stock.backorder.confirmation':
                    if hasattr(wizard, 'process_cancel_backorder'):
                        wizard.process_cancel_backorder()
                    elif hasattr(wizard, 'process'):
                        wizard.process()
        except Exception as err:
            _logger.exception('WORKSHOP button_validate failed for %s', picking.name)
            raise UserError(_('No se pudo validar el picking %s. Error: %s') % (picking.name, err))

    def _create_or_update_trace(self, output_line):
        self.ensure_one()
        Trace = self.env['workshop.transformation.trace']
        existing_traces = Trace.search([('output_line_id', '=', output_line.id)])
        existing_traces.unlink()

        if output_line.input_line_id:
            input_lines = output_line.input_line_id
        else:
            input_lines = self._get_active_input_lines()

        if not input_lines:
            return False

        total_input_area = sum(self._input_line_area(line) for line in input_lines) or 0.0
        if not total_input_area:
            total_input_area = sum(input_lines.mapped('qty_in')) or 1.0

        output_area = self._output_line_area(output_line)
        output_qty = output_line.qty_out or 0.0

        for input_line in input_lines:
            input_area = self._input_line_area(input_line)
            share = (input_area / total_input_area) if total_input_area else 0.0
            if not share and len(input_lines) == 1:
                share = 1.0

            Trace.create({
                'order_id': self.id,
                'input_line_id': input_line.id,
                'output_line_id': output_line.id,
                'source_product_id': input_line.product_id.id,
                'source_lot_id': input_line.lot_id.id,
                'result_product_id': output_line.product_id.id if output_line.product_id else False,
                'result_lot_id': output_line.lot_id.id if output_line.lot_id else False,
                'process_id': self.process_id.id,
                'qty_in': input_line.qty_in,
                'qty_out': output_qty * share,
                'area_in_sqm': input_area,
                'area_out_sqm': output_area * share if output_line.output_type not in ('scrap', 'rejected') else 0.0,
                'loss_sqm': output_area * share if output_line.output_type in ('scrap', 'rejected') else 0.0,
                'output_type': output_line.output_type,
                'date_done': fields.Datetime.now(),
                'responsible_id': self.responsible_id.id,
            })
        return True

    def action_normalize_result_lots(self):
        """Renombra y completa metadata de lotes resultado ya generados.

        Útil para órdenes donde el lote salió como T-TALLER/...-OBJ o ...-RET
        sin color, bloque, tipo, origen o pedimento. No mueve inventario; solo
        actualiza stock.lot y la referencia de la línea de salida.
        """
        for rec in self:
            updated = 0
            for output in rec._get_active_output_lines().filtered(lambda l: l.output_type not in ('scrap', 'rejected') and l.product_id):
                target_area = rec._output_line_area(output)
                lot = output.lot_id
                new_name = rec._get_compact_result_lot_name(
                    output_type=output.output_type,
                    product=output.product_id,
                    target_area=target_area,
                    exclude_output=output,
                    exclude_lot=lot,
                )
                if lot:
                    output._sync_result_lot_metadata(lot, force_name=new_name)
                    output.lot_name = new_name
                else:
                    output.lot_name = new_name
                    output._ensure_result_lot()
                updated += 1
            if updated:
                rec.message_post(body=_('Se normalizaron %(count)s lote(s) resultado con nombre corto y metadata heredada.') % {'count': updated})
        return True

    def action_print_pick_report(self):
        """Imprime la orden de recolección de placas para enviar a taller."""
        self.ensure_one()
        return self.env.ref('stone_workshop.action_report_workshop_pick').report_action(self)

    def action_view_consume_pickings(self):
        self.ensure_one()
        return self._action_view_records('stock.picking', self.consume_picking_ids, _('Pickings de consumo'))

    def action_view_produce_pickings(self):
        self.ensure_one()
        return self._action_view_records('stock.picking', self.produce_picking_ids, _('Pickings de producción'))

    def action_view_return_pickings(self):
        self.ensure_one()
        return self._action_view_records('stock.picking', self.return_picking_ids, _('Pickings de devolución'))

    def action_view_traces(self):
        self.ensure_one()
        return self._action_view_records('workshop.transformation.trace', self.trace_ids, _('Trazabilidad'))

    def _action_view_records(self, model, records, name):
        action = {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': model,
            'view_mode': 'list,form',
            'domain': [('id', 'in', records.ids)],
        }
        if len(records) == 1:
            action.update({'view_mode': 'form', 'res_id': records.id})
        return action


class WorkshopInputLine(models.Model):
    _name = 'workshop.input.line'
    _description = 'Entrada de Taller de Piedra'
    _order = 'sequence, id'
    _rec_name = 'name'

    sequence = fields.Integer(default=10)
    order_id = fields.Many2one('workshop.order', string='Orden', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='order_id.company_id', store=True, readonly=True)
    operation_mode = fields.Selection(related='order_id.operation_mode', store=True, readonly=True)

    material_type = fields.Selection([
        ('slab', 'Placa'),
        ('format', 'Formato'),
        ('pallet', 'Pallet'),
        ('remnant', 'Retazo'),
    ], string='Tipo material', required=True, default='slab')
    product_id = fields.Many2one('product.product', string='Producto entrada', required=True, domain=[('tracking', '!=', 'none')])
    lot_id = fields.Many2one('stock.lot', string='Lote / placa entrada', required=True, domain="[('product_id', '=', product_id)]")
    product_out_id = fields.Many2one(
        'product.product',
        string='Producto salida específico (obsoleto)',
        domain=[('tracking', '!=', 'none')],
        help='Campo técnico conservado por compatibilidad histórica. '
             'La salida ahora se controla desde la orden para evitar captura redundante por línea.',
    )

    qty_in = fields.Float(string='Cantidad entrada', digits=(12, 4), default=1.0)
    available_qty = fields.Float(string='Disponible real', compute='_compute_available_qty', digits=(12, 4))
    area_sqm = fields.Float(string='Área m²', digits=(12, 4))
    width_cm = fields.Float(string='Ancho cm', digits=(12, 2))
    height_cm = fields.Float(string='Alto cm', digits=(12, 2))
    thickness_cm = fields.Float(string='Espesor cm', digits=(12, 2))
    pieces = fields.Integer(string='Piezas', default=1)
    block_name = fields.Char(string='Bloque')
    tone = fields.Char(string='Tono')
    current_finish = fields.Char(string='Acabado actual')
    location_id = fields.Many2one('stock.location', string='Ubicación actual')
    reserved_origin = fields.Char(string='Compromiso comercial')

    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('reserved_for_workshop', 'Reservada para taller'),
        ('in_progress', 'En taller'),
        ('done', 'Terminada'),
        ('rejected', 'Rechazada'),
        ('cancelled', 'Cancelada'),
    ], string='Estado', default='pending')
    is_consumed = fields.Boolean(string='Consumida en taller', copy=False)
    progress_log_ids = fields.Many2many(
        'workshop.progress.log',
        'workshop_progress_log_input_line_rel',
        'input_line_id',
        'log_id',
        string='Corridas',
        help='Corridas de bitácora donde se registró el consumo real de este lote.',
    )
    is_used = fields.Boolean(
        string='Usada en proceso',
        compute='_compute_is_used',
        store=True,
        help='Se marca automáticamente cuando el lote se registra en una corrida de la bitácora. '
             'Los lotes nunca registrados se devuelven al stock al declarar el resultado.',
    )
    consume_picking_id = fields.Many2one('stock.picking', string='Picking consumo', readonly=True, copy=False)
    return_picking_id = fields.Many2one('stock.picking', string='Picking devolución', readonly=True, copy=False)
    name = fields.Char(string='Descripción', compute='_compute_name', store=True)

    @api.depends('progress_log_ids')
    def _compute_is_used(self):
        for line in self:
            line.is_used = bool(line.progress_log_ids)

    @api.model_create_multi
    def create(self, vals_list):
        """Blindaje para líneas creadas desde el selector visual.

        El widget reconstruye input_line_ids con comandos One2many. En algunas
        versiones del cliente web, los Many2one requeridos pueden llegar al ORM
        sin product_id aunque el lote esté seleccionado en pantalla. Antes de
        llamar a super(), se completa el producto desde el lote o desde el
        producto base de la orden para evitar el error de validación por campo
        requerido.
        """
        clean_vals_list = []
        for vals in vals_list:
            clean_vals = dict(vals or {})
            clean_vals = self._workshop_prepare_required_values(clean_vals)
            clean_vals_list.append(clean_vals)
        return super().create(clean_vals_list)

    def write(self, vals):
        clean_vals = dict(vals or {})
        if 'lot_id' in clean_vals or 'product_id' in clean_vals or 'qty_in' in clean_vals or 'area_sqm' in clean_vals:
            for line in self:
                scoped_vals = line._workshop_prepare_required_values(dict(clean_vals), existing_line=line)
                super(WorkshopInputLine, line).write(scoped_vals)
            return True
        return super().write(clean_vals)

    @api.model
    def _workshop_prepare_required_values(self, vals, existing_line=False):
        for m2o_name in ('order_id', 'product_id', 'lot_id', 'location_id', 'consume_picking_id'):
            raw_value = vals.get(m2o_name)
            if isinstance(raw_value, (list, tuple)):
                vals[m2o_name] = raw_value[0] if raw_value else False

        lot = False
        lot_value = vals.get('lot_id') if 'lot_id' in vals else (existing_line.lot_id.id if existing_line else False)
        if isinstance(lot_value, (list, tuple)):
            lot_value = lot_value[0] if lot_value else False
        if lot_value:
            lot = self.env['stock.lot'].browse(int(lot_value)).exists()

        order = False
        order_value = vals.get('order_id') if 'order_id' in vals else (existing_line.order_id.id if existing_line else False)
        if isinstance(order_value, (list, tuple)):
            order_value = order_value[0] if order_value else False
        if order_value:
            order = self.env['workshop.order'].browse(int(order_value)).exists()

        product_value = vals.get('product_id') if 'product_id' in vals else False
        if isinstance(product_value, (list, tuple)):
            product_value = product_value[0] if product_value else False

        if product_value:
            vals['product_id'] = int(product_value)
        else:
            if lot and lot.product_id:
                vals['product_id'] = lot.product_id.id
            elif order and order.input_product_id:
                vals['product_id'] = order.input_product_id.id
            elif existing_line and existing_line.product_id:
                vals['product_id'] = existing_line.product_id.id

        qty = vals.get('qty_in')
        area = vals.get('area_sqm')
        try:
            qty_float = float(qty or 0.0)
        except (TypeError, ValueError):
            qty_float = 0.0
        try:
            area_float = float(area or 0.0)
        except (TypeError, ValueError):
            area_float = 0.0

        product = False
        product_id = vals.get('product_id') or (existing_line.product_id.id if existing_line and existing_line.product_id else False)
        if product_id:
            product = self.env['product.product'].browse(int(product_id)).exists()

        is_area_uom = False
        if product and product.uom_id:
            if order:
                is_area_uom = order._product_uom_is_area(product)
            elif existing_line and existing_line.order_id:
                is_area_uom = existing_line.order_id._product_uom_is_area(product)
            else:
                uom = product.uom_id
                uom_text = ' '.join(filter(None, [uom.name or '', uom.display_name or ''])).lower()
                is_area_uom = any(token in uom_text for token in (
                    'm²', 'm2', 'm^2', 'sqm', 'sq m', 'metro cuadrado', 'metros cuadrados', 'superficie', 'area', 'área'
                ))

        if qty_float and (not area_float or (is_area_uom and area_float < (qty_float * 0.25))):
            vals['area_sqm'] = qty_float

        return vals

    @api.depends('lot_id', 'product_id', 'area_sqm')
    def _compute_name(self):
        for line in self:
            if line.lot_id:
                line.name = '%s / %s' % (line.product_id.display_name or '', line.lot_id.name)
            else:
                line.name = line.product_id.display_name or _('Entrada')

    @api.depends('lot_id', 'product_id', 'order_id.location_src_id')
    def _compute_available_qty(self):
        for line in self:
            if line.order_id and line.product_id and line.lot_id:
                line.available_qty = line.order_id._get_available_qty_for_lot(line.product_id, line.lot_id, line.order_id.location_src_id)
            else:
                line.available_qty = 0.0

    @api.onchange('lot_id')
    def _onchange_lot_id(self):
        for line in self:
            if not line.lot_id:
                continue
            if line.lot_id.product_id:
                line.product_id = line.lot_id.product_id.id
            line._pull_lot_metadata()
            if line.available_qty:
                line.qty_in = line.available_qty
            if line.qty_in:
                is_area_uom = False
                if line.product_id and line.product_id.uom_id:
                    if line.order_id:
                        is_area_uom = line.order_id._product_uom_is_area(line.product_id)
                    else:
                        uom = line.product_id.uom_id
                        uom_text = ' '.join(filter(None, [uom.name or '', uom.display_name or ''])).lower()
                        is_area_uom = any(token in uom_text for token in (
                            'm²', 'm2', 'm^2', 'sqm', 'sq m', 'metro cuadrado', 'metros cuadrados', 'superficie', 'area', 'área'
                        ))
                if not line.area_sqm or (is_area_uom and line.area_sqm < (line.qty_in * 0.25)):
                    line.area_sqm = line.qty_in

    @api.onchange('width_cm', 'height_cm', 'pieces')
    def _onchange_dimensions(self):
        for line in self:
            if line.width_cm and line.height_cm and line.pieces:
                width = float(line.width_cm or 0.0)
                height = float(line.height_cm or 0.0)
                pieces = int(line.pieces or 1)
                if max(width, height) <= 20.0:
                    line.area_sqm = width * height * pieces
                else:
                    line.area_sqm = (width / 100.0) * (height / 100.0) * pieces

    def _lot_value(self, *field_names):
        self.ensure_one()
        lot = self.lot_id
        for fname in field_names:
            if lot and fname in lot._fields:
                value = lot[fname]
                if hasattr(value, 'display_name'):
                    return value.display_name
                return value
        return False

    def _pull_lot_metadata(self):
        for line in self:
            width = line._lot_value('marble_width', 'width_cm', 'width', 'stone_width', 'x_width_cm')
            height = line._lot_value('marble_height', 'height_cm', 'height', 'stone_height', 'x_height_cm')
            thickness = line._lot_value('thickness_cm', 'thickness', 'marble_thickness', 'x_thickness_cm')
            area = line._lot_value('marble_sqm', 'area_sqm', 'sqm', 'x_area_sqm')
            block = line._lot_value('lot_general', 'block_name', 'block', 'bloque', 'x_block', 'x_bloque')
            tone = line._lot_value('tone', 'tono', 'x_tone', 'x_tono')
            finish = line._lot_value('current_finish', 'finish', 'finish_id', 'x_finish')

            line.width_cm = float(width or 0.0) if isinstance(width, (int, float)) else line.width_cm
            line.height_cm = float(height or 0.0) if isinstance(height, (int, float)) else line.height_cm
            line.thickness_cm = float(thickness or 0.0) if isinstance(thickness, (int, float)) else line.thickness_cm
            if isinstance(area, (int, float)) and area:
                line.area_sqm = area
            line.block_name = block or line.block_name
            line.tone = tone or line.tone
            line.current_finish = finish or line.current_finish

            if line.order_id and line.lot_id:
                quant = self.env['stock.quant'].search([
                    ('product_id', '=', line.product_id.id),
                    ('lot_id', '=', line.lot_id.id),
                    ('location_id.usage', '=', 'internal'),
                    ('quantity', '>', 0),
                ], limit=1, order='quantity desc')
                if quant:
                    line.location_id = quant.location_id.id


class WorkshopOutputLine(models.Model):
    _name = 'workshop.output.line'
    _description = 'Salida de Taller de Piedra'
    _order = 'sequence, id'
    _rec_name = 'name'

    sequence = fields.Integer(default=10)
    order_id = fields.Many2one('workshop.order', string='Orden', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='order_id.company_id', store=True, readonly=True)
    input_line_id = fields.Many2one('workshop.input.line', string='Entrada origen', required=False, ondelete='cascade', help='Opcional. En corte/formato agregado, la salida puede representar varias placas de entrada.')
    source_lot_id = fields.Many2one(related='input_line_id.lot_id', string='Lote origen', store=True, readonly=True)

    output_type = fields.Selection([
        ('finished_slab', 'Placa terminada'),
        ('format_piece', 'Formato / pieza'),
        ('remnant', 'Retazo aprovechable'),
        ('scrap', 'Merma'),
        ('rejected', 'Rechazado'),
    ], string='Tipo salida', required=True, default='finished_slab')
    product_id = fields.Many2one('product.product', string='Producto salida', domain=[('tracking', '!=', 'none')])
    lot_name = fields.Char(string='Lote salida')
    lot_id = fields.Many2one('stock.lot', string='Lote creado', readonly=True, copy=False)
    qty_out = fields.Float(string='Cantidad salida', digits=(12, 4), default=1.0)
    area_sqm = fields.Float(string='Área m²', digits=(12, 4))
    width_cm = fields.Float(string='Ancho cm', digits=(12, 2))
    height_cm = fields.Float(string='Alto cm', digits=(12, 2))
    thickness_cm = fields.Float(string='Espesor cm', digits=(12, 2))
    pieces = fields.Integer(string='Piezas', default=1)
    finish_result = fields.Char(string='Acabado resultado')
    location_dest_id = fields.Many2one('stock.location', string='Ubicación destino', domain=[('usage', '=', 'internal')])
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('ready_to_produce', 'Lista para producir'),
        ('produced', 'Producida'),
        ('received', 'Recibida'),
        ('scrapped', 'Merma/Rechazo'),
        ('cancelled', 'Cancelada'),
    ], string='Estado', default='draft')
    produce_picking_id = fields.Many2one('stock.picking', string='Picking producción', readonly=True, copy=False)
    name = fields.Char(string='Descripción', compute='_compute_name', store=True)

    @api.depends('output_type', 'lot_name', 'product_id')
    def _compute_name(self):
        labels = dict(self._fields['output_type'].selection)
        for line in self:
            if line.lot_name:
                line.name = '%s / %s' % (labels.get(line.output_type, ''), line.lot_name)
            elif line.product_id:
                line.name = '%s / %s' % (labels.get(line.output_type, ''), line.product_id.display_name)
            else:
                line.name = labels.get(line.output_type, _('Salida'))

    @api.onchange('input_line_id')
    def _onchange_input_line_id(self):
        for line in self:
            if not line.input_line_id:
                continue
            order = line.order_id or line.input_line_id.order_id
            line.order_id = order.id
            line.product_id = line.product_id or order.default_product_out_id or line.input_line_id.product_id
            line.area_sqm = line.area_sqm or line.input_line_id.area_sqm
            if order and line.product_id and line.area_sqm and order._product_uom_is_area(line.product_id):
                line.qty_out = line.area_sqm
            else:
                line.qty_out = line.qty_out or line.input_line_id.qty_in
            line.width_cm = line.width_cm or line.input_line_id.width_cm
            line.height_cm = line.height_cm or line.input_line_id.height_cm
            line.thickness_cm = line.thickness_cm or line.input_line_id.thickness_cm
            line.location_dest_id = line.location_dest_id or order.location_dest_id
            if not line.lot_name and line.output_type not in ('scrap', 'rejected'):
                base = '%s-%s' % (line.input_line_id.lot_id.name, order.process_id.code or 'PROC')
                line.lot_name = order._make_unique_lot_name(base, product=line.product_id, exclude_output=line)

    @api.onchange('output_type')
    def _onchange_output_type(self):
        for line in self:
            if line.output_type in ('scrap', 'rejected'):
                line.product_id = False
                line.lot_name = False
                line.qty_out = 0.0
            elif line.input_line_id:
                line._onchange_input_line_id()

    @api.onchange('width_cm', 'height_cm', 'pieces')
    def _onchange_dimensions(self):
        for line in self:
            if line.width_cm and line.height_cm and line.pieces:
                width = float(line.width_cm or 0.0)
                height = float(line.height_cm or 0.0)
                pieces = int(line.pieces or 1)
                if max(width, height) <= 20.0:
                    line.area_sqm = width * height * pieces
                else:
                    line.area_sqm = (width / 100.0) * (height / 100.0) * pieces
                line._onchange_area_or_product_qty()

    @api.onchange('product_id', 'area_sqm', 'pieces', 'output_type')
    def _onchange_area_or_product_qty(self):
        for line in self:
            if line.output_type in ('scrap', 'rejected'):
                line.qty_out = 0.0
                continue
            order = line.order_id or (line.input_line_id.order_id if line.input_line_id else False)
            if order and line.product_id and line.area_sqm and order._product_uom_is_area(line.product_id):
                line.qty_out = line.area_sqm
            elif not line.qty_out:
                line.qty_out = line.pieces or 1

    def _get_metadata_source_input_line(self):
        self.ensure_one()
        if self.input_line_id:
            return self.input_line_id
        if not self.order_id:
            return False
        return self.order_id._get_result_lot_source_line(
            output_type=self.output_type,
            target_area=self.order_id._output_line_area(self),
        )

    def _set_lot_field_value(self, vals, field_name, value):
        Lot = self.env['stock.lot']
        if field_name not in Lot._fields:
            return
        field = Lot._fields[field_name]
        if getattr(field, 'compute', False) and not getattr(field, 'inverse', False):
            return
        if field.type in ('one2many', 'reference'):
            return

        if field.type == 'many2one':
            if self._is_empty_lot_value(value):
                vals[field_name] = False
            elif hasattr(value, 'id'):
                vals[field_name] = value.id
            elif isinstance(value, int):
                vals[field_name] = value
            else:
                try:
                    vals[field_name] = int(value)
                except (TypeError, ValueError):
                    return
            return

        if field.type == 'many2many':
            if hasattr(value, 'ids'):
                vals[field_name] = [(6, 0, value.ids)]
            elif self._is_empty_lot_value(value):
                vals[field_name] = [(6, 0, [])]
            elif isinstance(value, (list, tuple, set)):
                clean_ids = []
                for item in value:
                    if hasattr(item, 'id'):
                        clean_ids.append(item.id)
                    else:
                        try:
                            clean_ids.append(int(item))
                        except (TypeError, ValueError):
                            continue
                vals[field_name] = [(6, 0, clean_ids)]
            return

        if self._is_empty_lot_value(value):
            vals[field_name] = False
            return

        if field.type in ('char', 'text', 'html'):
            vals[field_name] = self._lot_value_to_text(value)
        elif field.type == 'selection':
            selection_keys = []
            if isinstance(field.selection, (list, tuple)):
                selection_keys = [item[0] for item in field.selection]
            if value in selection_keys:
                vals[field_name] = value
            elif str(value) in selection_keys:
                vals[field_name] = str(value)
            else:
                return
        elif field.type == 'integer':
            try:
                vals[field_name] = int(float(value))
            except (TypeError, ValueError):
                return
        elif field.type in ('float', 'monetary'):
            try:
                vals[field_name] = float(value)
            except (TypeError, ValueError):
                return
        elif field.type == 'boolean':
            vals[field_name] = bool(value)
        else:
            vals[field_name] = value

    def _set_lot_material_type(self, vals, material_type):
        label_map = {
            'placa': 'Placa',
            'formato': 'Formato',
            'retazo': 'Retazo',
        }
        Lot = self.env['stock.lot']
        for field_name in ('x_tipo', 'tipo', 'material_type'):
            if field_name not in Lot._fields:
                continue
            field = Lot._fields[field_name]
            if getattr(field, 'compute', False) and not getattr(field, 'inverse', False):
                continue
            if field.type == 'selection' and isinstance(field.selection, (list, tuple)):
                keys = [item[0] for item in field.selection]
                if material_type in keys:
                    vals[field_name] = material_type
                elif label_map.get(material_type) in keys:
                    vals[field_name] = label_map[material_type]
            elif field.type in ('char', 'text', 'html'):
                vals[field_name] = label_map.get(material_type, material_type)

    def _lot_metadata_aliases(self):
        return {
            'color': (
                'x_color', 'color', 'color_id', 'x_color_id', 'stone_color',
                'product_color', 'x_tono_color', 'x_nombre_color',
            ),
            'container': (
                'x_contenedor', 'contenedor', 'container', 'container_id',
                'x_container', 'x_container_id', 'lot_container', 'x_lote_contenedor',
                'x_contenedor_id', 'container_number', 'x_container_number',
                'x_no_contenedor', 'numero_contenedor', 'x_numero_contenedor',
            ),
            'origin': (
                'x_origen', 'origin', 'x_origin', 'country_id', 'x_origen_id',
                'x_pais_origen', 'origin_country_id', 'x_country_id',
            ),
            'pedimento': (
                'x_pedimento', 'pedimento', 'x_pedimento_id', 'pedimento_id',
                'customs_entry', 'x_customs_entry', 'import_entry',
                'x_import_entry', 'x_numero_pedimento', 'numero_pedimento',
            ),
            'block': (
                'x_bloque', 'lot_general', 'block_name', 'block', 'bloque',
                'x_block', 'x_lot_general', 'x_bloque_id', 'block_id',
            ),
            'bundle': (
                'x_atado', 'atado', 'bundle', 'bundle_number', 'x_bundle',
                'x_bundle_number', 'pallet_count', 'x_pallet_count',
            ),
        }

    def _is_empty_lot_value(self, value):
        if value is False or value is None:
            return True
        if isinstance(value, str) and not value.strip():
            return True
        if hasattr(value, 'ids') and not value.ids:
            return True
        return False

    def _lot_value_to_text(self, value):
        if self._is_empty_lot_value(value):
            return ''
        if hasattr(value, 'ids'):
            return ', '.join(value.mapped('display_name'))
        if hasattr(value, 'display_name'):
            return value.display_name or ''
        return str(value)

    def _lot_field_is_image_or_photo(self, field_name, field):
        name = (field_name or '').lower()
        image_tokens = ('image', 'photo', 'picture', 'foto', 'fotografia', 'fotografía', 'avatar')
        return field.type == 'binary' or any(token in name for token in image_tokens)

    def _is_copyable_lot_metadata_field(self, field_name, field):
        if not field_name or not field:
            return False
        if self._lot_field_is_image_or_photo(field_name, field):
            return False
        if getattr(field, 'compute', False) and not getattr(field, 'inverse', False):
            return False
        if field.type in ('one2many', 'reference'):
            return False

        blocked_names = {
            'id', 'name', 'display_name', '__last_update',
            'product_id', 'product_tmpl_id', 'product_qty', 'product_uom_id',
            'company_id', 'quant_ids', 'create_uid', 'create_date', 'write_uid', 'write_date',
            'message_ids', 'message_follower_ids', 'message_partner_ids',
            'message_main_attachment_id', 'website_message_ids', 'activity_ids',
            'activity_user_id', 'activity_type_id', 'activity_date_deadline',
            'activity_summary', 'activity_exception_decoration', 'activity_exception_icon',
        }
        if field_name in blocked_names:
            return False

        blocked_prefixes = (
            'message_', 'activity_', 'rating_', 'access_', 'website_message_',
        )
        return not field_name.startswith(blocked_prefixes)

    def _copy_lot_metadata_from_source_lot(self, vals, source_lot):
        Lot = self.env['stock.lot']
        if not source_lot:
            return vals
        for field_name, source_field in source_lot._fields.items():
            if field_name not in Lot._fields:
                continue
            target_field = Lot._fields[field_name]
            if not self._is_copyable_lot_metadata_field(field_name, target_field):
                continue
            self._set_lot_field_value(vals, field_name, source_lot[field_name])
        return vals

    def _get_lot_field_value(self, lot, aliases, display=False):
        if not lot:
            return False
        for field_name in aliases or ():
            if field_name not in lot._fields:
                continue
            value = lot[field_name]
            if self._is_empty_lot_value(value):
                continue
            return self._lot_value_to_text(value) if display else value
        return False

    def _unique_text_values(self, values):
        result = []
        seen = set()
        for value in values or []:
            text_value = self._lot_value_to_text(value).strip()
            if not text_value:
                continue
            key = text_value.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(text_value)
        return result

    def _input_line_metadata_weight(self, input_line):
        if not input_line:
            return 0.0
        order = self.order_id or input_line.order_id
        if order:
            area = order._input_line_area(input_line)
            if area:
                return area
        return input_line.area_sqm or input_line.qty_in or 0.0

    def _get_weighted_input_value(self, input_lines, aliases):
        candidates = []
        for input_line in input_lines:
            value = self._get_lot_field_value(input_line.lot_id, aliases)
            if self._is_empty_lot_value(value):
                continue
            candidates.append((self._input_line_metadata_weight(input_line), input_line.id or 0, value))
        if not candidates:
            return False
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return candidates[0][2]

    def _get_common_input_value(self, input_lines, aliases):
        values = []
        raw_by_key = {}
        for input_line in input_lines:
            value = self._get_lot_field_value(input_line.lot_id, aliases)
            display_value = self._get_lot_field_value(input_line.lot_id, aliases, display=True)
            if self._is_empty_lot_value(value) or not display_value:
                continue
            key = display_value.strip().casefold()
            values.append(key)
            raw_by_key.setdefault(key, value)
        unique_keys = set(values)
        if len(unique_keys) == 1:
            return raw_by_key[next(iter(unique_keys))]
        return False

    def _get_common_or_weighted_input_value(self, input_lines, aliases):
        return self._get_common_input_value(input_lines, aliases) or self._get_weighted_input_value(input_lines, aliases)

    def _set_lot_alias_values(self, vals, aliases, value):
        Lot = self.env['stock.lot']
        for field_name in aliases or ():
            if field_name not in Lot._fields:
                continue
            field = Lot._fields[field_name]
            if self._lot_field_is_image_or_photo(field_name, field):
                continue
            before = set(vals.keys())
            self._set_lot_field_value(vals, field_name, value)
            if field.type == 'many2one' and field_name not in vals and before == set(vals.keys()):
                continue

    def _generated_pallet_count(self):
        self.ensure_one()
        candidates = (self.pieces, self.order_id.target_pieces if self.order_id else 0, self.qty_out)
        for candidate in candidates:
            try:
                number = int(float(candidate or 0))
            except (TypeError, ValueError):
                number = 0
            if number > 0:
                return number
        return 1

    def _aggregate_input_lines(self):
        self.ensure_one()
        if not self.order_id:
            return self.env['workshop.input.line']
        return self.order_id._get_active_input_lines().filtered(lambda line: line.lot_id)

    def _build_aggregate_lot_note(self, input_lines):
        aliases = self._lot_metadata_aliases()
        order = self.order_id
        detail_rows = []

        for input_line in input_lines:
            lot = input_line.lot_id
            detail_rows.append({
                'lot': lot.name or '',
                'product': input_line.product_id.display_name or '',
                'color': self._get_lot_field_value(lot, aliases['color'], display=True) or input_line.tone or '',
                'container': self._get_lot_field_value(lot, aliases['container'], display=True) or '',
                'origin': self._get_lot_field_value(lot, aliases['origin'], display=True) or '',
                'pedimento': self._get_lot_field_value(lot, aliases['pedimento'], display=True) or '',
                'block': self._get_lot_field_value(lot, aliases['block'], display=True) or input_line.block_name or '',
                'qty': input_line.qty_in or 0.0,
                'area': order._input_line_area(input_line) if order else (input_line.area_sqm or input_line.qty_in or 0.0),
            })

        summary = {
            'Lotes origen': self._unique_text_values(row['lot'] for row in detail_rows),
            'Colores': self._unique_text_values(row['color'] for row in detail_rows),
            'Contenedores': self._unique_text_values(row['container'] for row in detail_rows),
            'Orígenes': self._unique_text_values(row['origin'] for row in detail_rows),
            'Pedimentos': self._unique_text_values(row['pedimento'] for row in detail_rows),
            'Bloques': self._unique_text_values(row['block'] for row in detail_rows),
        }

        title = _('Origen de corte/formato generado desde la orden %s') % (order.name if order else '')
        output_line = _('Salida: %(lot)s | Producto: %(product)s | Pallets/piezas generadas: %(pieces)s') % {
            'lot': self.lot_name or (self.lot_id.name if self.lot_id else ''),
            'product': self.product_id.display_name if self.product_id else '',
            'pieces': self._generated_pallet_count(),
        }

        plain_lines = [title, output_line]
        for label, values in summary.items():
            if values:
                plain_lines.append('%s: %s' % (label, ', '.join(values)))
        plain_lines.append(_('Detalle de entradas:'))
        for row in detail_rows:
            plain_lines.append(
                '- %(lot)s | %(product)s | Color: %(color)s | Contenedor: %(container)s | '
                'Origen: %(origin)s | Pedimento: %(pedimento)s | Bloque: %(block)s | '
                'Cant.: %(qty).4f | Área m²: %(area).4f' % row
            )

        html_parts = [
            '<p><strong>%s</strong></p>' % escape(title),
            '<p>%s</p>' % escape(output_line),
            '<ul>',
        ]
        for label, values in summary.items():
            if values:
                html_parts.append('<li><strong>%s:</strong> %s</li>' % (escape(label), escape(', '.join(values))))
        html_parts.extend(['</ul>', '<p><strong>%s</strong></p>' % escape(_('Detalle de entradas:')), '<ul>'])
        for row in detail_rows:
            row_text = (
                '%(lot)s | %(product)s | Color: %(color)s | Contenedor: %(container)s | '
                'Origen: %(origin)s | Pedimento: %(pedimento)s | Bloque: %(block)s | '
                'Cant.: %(qty).4f | Área m²: %(area).4f' % row
            )
            html_parts.append('<li>%s</li>' % escape(row_text))
        html_parts.append('</ul>')

        return '\n'.join(plain_lines), ''.join(html_parts)

    def _set_lot_note_values(self, vals, plain_note, html_note):
        Lot = self.env['stock.lot']
        note_fields = (
            'note', 'notes', 'x_note', 'x_notes', 'x_nota', 'x_notas',
            'description', 'x_description', 'x_observaciones', 'observaciones',
            'x_detalles_placa', 'detalles_placa',
        )
        for field_name in note_fields:
            if field_name not in Lot._fields:
                continue
            field = Lot._fields[field_name]
            if getattr(field, 'compute', False) and not getattr(field, 'inverse', False):
                continue
            if field.type not in ('char', 'text', 'html'):
                continue
            note_value = html_note if field.type == 'html' else plain_note
            if field.type == 'char':
                limit = getattr(field, 'size', False) or 1024
                note_value = note_value[:limit]
            vals[field_name] = note_value

    def _prepare_aggregate_result_lot_metadata_vals(self):
        self.ensure_one()
        vals = {}
        input_lines = self._aggregate_input_lines()
        if not input_lines:
            return vals

        aliases = self._lot_metadata_aliases()
        pedimento = self._get_weighted_input_value(input_lines, aliases['pedimento'])
        container = self._get_weighted_input_value(input_lines, aliases['container'])
        block = self._get_common_or_weighted_input_value(input_lines, aliases['block'])
        color = self._get_common_input_value(input_lines, aliases['color'])
        origin = self._get_common_input_value(input_lines, aliases['origin'])

        if pedimento:
            self._set_lot_alias_values(vals, aliases['pedimento'], pedimento)
        if container:
            self._set_lot_alias_values(vals, aliases['container'], container)
        if block:
            self._set_lot_alias_values(vals, aliases['block'], block)
        if color:
            self._set_lot_alias_values(vals, aliases['color'], color)
        if origin:
            self._set_lot_alias_values(vals, aliases['origin'], origin)

        self._set_lot_alias_values(vals, aliases['bundle'], self._generated_pallet_count())

        plain_note, html_note = self._build_aggregate_lot_note(input_lines)
        self._set_lot_note_values(vals, plain_note, html_note)
        return vals

    def _apply_result_lot_area_and_dimensions(self, vals):
        Lot = self.env['stock.lot']
        output_area = self.order_id._output_line_area(self) if self.order_id else (self.area_sqm or self.qty_out or 0.0)
        for area_field in ('marble_sqm', 'area_sqm', 'sqm', 'x_area_sqm'):
            if area_field in Lot._fields and output_area:
                self._set_lot_field_value(vals, area_field, output_area)

        dimension_map = {
            'width_cm': ('marble_width', 'width_cm', 'width', 'stone_width', 'x_width_cm', 'x_ancho'),
            'height_cm': ('marble_height', 'height_cm', 'height', 'stone_height', 'x_height_cm', 'x_alto'),
            'thickness_cm': ('thickness_cm', 'thickness', 'marble_thickness', 'x_thickness_cm', 'x_grosor'),
        }
        for line_field, lot_fields in dimension_map.items():
            value = self[line_field]
            if not value:
                continue
            for lot_field in lot_fields:
                if lot_field in Lot._fields:
                    self._set_lot_field_value(vals, lot_field, value)
                    break
        return vals

    def _prepare_result_lot_metadata_vals(self):
        self.ensure_one()
        vals = {}
        source_line = self._get_metadata_source_input_line()
        source_lot = source_line.lot_id if source_line else False
        is_aggregate_cut = (
            self.order_id
            and self.order_id.operation_mode in ('slab_cut', 'format_process')
            and self.output_type in ('format_piece', 'remnant')
        )

        if is_aggregate_cut:
            vals.update(self._prepare_aggregate_result_lot_metadata_vals())
        elif source_lot:
            self._copy_lot_metadata_from_source_lot(vals, source_lot)

        self._apply_result_lot_area_and_dimensions(vals)

        if self.output_type == 'remnant':
            self._set_lot_material_type(vals, 'retazo')
        elif self.output_type == 'format_piece':
            self._set_lot_material_type(vals, 'formato')
        elif self.output_type == 'finished_slab':
            self._set_lot_material_type(vals, 'placa')

        return vals

    def _sync_result_lot_metadata(self, lot, force_name=False):
        self.ensure_one()
        if not lot:
            return False
        vals = self._prepare_result_lot_metadata_vals()
        if force_name:
            vals['name'] = force_name
        if vals:
            lot.write(vals)
        return True

    def _ensure_result_lot(self):
        self.ensure_one()
        if self.output_type in ('scrap', 'rejected'):
            return False
        if not self.product_id:
            raise UserError(_('La salida %s no tiene producto definido.') % self.display_name)
        if self.lot_id:
            self._sync_result_lot_metadata(self.lot_id)
            return self.lot_id
        if not self.lot_name:
            if self.input_line_id:
                self.lot_name = self.order_id._make_unique_lot_name(
                    self.order_id._default_output_lot_name(self.input_line_id),
                    product=self.product_id,
                    exclude_output=self,
                )
            else:
                self.lot_name = self.order_id._get_compact_result_lot_name(
                    output_type=self.output_type,
                    product=self.product_id,
                    target_area=self.order_id._output_line_area(self),
                    exclude_output=self,
                )
        existing = self.env['stock.lot'].search([
            ('name', '=', self.lot_name),
            ('product_id', '=', self.product_id.id),
            '|', ('company_id', '=', self.company_id.id), ('company_id', '=', False),
        ], limit=1)
        if existing:
            self.lot_id = existing.id
            self._sync_result_lot_metadata(existing)
            return existing
        lot_vals = {
            'name': self.lot_name,
            'product_id': self.product_id.id,
            'company_id': self.company_id.id,
        }
        lot_vals.update(self._prepare_result_lot_metadata_vals())
        lot = self.env['stock.lot'].create(lot_vals)
        self.lot_id = lot.id
        return lot


class WorkshopTransformationTrace(models.Model):
    _name = 'workshop.transformation.trace'
    _description = 'Trazabilidad de Transformación de Piedra'
    _order = 'date_done desc, id desc'

    order_id = fields.Many2one('workshop.order', string='Orden', required=True, ondelete='cascade')
    input_line_id = fields.Many2one('workshop.input.line', string='Entrada', ondelete='set null')
    output_line_id = fields.Many2one('workshop.output.line', string='Salida', ondelete='set null')
    source_product_id = fields.Many2one('product.product', string='Producto origen')
    source_lot_id = fields.Many2one('stock.lot', string='Lote origen')
    result_product_id = fields.Many2one('product.product', string='Producto resultado')
    result_lot_id = fields.Many2one('stock.lot', string='Lote resultado')
    process_id = fields.Many2one('workshop.process', string='Proceso')
    output_type = fields.Selection([
        ('finished_slab', 'Placa terminada'),
        ('format_piece', 'Formato / pieza'),
        ('remnant', 'Retazo aprovechable'),
        ('scrap', 'Merma'),
        ('rejected', 'Rechazado'),
    ], string='Tipo salida')
    qty_in = fields.Float(string='Cantidad entrada', digits=(12, 4))
    qty_out = fields.Float(string='Cantidad salida', digits=(12, 4))
    area_in_sqm = fields.Float(string='Área entrada m²', digits=(12, 4))
    area_out_sqm = fields.Float(string='Área salida m²', digits=(12, 4))
    loss_sqm = fields.Float(string='Merma m²', digits=(12, 4))
    date_done = fields.Datetime(string='Fecha', default=fields.Datetime.now)
    responsible_id = fields.Many2one('res.users', string='Responsable')


class WorkshopProgressLog(models.Model):
    _name = 'workshop.progress.log'
    _description = 'Bitácora de avance del taller'
    _order = 'date desc, id desc'

    order_id = fields.Many2one('workshop.order', string='Orden', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='order_id.company_id', store=True, readonly=True)
    date = fields.Date(string='Fecha', required=True, default=fields.Date.context_today)
    responsible_id = fields.Many2one(
        'res.users',
        string='Responsable',
        default=lambda self: self.env.user,
        help='Quién registró la corrida (auditoría).',
    )
    input_line_ids = fields.Many2many(
        'workshop.input.line',
        'workshop_progress_log_input_line_rel',
        'log_id',
        'input_line_id',
        string='Lotes procesados',
        domain="[('id', 'in', available_input_line_ids)]",
        help='Lotes consumidos en esta corrida. Cada lote sólo puede asignarse a una corrida; los que no se asignen a ninguna se devolverán al stock al declarar el resultado.',
    )
    available_input_line_ids = fields.Many2many(
        'workshop.input.line',
        compute='_compute_available_input_line_ids',
        string='Lotes disponibles',
    )
    area_sqm = fields.Float(string='m² producidos', digits=(12, 4), required=True)
    notes = fields.Text(string='Notas')

    @api.depends(
        'order_id',
        'order_id.input_line_ids',
        'order_id.input_line_ids.state',
        'order_id.progress_log_ids',
        'order_id.progress_log_ids.input_line_ids',
        'input_line_ids',
    )
    def _compute_available_input_line_ids(self):
        for log in self:
            order = log.order_id
            if not order:
                log.available_input_line_ids = False
                continue
            order_lines = order.input_line_ids.filtered(lambda l: l.state != 'cancelled')
            # `order.progress_log_ids - log` excluye la corrida actual sin
            # importar si está persistida (id real) o sólo en memoria (NewId);
            # así un lote elegido en otro renglón hermano desaparece del
            # dropdown de éste, en tiempo real, antes de guardar.
            used_in_other_logs = (order.progress_log_ids - log).mapped('input_line_ids')
            log.available_input_line_ids = (order_lines - used_in_other_logs) | log.input_line_ids

    @api.onchange('input_line_ids')
    def _onchange_input_line_ids_autofill_area(self):
        """Sugerir m² producidos = suma de m² de los lotes seleccionados.

        Para acabado/reproceso (1:1 sin transformación) el m² resultante coincide
        con el m² de entrada de las placas registradas. Pre-llenamos el campo
        para evitar que el usuario tenga que sumar a mano; si la corrida tuvo
        merma o retazo, podrá ajustarlo después porque el campo sigue editable.
        Para corte/formato no auto-llenamos: ahí los m² producidos suelen
        diferir del área de entrada por el plan de corte declarado en la orden.
        """
        for log in self:
            order = log.order_id
            if not order or order.operation_mode not in ('slab_finish', 'rework'):
                continue
            if not log.input_line_ids:
                continue
            total = sum(order._input_line_area(line) for line in log.input_line_ids)
            if total > 0.0:
                log.area_sqm = total

    @api.constrains('input_line_ids', 'order_id')
    def _check_input_lines_unique_in_order(self):
        for log in self:
            if not log.input_line_ids or not log.order_id:
                continue
            for input_line in log.input_line_ids:
                duplicate = self.search([
                    ('order_id', '=', log.order_id.id),
                    ('input_line_ids', 'in', input_line.id),
                    ('id', '!=', log.id),
                ], limit=1)
                if duplicate:
                    raise ValidationError(_(
                        'El lote %(lot)s ya está registrado en la corrida del %(date)s. '
                        'Cada lote sólo puede asignarse a una corrida de la bitácora.'
                    ) % {
                        'lot': input_line.lot_id.name or input_line.display_name,
                        'date': duplicate.date,
                    })

    @api.constrains('area_sqm', 'input_line_ids')
    def _check_area_sqm_within_lots_area(self):
        """Los m² producidos no pueden exceder el área de los lotes consumidos.

        Es físicamente imposible producir más m² de salida que los m² que
        entraron en la corrida. Se permite igualar (0% merma) y bajar (merma o
        retazos); subir está bloqueado.
        """
        for log in self:
            if not log.input_line_ids:
                continue
            order = log.order_id
            if order:
                lots_area = sum(order._input_line_area(line) for line in log.input_line_ids)
            else:
                lots_area = sum(log.input_line_ids.mapped('area_sqm'))
            # Tolerancia mínima para evitar falsos positivos por redondeo.
            if log.area_sqm > lots_area + 0.0001:
                raise ValidationError(_(
                    'Los m² producidos (%(produced).4f) exceden el área disponible '
                    'de los lotes seleccionados (%(available).4f m²) en la corrida del %(date)s.'
                ) % {
                    'produced': log.area_sqm,
                    'available': lots_area,
                    'date': log.date,
                })```

## ./models/workshop_process.py
```py
from odoo import models, fields, api


class WorkshopProcess(models.Model):
    _name = 'workshop.process'
    _description = 'Tipo de proceso de taller'
    _order = 'sequence, name'

    name = fields.Char(string='Nombre', required=True)
    code = fields.Char(string='Código', required=True, help='Código corto, ej: PUL, CRT, CEP')
    sequence = fields.Integer(default=10)
    process_type = fields.Selection([
        ('finish', 'Acabado'),
        ('cut', 'Corte / Formato'),
        ('format', 'Formato / Pallet'),
        ('rework', 'Reproceso / Reparación'),
        ('other', 'Otro'),
    ], string='Tipo de proceso', required=True, default='finish')
    default_operation_mode = fields.Selection([
        ('slab_finish', 'Acabado de placas'),
        ('slab_cut', 'Corte de placas'),
        ('format_process', 'Formatos / pallets'),
        ('rework', 'Reproceso / reparación'),
    ], string='Modo operativo sugerido', compute='_compute_default_operation_mode', store=True, readonly=False)
    active = fields.Boolean(default=True)
    description = fields.Text(string='Descripción')
    cost_per_sqm = fields.Float(string='Costo proceso por m²', digits=(12, 2))
    labor_cost = fields.Float(string='Costo mano de obra', digits=(12, 2))
    machine_cost = fields.Float(string='Costo máquina', digits=(12, 2))
    overhead_cost = fields.Float(string='Costo indirecto', digits=(12, 2))
    expected_yield_percent = fields.Float(
        string='Rendimiento esperado (%)',
        default=90.0,
        help='Rendimiento esperado para procesos de corte/formato. Se copia a la orden para calcular entrada requerida y KPI.',
    )
    default_loss_percent = fields.Float(
        string='Merma planeada por defecto (%)',
        default=0.0,
        help='Porcentaje de merma sugerido para generar automáticamente una salida de merma en corte/formato.',
    )
    color = fields.Integer(string='Color', default=0)

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'El código del proceso debe ser único.'),
    ]

    @api.depends('process_type')
    def _compute_default_operation_mode(self):
        mapping = {
            'finish': 'slab_finish',
            'cut': 'slab_cut',
            'format': 'format_process',
            'rework': 'rework',
            'other': 'slab_finish',
        }
        for rec in self:
            if not rec.default_operation_mode:
                rec.default_operation_mode = mapping.get(rec.process_type, 'slab_finish')
```

## ./reports/workshop_pick_report.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="action_report_workshop_pick" model="ir.actions.report">
        <field name="name">Orden de recolección de taller</field>
        <field name="model">workshop.order</field>
        <field name="report_type">qweb-pdf</field>
        <field name="report_name">stone_workshop.report_workshop_pick</field>
        <field name="report_file">stone_workshop.report_workshop_pick</field>
        <field name="binding_model_id" ref="model_workshop_order"/>
        <field name="binding_type">report</field>
        <field name="print_report_name">'Recoleccion-%s' % (object.name or '').replace('/', '-')</field>
    </record>

    <template id="report_workshop_pick">
        <t t-call="web.html_container">
            <t t-foreach="docs" t-as="o">
                <t t-call="web.external_layout">

                    <t t-set="lines" t-value="o.input_line_ids.filtered(lambda l: l.state != 'cancelled')"/>
                    <t t-set="line_count" t-value="len(lines)"/>

                    <t t-set="priority_label"
                       t-value="'URGENTE' if o.priority == '2' else 'ALTA' if o.priority == '1' else 'Normal'"/>

                    <div class="page" style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; font-size: 10px; color: #000; line-height: 1.25;">

                        <style type="text/css">
                            thead { display: table-row-group; }
                            tbody { display: table-row-group; }
                            tfoot { display: table-row-group; }
                            tr { page-break-inside: avoid; break-inside: avoid; }

                            table.aq-pick-table > thead > tr > th {
                                padding-top: 5px !important;
                                padding-bottom: 5px !important;
                                line-height: 1.05 !important;
                            }
                            table.aq-pick-table > tbody > tr > td {
                                padding-top: 4px !important;
                                padding-bottom: 4px !important;
                                line-height: 1.1 !important;
                            }
                        </style>

                        <!-- ================= ENCABEZADO ================= -->
                        <div class="row border-bottom pb-2 mb-2" style="border-color: #000 !important; align-items: center;">
                            <div class="col-8">
                                <h4 class="text-uppercase mb-0" style="font-weight: 800; letter-spacing: 1px;">
                                    Orden
                                    <span t-field="o.name" style="margin-left: 10px; color: #333;"/>
                                </h4>
                            </div>
                            <div class="col-4 text-end">
                                <div style="font-size: 14px; color: #505050; margin-top: 4px;">
                                    Impreso: <span t-esc="context_timestamp(datetime.datetime.now()).strftime('%d/%m/%Y %H:%M')"/>
                                </div>
                            </div>
                        </div>

                        <!-- ================= ALERTA URGENTE ================= -->
                        <div class="row mb-3" t-if="o.priority == '2'">
                            <div class="col-12">
                                <div style="border: 4px solid #b00020; background-color: #fff5f5; padding: 8px; width: 100%; box-sizing: border-box;">
                                    <div style="color: #b00020; font-weight: 900; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">
                                        ⚠️ ORDEN URGENTE — PRIORIZAR RECOLECCIÓN
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- ================= INFORMACIÓN GENERAL ================= -->
                        <div id="informations" style="margin-bottom: 12px; font-size: 10px;">
                            <table style="font-size: 9px; width: 100%; border-collapse: collapse; border: 1px solid #d8d8d8 !important; table-layout: fixed;">
                                <colgroup>
                                    <col style="width: 34%;"/>
                                    <col style="width: 34%;"/>
                                    <col style="width: 32%;"/>
                                </colgroup>

                                <thead>
                                    <tr>
                                        <th style="padding: 6px 8px; text-align: left; font-weight: 800; font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.5px; background-color: #f2f2f2; color: #222; border: 1px solid #d8d8d8 !important;">
                                            Proceso / Operación
                                        </th>
                                        <th style="padding: 6px 8px; text-align: left; font-weight: 800; font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.5px; background-color: #f2f2f2; color: #222; border: 1px solid #d8d8d8 !important;">
                                            Ruta de recolección
                                        </th>
                                        <th style="padding: 6px 8px; text-align: left; font-weight: 800; font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.5px; background-color: #f2f2f2; color: #222; border: 1px solid #d8d8d8 !important;">
                                            Estado del Documento
                                        </th>
                                    </tr>
                                </thead>

                                <tbody>
                                    <tr>
                                        <!-- PROCESO / OPERACIÓN -->
                                        <td style="vertical-align: top; padding: 0; border: 1px solid #d8d8d8 !important;">
                                            <div style="display: table; width: 100%; table-layout: fixed; border-bottom: 1px solid #d8d8d8 !important;">
                                                <div style="display: table-cell; width: 42%; padding: 5px 8px; color: #555; text-transform: uppercase; font-size: 8.5px; letter-spacing: 0.3px; border-right: 1px solid #d8d8d8 !important;">
                                                    Proceso
                                                </div>
                                                <div style="display: table-cell; padding: 5px 8px; font-weight: 600; overflow-wrap: anywhere; word-break: break-word;">
                                                    <span t-field="o.process_id"/>
                                                </div>
                                            </div>
                                            <div style="display: table; width: 100%; table-layout: fixed; border-bottom: 1px solid #d8d8d8 !important;">
                                                <div style="display: table-cell; width: 42%; padding: 5px 8px; color: #555; text-transform: uppercase; font-size: 8.5px; letter-spacing: 0.3px; border-right: 1px solid #d8d8d8 !important;">
                                                    Modo operativo
                                                </div>
                                                <div style="display: table-cell; padding: 5px 8px; font-weight: 600; overflow-wrap: anywhere; word-break: break-word;">
                                                    <span t-field="o.operation_mode"/>
                                                </div>
                                            </div>
                                            <div style="display: table; width: 100%; table-layout: fixed;">
                                                <div style="display: table-cell; width: 42%; padding: 5px 8px; color: #555; text-transform: uppercase; font-size: 8.5px; letter-spacing: 0.3px; border-right: 1px solid #d8d8d8 !important;">
                                                    Responsable
                                                </div>
                                                <div style="display: table-cell; padding: 5px 8px; font-weight: 600; overflow-wrap: anywhere; word-break: break-word;">
                                                    <span t-field="o.responsible_id"/>
                                                </div>
                                            </div>
                                        </td>

                                        <!-- RUTA DE RECOLECCIÓN -->
                                        <td style="vertical-align: top; padding: 0; border: 1px solid #d8d8d8 !important;">
                                            <div style="display: table; width: 100%; table-layout: fixed; border-bottom: 1px solid #d8d8d8 !important;">
                                                <div style="display: table-cell; width: 42%; padding: 5px 8px; color: #555; text-transform: uppercase; font-size: 8.5px; letter-spacing: 0.3px; border-right: 1px solid #d8d8d8 !important;">
                                                    Origen
                                                </div>
                                                <div style="display: table-cell; padding: 5px 8px; font-weight: 600; overflow-wrap: anywhere; word-break: break-word;">
                                                    <span t-field="o.location_src_id"/>
                                                </div>
                                            </div>
                                            <div style="display: table; width: 100%; table-layout: fixed;">
                                                <div style="display: table-cell; width: 42%; padding: 5px 8px; color: #555; text-transform: uppercase; font-size: 8.5px; letter-spacing: 0.3px; border-right: 1px solid #d8d8d8 !important;">
                                                    Taller (destino)
                                                </div>
                                                <div style="display: table-cell; padding: 5px 8px; font-weight: 600; overflow-wrap: anywhere; word-break: break-word;">
                                                    <span t-field="o.location_workshop_id"/>
                                                </div>
                                            </div>
                                        </td>

                                        <!-- ESTADO DEL DOCUMENTO -->
                                        <td style="vertical-align: top; padding: 0; border: 1px solid #d8d8d8 !important;">
                                            <div style="display: table; width: 100%; table-layout: fixed; border-bottom: 1px solid #d8d8d8 !important;">
                                                <div style="display: table-cell; width: 45%; padding: 5px 8px; color: #555; text-transform: uppercase; font-size: 8.5px; letter-spacing: 0.3px; border-right: 1px solid #d8d8d8 !important;">
                                                    Fecha planeada
                                                </div>
                                                <div style="display: table-cell; padding: 5px 8px; text-align: right; font-weight: 600; font-variant-numeric: tabular-nums;">
                                                    <span t-field="o.date_planned" t-options='{"widget": "date"}'/>
                                                </div>
                                            </div>
                                            <div style="display: table; width: 100%; table-layout: fixed; border-bottom: 1px solid #d8d8d8 !important;">
                                                <div style="display: table-cell; width: 45%; padding: 5px 8px; color: #555; text-transform: uppercase; font-size: 8.5px; letter-spacing: 0.3px; border-right: 1px solid #d8d8d8 !important;">
                                                    Prioridad
                                                </div>
                                                <div style="display: table-cell; padding: 5px 8px; text-align: right; font-weight: 700;">
                                                    <span t-if="o.priority == '2'" style="color: #b00020;">URGENTE</span>
                                                    <span t-elif="o.priority == '1'" style="color: #b8860b;">ALTA</span>
                                                    <span t-else="" style="color: #222;">Normal</span>
                                                </div>
                                            </div>
                                            <div style="display: table; width: 100%; table-layout: fixed;">
                                                <div style="display: table-cell; width: 45%; padding: 5px 8px; color: #555; text-transform: uppercase; font-size: 8.5px; letter-spacing: 0.3px; border-right: 1px solid #d8d8d8 !important;">
                                                    Estado
                                                </div>
                                                <div style="display: table-cell; padding: 5px 8px; text-align: right; font-weight: 600; overflow-wrap: anywhere; word-break: break-word;">
                                                    <span t-field="o.state"/>
                                                </div>
                                            </div>
                                        </td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>

                        <!-- ================= ENCABEZADO DE SECCIÓN ================= -->
                        <div style="margin-top: 14px; margin-bottom: 6px; width: 100%; page-break-inside: avoid;">
                            <table role="presentation" cellpadding="0" cellspacing="0" border="0"
                                   style="width: 100%; border-collapse: collapse; border-spacing: 0; table-layout: auto; border: 0 !important; background: transparent !important;">
                                <tbody style="border: 0 !important; background: transparent !important;">
                                    <tr style="border: 0 !important; background: transparent !important;">
                                        <td style="width: 1%; vertical-align: middle; padding: 0 12px 0 0; white-space: nowrap; border: 0 !important; background: transparent !important;">
                                            <span style="font-family: Georgia, 'Times New Roman', serif; font-size: 13px; font-style: italic; font-weight: 400; color: #222;">
                                                Placas a recolectar
                                            </span>
                                        </td>
                                        <td style="vertical-align: middle; padding: 0; border: 0 !important; background: transparent !important;">
                                            <div style="height: 0; line-height: 0; font-size: 0; overflow: hidden; margin: 0; padding: 0; border-top: 1px solid #222 !important;">&#160;</div>
                                        </td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>

                        <!-- ================= TABLA DE PLACAS ================= -->
                        <table class="table table-sm table-striped aq-pick-table"
                               style="width: 100%; font-size: 9px; border-collapse: collapse; table-layout: fixed; page-break-inside: auto;">
                            <thead>
                                <tr style="background-color: #2f2f2f; color: #fff;">
                                    <th class="text-center" style="color: #fff; width: 5%; font-weight: bold;">#</th>
                                    <th style="color: #fff; width: 24%; font-weight: bold;">Lote / Placa</th>
                                    <th style="color: #fff; width: 36%; font-weight: bold;">Producto</th>
                                    <th style="color: #fff; width: 22%; font-weight: bold;">Ubicación origen</th>
                                    <th class="text-end" style="color: #fff; width: 8%; font-weight: bold;">m²</th>
                                    <th class="text-center" style="color: #fff; width: 5%; font-weight: bold;">✔</th>
                                </tr>
                            </thead>
                            <tbody>
                                <t t-foreach="lines" t-as="line">
                                    <t t-set="loc_full" t-value="line.location_id.complete_name or line.location_id.display_name or ''"/>
                                    <t t-set="loc_short" t-value="loc_full.split('/', 2)[2] if loc_full.count('/') &gt;= 2 else loc_full"/>
                                    <tr style="page-break-inside: avoid;">
                                        <td class="text-center align-middle"><span t-esc="line_index + 1"/></td>
                                        <td class="align-middle" style="overflow-wrap: anywhere; word-break: break-word;">
                                            <strong t-field="line.lot_id"/>
                                        </td>
                                        <td class="align-middle" style="overflow-wrap: anywhere; word-break: break-word;">
                                            <span t-field="line.product_id"/>
                                        </td>
                                        <td class="align-middle" style="overflow-wrap: anywhere; word-break: break-word;">
                                            <span t-esc="loc_short"/>
                                        </td>
                                        <td class="text-end align-middle" style="white-space: nowrap; font-variant-numeric: tabular-nums;">
                                            <span t-field="line.area_sqm"/>
                                        </td>
                                        <td class="text-center align-middle" style="border: 1px solid #bbb;"></td>
                                    </tr>
                                </t>
                                <t t-if="not lines">
                                    <tr>
                                        <td colspan="6" class="text-center" style="padding: 12px; color: #999; font-style: italic;">
                                            Esta orden no tiene placas registradas.
                                        </td>
                                    </tr>
                                </t>
                            </tbody>
                            <tfoot>
                                <tr style="background-color: #f2f2f2; color: #222;">
                                    <td colspan="4" style="padding: 5px 8px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.3px; border: 1px solid #d8d8d8 !important;">
                                        Totales
                                    </td>
                                    <td class="text-end" style="padding: 5px 8px; font-weight: 800; font-variant-numeric: tabular-nums; white-space: nowrap; border: 1px solid #d8d8d8 !important;">
                                        <span t-esc="'%.4f' % sum(lines.mapped('area_sqm'))"/>
                                    </td>
                                    <td style="border: 1px solid #d8d8d8 !important;"></td>
                                </tr>
                            </tfoot>
                        </table>

                        <!-- ================= NOTAS ================= -->
                        <div t-if="o.notes" style="border: 1px dashed #ccc; padding: 10px; margin-top: 14px;">
                            <strong style="font-size: 9px; text-transform: uppercase; color: #555;">Notas:</strong>
                            <div style="font-size: 9px; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word;" t-field="o.notes"/>
                        </div>

                        <!-- ================= FIRMAS ================= -->
                        <div style="margin-top: 50px; width: 100%; page-break-inside: avoid;">
                            <div class="row" style="margin-top: 40px;">
                                <div class="col-1"></div>
                                <div class="col-4 text-center">
                                    <div style="border-top: 1px solid #000; padding-top: 5px;">
                                        <strong style="font-size: 9px; text-transform: uppercase;">Entrega (almacén)</strong>
                                        <div style="font-size: 8px; color: #777; margin-top: 2px;">Nombre y firma</div>
                                    </div>
                                </div>
                                <div class="col-2"></div>
                                <div class="col-4 text-center">
                                    <div style="border-top: 1px solid #000; padding-top: 5px;">
                                        <strong style="font-size: 9px; text-transform: uppercase;">Recibe (taller)</strong>
                                        <div style="font-size: 8px; color: #777; margin-top: 2px;">Nombre y firma</div>
                                    </div>
                                </div>
                                <div class="col-1"></div>
                            </div>
                        </div>

                        <p style="text-align: center; margin-top: 18px; font-size: 8.5px; color: #999; letter-spacing: 0.3px;">
                            Documento de recolección interna · No es factura · No es comprobante fiscal
                        </p>

                    </div>
                </t>
            </t>
        </t>
    </template>
</odoo>```

## ./security/workshop_security.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <data noupdate="0">
        <record id="module_category_stone_workshop" model="ir.module.category">
            <field name="name">Taller de Piedra</field>
            <field name="description">Permisos para gestión de taller de piedra</field>
            <field name="sequence">35</field>
        </record>

        <record id="res_groups_privilege_stone_workshop" model="res.groups.privilege">
            <field name="name">Taller de Piedra</field>
            <field name="category_id" ref="module_category_stone_workshop"/>
            <field name="sequence">35</field>
        </record>

        <record id="group_workshop_user" model="res.groups">
            <field name="name">Usuario de taller</field>
            <field name="privilege_id" ref="res_groups_privilege_stone_workshop"/>
        </record>

        <record id="group_workshop_supervisor" model="res.groups">
            <field name="name">Supervisor de taller</field>
            <field name="privilege_id" ref="res_groups_privilege_stone_workshop"/>
            <field name="implied_ids" eval="[(4, ref('group_workshop_user'))]"/>
        </record>

        <record id="group_workshop_manager" model="res.groups">
            <field name="name">Administrador de taller</field>
            <field name="privilege_id" ref="res_groups_privilege_stone_workshop"/>
            <field name="implied_ids" eval="[(4, ref('group_workshop_supervisor'))]"/>
        </record>
    </data>
</odoo>```

## ./static/src/components/workshop_lot_selector/workshop_lot_selector.js
```js
/** @odoo-module **/

import { Component, useState, onWillStart, onWillUpdateProps, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";

const STATE_LABELS = {
    pending: "Pendiente",
    reserved_for_workshop: "Reservada",
    sent_to_workshop: "En taller",
    in_progress: "En proceso",
    partial_done: "Parcial",
    done: "Terminada",
    rejected: "Rechazada",
    damaged: "Dañada",
    cancelled: "Cancelada",
};

export class WorkshopLotSelector extends Component {
    static template = "stone_workshop.WorkshopLotSelector";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");

        this.state = useState({
            version: 0,
            savedRows: [],
            savedRowsLoaded: false,
            savedRowsOrderId: false,
        });

        this._popupRoot = null;
        this._popupKeyHandler = null;
        this._popupObserver = null;

        onWillStart(async () => {
            await this._loadSavedRowsFromServer();
        });

        onWillUpdateProps(async (nextProps) => {
            const currentOrderId = this.getOrderId(this.props);
            const nextOrderId = this.getOrderId(nextProps);

            if (currentOrderId !== nextOrderId) {
                await this._loadSavedRowsFromServer(nextProps);
            }

            this.state.version += 1;
        });

        onWillUnmount(() => {
            this.destroyPopup();
        });
    }

    _notify(message, type = "info") {
        if (this.notification) {
            this.notification.add(message, { type, sticky: false });
        }
    }

    _escapeHtml(value) {
        if (value === null || value === undefined) return "";
        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    _extractId(value) {
        if (!value) return false;
        if (typeof value === "number") return value;
        if (Array.isArray(value)) return value[0] || false;
        if (typeof value === "object") {
            return value.resId || value.id || value[0] || false;
        }
        return false;
    }

    _extractName(value) {
        if (!value) return "";
        if (Array.isArray(value)) return value[1] || "";
        if (typeof value === "object") {
            return value.display_name || value.name || value.value || "";
        }
        return String(value || "");
    }

    _extractNumber(value) {
        if (value === null || value === undefined || value === false) return 0;
        if (typeof value === "number") return value;
        if (typeof value === "string") return parseFloat(value.replace(",", ".")) || 0;
        if (typeof value === "object") {
            if ("value" in value) return this._extractNumber(value.value);
            if ("raw_value" in value) return this._extractNumber(value.raw_value);
        }
        return 0;
    }

    _getOrderState(props = this.props) {
        const state = props.record.data.state;
        if (!state) return "draft";
        if (typeof state === "string") return state;
        if (typeof state === "object") return state.value || state.raw_value || state.name || "draft";
        return String(state || "draft");
    }

    canEdit() {
        const state = this._getOrderState();
        return !this.props.readonly && ["draft", "validated"].includes(state);
    }

    getOrderId(props = this.props) {
        const id = props.record.resId || props.record.data.id || false;
        return typeof id === "number" && id > 0 ? id : false;
    }

    getProductId() {
        const data = this.props.record.data || {};
        const selectedProduct = this._extractId(data.input_product_id);
        if (selectedProduct) return selectedProduct;

        const firstRow = this.selectedRows[0];
        return firstRow ? firstRow.product_id : false;
    }

    getProductName() {
        const data = this.props.record.data || {};
        const selectedProductName = this._extractName(data.input_product_id);
        if (selectedProductName) return selectedProductName;

        const firstRow = this.selectedRows[0];
        return firstRow ? firstRow.product_name : "";
    }

    getLocationSrcId() {
        return this._extractId(this.props.record.data.location_src_id);
    }

    _getX2ManyRecords(fieldName) {
        const value = this.props.record.data[fieldName];
        if (!value) return [];
        if (Array.isArray(value.records)) return value.records;
        if (Array.isArray(value)) return value;
        return [];
    }

    _hasOutputLines() {
        return this._getX2ManyRecords("output_line_ids").length > 0;
    }

    _effectiveArea(row) {
        const area = this._extractNumber(row && row.area_sqm);
        const qty = this._extractNumber(row && row.qty_in);

        // Blindaje visual para líneas creadas con el bug metro/centímetro:
        // si área_sqm quedó diminuta, pero Cant. sí trae los m² reales, mostramos Cant. como área.
        if (qty > 0 && (!area || area < qty * 0.25)) {
            return qty;
        }

        return area || qty || 0;
    }

    _serverRowToDisplayRow(row) {
        const state = row.state || "pending";
        const qtyIn = this._extractNumber(row.qty_in);
        const areaSqm = this._effectiveArea({ area_sqm: row.area_sqm, qty_in: qtyIn });
        return {
            key: row.id,
            id: row.id,
            lot_id: row.lot_id ? row.lot_id[0] : false,
            lot_name: row.lot_id ? row.lot_id[1] : "-",
            product_id: row.product_id ? row.product_id[0] : false,
            product_name: row.product_id ? row.product_id[1] : "-",
            qty_in: qtyIn,
            area_sqm: areaSqm,
            width_cm: this._extractNumber(row.width_cm),
            height_cm: this._extractNumber(row.height_cm),
            thickness_cm: this._extractNumber(row.thickness_cm),
            block_name: row.block_name || "",
            tone: row.tone || "",
            location_name: row.location_id ? String(row.location_id[1]).split("/").pop() : "",
            state,
            state_label: STATE_LABELS[state] || state,
        };
    }

    async _loadSavedRowsFromServer(props = this.props) {
        const orderId = this.getOrderId(props);

        if (!orderId) {
            this.state.savedRows = [];
            this.state.savedRowsLoaded = false;
            this.state.savedRowsOrderId = false;
            return;
        }

        try {
            const rows = await this.orm.searchRead(
                "workshop.input.line",
                [["order_id", "=", orderId], ["state", "!=", "cancelled"]],
                [
                    "id",
                    "sequence",
                    "material_type",
                    "product_id",
                    "lot_id",
                    "qty_in",
                    "area_sqm",
                    "width_cm",
                    "height_cm",
                    "thickness_cm",
                    "pieces",
                    "block_name",
                    "tone",
                    "current_finish",
                    "location_id",
                    "reserved_origin",
                    "state",
                ],
                { order: "sequence, id" }
            );

            this.state.savedRows = (rows || [])
                .map((row) => this._serverRowToDisplayRow(row))
                .filter((row) => row.lot_id);

            this.state.savedRowsLoaded = true;
            this.state.savedRowsOrderId = orderId;
        } catch (error) {
            console.warn("[WORKSHOP LOT SELECTOR] No se pudieron cargar entradas guardadas:", error);
            this.state.savedRows = [];
            this.state.savedRowsLoaded = false;
            this.state.savedRowsOrderId = false;
        }
    }

    _shouldUseSavedRows() {
        const orderId = this.getOrderId();
        return (
            orderId &&
            this.state.savedRowsLoaded &&
            this.state.savedRowsOrderId === orderId
        );
    }

    get selectedRows() {
        void this.state.version;

        if (this._shouldUseSavedRows()) {
            return this.state.savedRows || [];
        }

        const records = this._getX2ManyRecords("input_line_ids");

        return records.map((record, index) => {
            const data = record.data || record;
            const lotId = this._extractId(data.lot_id);
            const productId = this._extractId(data.product_id);
            const locationName = this._extractName(data.location_id);
            const state = data.state || "pending";
            const qtyIn = this._extractNumber(data.qty_in);
            const areaSqm = this._effectiveArea({ area_sqm: data.area_sqm, qty_in: qtyIn });

            return {
                key: record.id || record.resId || lotId || index,
                lot_id: lotId,
                lot_name: this._extractName(data.lot_id) || "-",
                product_id: productId,
                product_name: this._extractName(data.product_id) || "-",
                qty_in: qtyIn,
                area_sqm: areaSqm,
                width_cm: this._extractNumber(data.width_cm),
                height_cm: this._extractNumber(data.height_cm),
                thickness_cm: this._extractNumber(data.thickness_cm),
                block_name: data.block_name || "",
                tone: data.tone || "",
                location_name: locationName ? locationName.split("/").pop() : "",
                state,
                state_label: STATE_LABELS[state] || state,
            };
        }).filter((row) => row.lot_id);
    }

    get selectedArea() {
        return this.selectedRows.reduce((total, row) => {
            return total + this._effectiveArea(row);
        }, 0);
    }

    formatNum(value) {
        const num = parseFloat(value || 0);
        return Number.isFinite(num) ? num.toFixed(2) : "0.00";
    }

    formatDim(value) {
        const num = parseFloat(value || 0);
        if (!Number.isFinite(num) || !num) return "-";
        return num % 1 === 0 ? num.toFixed(0) : num.toFixed(2);
    }

    _getCurrentLotIds() {
        return this.selectedRows.map((row) => row.lot_id).filter(Boolean);
    }

    async removeLot(lotId, ev = null) {
        if (ev) ev.stopPropagation();

        if (!this.canEdit()) {
            this._notify("La selección de entradas solo puede modificarse antes de enviar la orden a taller.", "warning");
            return;
        }

        const nextLotIds = this._getCurrentLotIds().filter((id) => id !== lotId);
        await this._rebuildInputLines(nextLotIds);
    }

    async _readDisplayNameMap(model, ids) {
        const cleanIds = Array.from(new Set((ids || []).map((id) => parseInt(id, 10)).filter(Boolean)));
        const result = new Map();

        if (!cleanIds.length) return result;

        try {
            const rows = await this.orm.read(model, cleanIds, ["display_name"]);
            for (const row of rows || []) {
                result.set(row.id, row.display_name || String(row.id));
            }
        } catch (error) {
            console.warn(`[WORKSHOP LOT SELECTOR] No se pudo leer display_name de ${model}:`, error);
            for (const id of cleanIds) {
                result.set(id, String(id));
            }
        }

        return result;
    }

    async _buildRecordUpdateNameMaps(lineVals) {
        const productIds = [];
        const lotIds = [];
        const locationIds = [];

        for (const vals of lineVals || []) {
            const productId = this._extractId(vals.product_id);
            const lotId = this._extractId(vals.lot_id);
            const locationId = this._extractId(vals.location_id);

            if (productId) productIds.push(productId);
            if (lotId) lotIds.push(lotId);
            if (locationId) locationIds.push(locationId);
        }

        const [productNames, lotNames, locationNames] = await Promise.all([
            this._readDisplayNameMap("product.product", productIds),
            this._readDisplayNameMap("stock.lot", lotIds),
            this._readDisplayNameMap("stock.location", locationIds),
        ]);

        return { productNames, lotNames, locationNames };
    }

    _toRecordMany2OneValue(value, nameMap, fallbackName = "") {
        const id = this._extractId(value);
        if (!id) return false;
        return [id, nameMap.get(id) || fallbackName || String(id)];
    }

    _normalizeInputLineValsForRecordUpdate(vals, nameMaps) {
        const cleanVals = { ...(vals || {}) };

        const productId = this._extractId(cleanVals.product_id);
        const lotId = this._extractId(cleanVals.lot_id);
        const locationId = this._extractId(cleanVals.location_id);

        if (!lotId) {
            return null;
        }

        if (productId) {
            cleanVals.product_id = this._toRecordMany2OneValue(
                productId,
                nameMaps.productNames,
                this.getProductName() || String(productId)
            );
        }

        cleanVals.lot_id = this._toRecordMany2OneValue(
            lotId,
            nameMaps.lotNames,
            String(lotId)
        );

        if (locationId) {
            cleanVals.location_id = this._toRecordMany2OneValue(
                locationId,
                nameMaps.locationNames,
                String(locationId)
            );
        } else if ("location_id" in cleanVals) {
            cleanVals.location_id = false;
        }

        return cleanVals;
    }

    _normalizeInputLineValsForServerWrite(vals) {
        const productId = this._extractId(vals.product_id);
        const lotId = this._extractId(vals.lot_id);
        const locationId = this._extractId(vals.location_id);

        if (!productId || !lotId) {
            return null;
        }

        const cleanVals = {
            sequence: vals.sequence || 10,
            material_type: vals.material_type || "slab",
            product_id: productId,
            lot_id: lotId,
            qty_in: vals.qty_in || 1.0,
            area_sqm: vals.area_sqm || vals.qty_in || 0.0,
            width_cm: vals.width_cm || 0.0,
            height_cm: vals.height_cm || 0.0,
            thickness_cm: vals.thickness_cm || 0.0,
            pieces: vals.pieces || 1,
            block_name: vals.block_name || false,
            tone: vals.tone || false,
            current_finish: vals.current_finish || false,
            reserved_origin: vals.reserved_origin || false,
            state: vals.state || "pending",
        };

        if (locationId) {
            cleanVals.location_id = locationId;
        }

        return cleanVals;
    }

    async _prepareLineVals(cleanLotIds, productId) {
        if (!cleanLotIds.length) return [];

        return await this.orm.call(
            "workshop.order",
            "prepare_input_line_vals_from_lots",
            [],
            {
                product_id: productId,
                lot_ids: cleanLotIds,
                location_id: this.getLocationSrcId() || false,
            }
        );
    }

    async _writeInputLinesDirectly(orderId, lineVals) {
        const serverVals = [];

        for (const vals of lineVals || []) {
            const clean = this._normalizeInputLineValsForServerWrite(vals);
            if (clean) {
                serverVals.push(clean);
            }
        }

        const updateVals = {
            input_line_ids: [
                [5, 0, 0],
                ...serverVals.map((vals) => [0, 0, vals]),
            ],
        };

        if (this._hasOutputLines()) {
            updateVals.output_line_ids = [[5, 0, 0]];
        }

        await this.orm.write("workshop.order", [orderId], updateVals);

        await this._loadSavedRowsFromServer();
        this.state.version += 1;

        if (this._hasOutputLines()) {
            this._notify("Se actualizaron entradas y se limpiaron salidas esperadas para evitar desajustes.", "warning");
        }
    }

    async _updateInputLinesInUnsavedRecord(lineVals) {
        const nameMaps = await this._buildRecordUpdateNameMaps(lineVals);
        const normalizedLineVals = [];

        for (const vals of lineVals || []) {
            const normalized = this._normalizeInputLineValsForRecordUpdate(vals, nameMaps);
            if (normalized) {
                normalizedLineVals.push(normalized);
            }
        }

        if ((lineVals || []).length && !normalizedLineVals.length) {
            this._notify(
                "No se pudo preparar ninguna línea válida con lote. Revisa que los lotes seleccionados existan y tengan producto.",
                "danger"
            );
            return;
        }

        if ((lineVals || []).length !== normalizedLineVals.length) {
            this._notify(
                "Se omitieron una o más líneas sin lote válido para evitar guardar entradas incompletas.",
                "warning"
            );
        }

        const updateVals = {
            input_line_ids: [
                [5, 0, 0],
                ...normalizedLineVals.map((vals) => [0, 0, vals]),
            ],
        };

        if (this._hasOutputLines()) {
            updateVals.output_line_ids = [[5, 0, 0]];
        }

        await this.props.record.update(updateVals);
        this.state.savedRowsLoaded = false;
        this.state.savedRows = [];
        this.state.version += 1;

        if (this._hasOutputLines()) {
            this._notify("Se actualizaron entradas y se limpiaron salidas esperadas para evitar desajustes.", "warning");
        }
    }

    async _rebuildInputLines(lotIds) {
        if (!this.canEdit()) {
            this._notify("La selección de entradas solo puede modificarse antes de enviar la orden a taller.", "warning");
            return;
        }

        const cleanLotIds = Array.from(
            new Set((lotIds || []).map((id) => parseInt(id, 10)).filter(Boolean))
        );

        const productId = this.getProductId();

        if (!productId && cleanLotIds.length) {
            this._notify("Selecciona un producto de entrada antes de agregar lotes.", "warning");
            return;
        }

        const lineVals = await this._prepareLineVals(cleanLotIds, productId);
        const orderId = this.getOrderId();

        if (orderId) {
            await this._writeInputLinesDirectly(orderId, lineVals);
        } else {
            await this._updateInputLinesInUnsavedRecord(lineVals);
        }
    }

    openPopup() {
        if (!this.canEdit()) {
            this._notify("La selección de entradas solo puede modificarse antes de enviar la orden a taller.", "warning");
            return;
        }

        const productId = this.getProductId();

        if (!productId) {
            this._notify("Selecciona un producto de entrada para cargar lotes disponibles.", "warning");
            return;
        }

        this.destroyPopup();

        this._popupRoot = document.createElement("div");
        this._popupRoot.className = "wlp-root";
        document.body.appendChild(this._popupRoot);

        this._renderPopupDOM(productId);
    }

    async _renderPopupDOM(productId) {
        const PAGE_SIZE = 35;
        const root = this._popupRoot;
        const self = this;

        const popupState = {
            quants: [],
            totalCount: 0,
            page: 0,
            hasMore: false,
            isLoading: false,
            isLoadingMore: false,
            pendingIds: new Set(this._getCurrentLotIds()),
            filters: {
                lot_name: "",
                bloque: "",
                atado: "",
                alto_min: "",
                ancho_min: "",
                tipo: "",
            },
            qtyCache: {},
            cachedQuantIds: new Set(),
        };

        let searchTimeout = null;

        root.innerHTML = `
            <div class="wlp-overlay" id="wlp-overlay">
                <div class="wlp-container">
                    <div class="wlp-header">
                        <div class="wlp-title">
                            <i class="fa fa-th-large"></i>
                            <div>
                                <strong>Seleccionar lotes para taller</strong>
                                <span>${this._escapeHtml(this.getProductName())}</span>
                            </div>
                        </div>
                        <div class="wlp-header-actions">
                            <span class="wlp-badge">
                                <i class="fa fa-check-circle"></i>
                                <span id="wlp-count">${popupState.pendingIds.size}</span> seleccionados
                            </span>
                            <span class="wlp-badge wlp-badge-area">
                                <i class="fa fa-balance-scale"></i>
                                <span id="wlp-area">0.00</span> m²
                            </span>
                            <button type="button" class="wlp-btn wlp-btn-primary" id="wlp-confirm-top">
                                <i class="fa fa-check"></i> Confirmar
                            </button>
                            <button type="button" class="wlp-btn wlp-btn-ghost" id="wlp-close">
                                <i class="fa fa-times"></i>
                            </button>
                        </div>
                    </div>

                    <div class="wlp-filters">
                        <label>Lote<input type="text" id="wlf-lot" placeholder="Buscar lote"/></label>
                        <label>Bloque<input type="text" id="wlf-bloque" placeholder="Bloque"/></label>
                        <label>Atado<input type="text" id="wlf-atado" placeholder="Atado"/></label>
                        <label>Alto mín.<input type="number" id="wlf-alto" step="0.01" placeholder="0"/></label>
                        <label>Ancho mín.<input type="number" id="wlf-ancho" step="0.01" placeholder="0"/></label>
                        <label>Tipo
                            <select id="wlf-tipo">
                                <option value="">Todos</option>
                                <option value="placa">Placa</option>
                                <option value="formato">Formato</option>
                                <option value="pieza">Pieza</option>
                                <option value="pallet">Pallet</option>
                            </select>
                        </label>
                        <div class="wlp-filter-actions">
                            <button type="button" class="wlp-btn wlp-btn-soft" id="wlp-select-all">
                                <i class="fa fa-check-square-o"></i> Todo visible
                            </button>
                            <button type="button" class="wlp-btn wlp-btn-danger-soft" id="wlp-clear">
                                <i class="fa fa-square-o"></i> Limpiar
                            </button>
                        </div>
                        <div class="wlp-spacer"></div>
                        <span class="wlp-stat" id="wlp-stat">
                            <i class="fa fa-circle-o-notch fa-spin"></i> Buscando...
                        </span>
                    </div>

                    <div class="wlp-body" id="wlp-body">
                        <div class="wlp-empty">
                            <i class="fa fa-circle-o-notch fa-spin"></i>
                            <span>Cargando inventario...</span>
                        </div>
                    </div>

                    <div class="wlp-footer">
                        <span id="wlp-footer-info">—</span>
                        <div class="wlp-footer-actions">
                            <button type="button" class="wlp-btn wlp-btn-outline" id="wlp-cancel">Cancelar</button>
                            <button type="button" class="wlp-btn wlp-btn-primary" id="wlp-confirm-bottom">
                                <i class="fa fa-check"></i> Agregar selección
                            </button>
                        </div>
                    </div>
                </div>
            </div>`;

        const body = root.querySelector("#wlp-body");
        const stat = root.querySelector("#wlp-stat");
        const footerInfo = root.querySelector("#wlp-footer-info");
        const countEl = root.querySelector("#wlp-count");
        const areaEl = root.querySelector("#wlp-area");

        const cacheQuant = (quant) => {
            if (!quant || !quant.lot_id) return;

            const quantKey = String(quant.id);

            if (popupState.cachedQuantIds.has(quantKey)) return;

            popupState.cachedQuantIds.add(quantKey);

            const lotId = quant.lot_id[0];
            const key = String(lotId);

            if (!popupState.qtyCache[key]) {
                popupState.qtyCache[key] = {
                    qty: 0,
                    tipo: (quant.x_tipo || "placa").toLowerCase(),
                };
            }

            popupState.qtyCache[key].qty += quant.quantity || 0;
        };

        const cacheQuantList = (items) => {
            for (const item of items || []) {
                cacheQuant(item);
            }
        };

        const computeSelectedArea = () => {
            let total = 0;

            for (const lotId of popupState.pendingIds) {
                const cached = popupState.qtyCache[String(lotId)];
                if (cached) {
                    total += cached.qty || 0;
                }
            }

            return total;
        };

        const updateCounters = () => {
            countEl.textContent = popupState.pendingIds.size;
            areaEl.textContent = self.formatNum(computeSelectedArea());
        };

        const ensureQtyCacheForPending = async () => {
            const missingIds = Array.from(popupState.pendingIds).filter((lotId) => {
                return !popupState.qtyCache[String(lotId)];
            });

            if (!missingIds.length) return;

            try {
                const items = await self.orm.call(
                    "stock.quant",
                    "search_workshop_lot_inventory",
                    [],
                    {
                        product_id: productId,
                        filters: {},
                        current_lot_ids: missingIds,
                        location_id: self.getLocationSrcId() || false,
                        order_id: self.getOrderId() || false,
                    }
                );

                cacheQuantList(
                    (items || []).filter((q) => q.lot_id && missingIds.includes(q.lot_id[0]))
                );
            } catch (error) {
                console.warn("[WORKSHOP LOT SELECTOR] No se pudo precargar selección actual:", error);
            }
        };

        const updateStats = () => {
            stat.innerHTML = `${popupState.totalCount} lotes`;
            footerInfo.innerHTML = `<strong>${popupState.quants.length}</strong> de <strong>${popupState.totalCount}</strong> registros visibles`;
        };

        const renderTable = () => {
            updateCounters();
            updateStats();

            if (!popupState.quants.length && !popupState.isLoading) {
                body.innerHTML = `
                    <div class="wlp-empty">
                        <i class="fa fa-inbox"></i>
                        <span>No hay lotes disponibles con estos filtros.</span>
                    </div>`;
                return;
            }

            let rows = "";

            for (const quant of popupState.quants) {
                cacheQuant(quant);

                const lotId = quant.lot_id ? quant.lot_id[0] : 0;
                const lotName = quant.lot_id ? quant.lot_id[1] : "-";
                const selected = popupState.pendingIds.has(lotId);
                const tipo = (quant.x_tipo || "placa").toLowerCase();
                const location = quant.location_id ? String(quant.location_id[1]).split("/").pop() : "-";

                const photo = quant.x_fotografia_principal
                    ? `<img src="data:image/jpeg;base64,${quant.x_fotografia_principal}" alt="Foto"/>`
                    : `<i class="fa fa-picture-o"></i>`;

                const status = selected
                    ? `<span class="wlp-tag wlp-tag-selected">Selec.</span>`
                    : `<span class="wlp-tag wlp-tag-free">Libre</span>`;

                rows += `
                    <tr data-lot-id="${lotId}" class="${selected ? "is-selected" : ""}">
                        <td class="wlp-col-check">
                            <span class="wlp-check">${selected ? '<i class="fa fa-check"></i>' : ""}</span>
                        </td>
                        <td class="wlp-col-photo">
                            <span class="wlp-photo">${photo}</span>
                        </td>
                        <td class="wlp-cell-lot">${self._escapeHtml(lotName)}</td>
                        <td>${self._escapeHtml(quant.x_bloque || "-")}</td>
                        <td>${self._escapeHtml(quant.x_atado || "-")}</td>
                        <td class="text-end">${self.formatDim(quant.x_alto)}</td>
                        <td class="text-end">${self.formatDim(quant.x_ancho)}</td>
                        <td class="text-end">${self.formatDim(quant.x_grosor)}</td>
                        <td class="text-end fw-bold">${self.formatNum(quant.quantity)}</td>
                        <td><span class="wlp-type">${self._escapeHtml(tipo || "-")}</span></td>
                        <td>${self._escapeHtml(quant.x_color || "-")}</td>
                        <td class="text-muted">${self._escapeHtml(location)}</td>
                        <td>${status}</td>
                    </tr>`;
            }

            const sentinel = `
                <div id="wlp-sentinel" class="wlp-sentinel">
                    ${popupState.isLoadingMore ? '<i class="fa fa-circle-o-notch fa-spin"></i> Cargando más...' : ""}
                    ${popupState.hasMore && !popupState.isLoadingMore ? "<span>Más resultados</span>" : ""}
                </div>`;

            body.innerHTML = `
                <table class="wlp-table">
                    <thead>
                        <tr>
                            <th class="wlp-col-check">✓</th>
                            <th class="wlp-col-photo">Foto</th>
                            <th>Lote</th>
                            <th>Bloque</th>
                            <th>Atado</th>
                            <th class="text-end">Alto</th>
                            <th class="text-end">Ancho</th>
                            <th class="text-end">Esp.</th>
                            <th class="text-end">M²</th>
                            <th>Tipo</th>
                            <th>Color</th>
                            <th>Ubic.</th>
                            <th>Estado</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
                ${sentinel}`;

            body.querySelectorAll("tr[data-lot-id]").forEach((tr) => {
                tr.addEventListener("click", () => {
                    const lotId = parseInt(tr.dataset.lotId, 10);

                    if (!lotId) return;

                    if (popupState.pendingIds.has(lotId)) {
                        popupState.pendingIds.delete(lotId);
                    } else {
                        popupState.pendingIds.add(lotId);
                    }

                    renderTable();
                });
            });

            if (self._popupObserver) {
                self._popupObserver.disconnect();
                self._popupObserver = null;
            }

            const sentinelEl = body.querySelector("#wlp-sentinel");

            if (sentinelEl && popupState.hasMore) {
                self._popupObserver = new IntersectionObserver(
                    (entries) => {
                        if (entries[0].isIntersecting && popupState.hasMore && !popupState.isLoadingMore) {
                            loadPage(popupState.page + 1, false);
                        }
                    },
                    { root: body, rootMargin: "140px", threshold: 0.1 }
                );

                self._popupObserver.observe(sentinelEl);
            }
        };

        const loadPage = async (page, reset) => {
            if (reset) {
                popupState.isLoading = true;
                popupState.quants = [];
                popupState.page = 0;
                popupState.qtyCache = {};
                popupState.cachedQuantIds = new Set();

                stat.innerHTML = `<i class="fa fa-circle-o-notch fa-spin"></i> Buscando...`;
                body.innerHTML = `
                    <div class="wlp-empty">
                        <i class="fa fa-circle-o-notch fa-spin"></i>
                        <span>Buscando inventario...</span>
                    </div>`;
            } else {
                popupState.isLoadingMore = true;
            }

            try {
                const result = await self.orm.call(
                    "stock.quant",
                    "search_workshop_lot_inventory_paginated",
                    [],
                    {
                        product_id: productId,
                        filters: popupState.filters,
                        current_lot_ids: Array.from(popupState.pendingIds),
                        page,
                        page_size: PAGE_SIZE,
                        location_id: self.getLocationSrcId() || false,
                        order_id: self.getOrderId() || false,
                    }
                );

                const items = result.items || [];

                cacheQuantList(items);

                popupState.quants = reset || page === 0
                    ? items
                    : [...popupState.quants, ...items];

                popupState.totalCount = result.total || 0;
                popupState.page = page;
                popupState.hasMore = popupState.quants.length < popupState.totalCount;

                await ensureQtyCacheForPending();
            } catch (error) {
                console.error("[WORKSHOP LOT SELECTOR] Error:", error);

                body.innerHTML = `
                    <div class="wlp-empty is-error">
                        <i class="fa fa-exclamation-triangle"></i>
                        <span>${self._escapeHtml(error.message || error.toString())}</span>
                    </div>`;

                return;
            } finally {
                popupState.isLoading = false;
                popupState.isLoadingMore = false;
            }

            renderTable();
        };

        const bindFilter = (id, key) => {
            const input = root.querySelector(`#${id}`);

            if (!input) return;

            const handler = (ev) => {
                popupState.filters[key] = ev.target.value;

                if (searchTimeout) clearTimeout(searchTimeout);

                searchTimeout = setTimeout(() => loadPage(0, true), 350);
            };

            input.addEventListener("input", handler);
            input.addEventListener("change", handler);
        };

        const doConfirm = async () => {
            const selected = Array.from(popupState.pendingIds);

            try {
                await self._rebuildInputLines(selected);
                self.destroyPopup();
            } catch (error) {
                console.error("[WORKSHOP LOT SELECTOR] Confirm error:", error);
                self._notify(error.message || "No se pudo actualizar la selección de lotes.", "danger");
            }
        };

        const doClose = () => this.destroyPopup();

        root.querySelector("#wlp-close").addEventListener("click", doClose);
        root.querySelector("#wlp-cancel").addEventListener("click", doClose);
        root.querySelector("#wlp-confirm-top").addEventListener("click", doConfirm);
        root.querySelector("#wlp-confirm-bottom").addEventListener("click", doConfirm);

        root.querySelector("#wlp-select-all").addEventListener("click", () => {
            for (const quant of popupState.quants) {
                if (quant.lot_id && quant.lot_id[0]) {
                    popupState.pendingIds.add(quant.lot_id[0]);
                }
            }

            renderTable();
        });

        root.querySelector("#wlp-clear").addEventListener("click", () => {
            popupState.pendingIds = new Set();
            renderTable();
        });

        root.querySelector("#wlp-overlay").addEventListener("click", (ev) => {
            if (ev.target.id === "wlp-overlay") doClose();
        });

        const keyHandler = (ev) => {
            if (ev.key === "Escape") doClose();
        };

        document.addEventListener("keydown", keyHandler);
        this._popupKeyHandler = keyHandler;

        bindFilter("wlf-lot", "lot_name");
        bindFilter("wlf-bloque", "bloque");
        bindFilter("wlf-atado", "atado");
        bindFilter("wlf-alto", "alto_min");
        bindFilter("wlf-ancho", "ancho_min");
        bindFilter("wlf-tipo", "tipo");

        await loadPage(0, true);
    }

    destroyPopup() {
        if (this._popupObserver) {
            this._popupObserver.disconnect();
            this._popupObserver = null;
        }

        if (this._popupKeyHandler) {
            document.removeEventListener("keydown", this._popupKeyHandler);
            this._popupKeyHandler = null;
        }

        if (this._popupRoot) {
            this._popupRoot.remove();
            this._popupRoot = null;
        }
    }
}

registry.category("fields").add("workshop_lot_selector", {
    component: WorkshopLotSelector,
    displayName: "Selector visual de lotes de taller",
});```

## ./static/src/components/workshop_lot_selector/workshop_lot_selector.xml
```xml
<?xml version="1.0" encoding="UTF-8"?>
<templates xml:space="preserve">
    <t t-name="stone_workshop.WorkshopLotSelector" owl="1">
        <div class="wls-panel" t-att-class="canEdit() ? '' : 'is-readonly'">
            <div class="wls-header">
                <div class="wls-title-block">
                    <span class="wls-icon"><i class="fa fa-th-large"/></span>
                    <div>
                        <h3>Selector visual de lotes de entrada</h3>
                        <p t-if="getProductId()">
                            Producto: <strong><t t-esc="getProductName()"/></strong>
                        </p>
                        <p t-else="">Selecciona primero un producto de entrada.</p>
                    </div>
                </div>
                <div class="wls-actions">
                    <span class="wls-pill wls-pill-count">
                        <i class="fa fa-check-circle"/> <t t-esc="selectedRows.length"/> lotes
                    </span>
                    <span class="wls-pill wls-pill-area">
                        <i class="fa fa-balance-scale"/> <t t-esc="formatNum(selectedArea)"/> m²
                    </span>
                    <button type="button"
                            class="wls-btn wls-btn-primary"
                            t-on-click="openPopup"
                            t-att-disabled="!canEdit() or !getProductId()">
                        <i class="fa fa-plus"/> Seleccionar lotes
                    </button>
                </div>
            </div>

            <div t-if="!getProductId()" class="wls-empty wls-empty-warning">
                <i class="fa fa-info-circle"/>
                <span>Define el producto de entrada para cargar el inventario disponible.</span>
            </div>

            <div t-elif="selectedRows.length === 0" class="wls-empty">
                <i class="fa fa-cubes"/>
                <span>No hay lotes seleccionados para esta orden.</span>
            </div>

            <div t-else="" class="wls-table-wrap">
                <table class="wls-table">
                    <thead>
                        <tr>
                            <th>Lote</th>
                            <th>Producto</th>
                            <th>Bloque</th>
                            <th>Tono</th>
                            <th class="text-end">Cant.</th>
                            <th class="text-end">M²</th>
                            <th class="text-end">Alto</th>
                            <th class="text-end">Ancho</th>
                            <th class="text-end">Esp.</th>
                            <th>Ubicación</th>
                            <th>Estado</th>
                            <th t-if="canEdit()" class="wls-col-action"></th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr t-foreach="selectedRows" t-as="row" t-key="row.key">
                            <td class="wls-cell-lot"><t t-esc="row.lot_name"/></td>
                            <td><t t-esc="row.product_name"/></td>
                            <td><t t-esc="row.block_name || '-'"/></td>
                            <td><t t-esc="row.tone || '-'"/></td>
                            <td class="text-end"><t t-esc="formatNum(row.qty_in)"/></td>
                            <td class="text-end"><t t-esc="formatNum(row.area_sqm)"/></td>
                            <td class="text-end"><t t-esc="formatDim(row.height_cm)"/></td>
                            <td class="text-end"><t t-esc="formatDim(row.width_cm)"/></td>
                            <td class="text-end"><t t-esc="formatDim(row.thickness_cm)"/></td>
                            <td class="text-muted"><t t-esc="row.location_name || '-'"/></td>
                            <td><span t-att-class="'wls-state wls-state-' + row.state"><t t-esc="row.state_label"/></span></td>
                            <td t-if="canEdit()" class="wls-col-action">
                                <button type="button" class="wls-remove" t-on-click="(ev) => this.removeLot(row.lot_id, ev)" title="Quitar lote">
                                    <i class="fa fa-times"/>
                                </button>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </t>
</templates>
```

## ./static/src/js/workshop_dashboard.js
```js
/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const STATE_LABELS = {
    draft: "Borrador",
    in_workshop: "En taller",
    done: "Terminada",
    cancel: "Cancelada",
};

const PRIORITY_LABELS = {
    "0": "Normal",
    "1": "Alta",
    "2": "Urgente",
};

const MODE_LABELS = {
    slab_finish: "Acabado de placas",
    slab_cut: "Corte de placas",
    format_process: "Formatos / pallets",
    rework: "Reproceso",
};

const MODE_CARDS = [
    {
        mode: "slab_finish",
        title: "Acabado",
        subtitle: "Placa a placa",
        icon: "✦",
    },
    {
        mode: "slab_cut",
        title: "Corte",
        subtitle: "Demanda en m² con retazos",
        icon: "◫",
    },
    {
        mode: "format_process",
        title: "Formatos",
        subtitle: "Pallet o piezas por m²",
        icon: "▦",
    },
    {
        mode: "rework",
        title: "Reproceso",
        subtitle: "Reparación o reclasificación",
        icon: "↻",
    },
];

function fmt(value, decimals = 2) {
    if (value === null || value === undefined) return "0";
    const num = typeof value === "number" ? value : parseFloat(value);
    if (Number.isNaN(num)) return "0";
    return num.toFixed(decimals);
}

class StoneWorkshopDashboard extends Component {
    static template = "stone_workshop.Dashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            modeCards: MODE_CARDS,
            modeLabels: MODE_LABELS,
            kpis: {
                draft: 0,
                in_workshop: 0,
                done_today: 0,
                area_today: 0,
            },
            modeStats: {
                slab_finish: 0,
                slab_cut: 0,
                format_process: 0,
                rework: 0,
            },
            priorityQueue: [],
            executingOrders: [],
            recentDone: [],
            loading: true,
            lastRefresh: null,
        });

        onWillStart(async () => {
            await this.loadDashboard();
        });
    }

    async loadDashboard() {
        this.state.loading = true;
        try {
            await Promise.all([
                this.loadKpis(),
                this.loadPriorityQueue(),
                this.loadExecuting(),
                this.loadRecentDone(),
            ]);
        } finally {
            this.state.loading = false;
            const d = new Date();
            this.state.lastRefresh =
                d.getHours().toString().padStart(2, "0") +
                ":" +
                d.getMinutes().toString().padStart(2, "0");
        }
    }

    _decorate(order) {
        return {
            ...order,
            state_label: STATE_LABELS[order.state] || order.state,
            priority_label: PRIORITY_LABELS[order.priority] || PRIORITY_LABELS["0"],
            mode_label: MODE_LABELS[order.operation_mode] || order.operation_mode,
        };
    }

    async loadKpis() {
        const today = new Date();
        const todayStart =
            today.getFullYear() +
            "-" +
            String(today.getMonth() + 1).padStart(2, "0") +
            "-" +
            String(today.getDate()).padStart(2, "0") +
            " 00:00:00";

        const [active, doneToday] = await Promise.all([
            this.orm.searchRead(
                "workshop.order",
                [["state", "in", ["draft", "in_workshop"]]],
                ["state", "operation_mode"],
            ),
            this.orm.searchRead(
                "workshop.order",
                [
                    ["state", "=", "done"],
                    ["date_done", ">=", todayStart],
                ],
                ["area_out_total"],
            ),
        ]);

        this.state.kpis = {
            draft: active.filter((o) => o.state === "draft").length,
            in_workshop: active.filter((o) => o.state === "in_workshop").length,
            done_today: doneToday.length,
            area_today: doneToday.reduce((s, o) => s + (o.area_out_total || 0), 0),
        };
        this.state.modeStats = {
            slab_finish: active.filter((o) => o.operation_mode === "slab_finish").length,
            slab_cut: active.filter((o) => o.operation_mode === "slab_cut").length,
            format_process: active.filter((o) => o.operation_mode === "format_process").length,
            rework: active.filter((o) => o.operation_mode === "rework").length,
        };
    }

    async loadPriorityQueue() {
        const orders = await this.orm.searchRead(
            "workshop.order",
            [["state", "=", "draft"]],
            [
                "name",
                "priority",
                "process_id",
                "operation_mode",
                "responsible_id",
                "date_planned",
                "production_target_sqm",
                "area_in_total",
                "input_count",
                "state",
            ],
            { order: "priority desc, date_planned asc, id asc", limit: 15 },
        );
        this.state.priorityQueue = orders.map((o, idx) => ({
            ...this._decorate(o),
            is_next: idx === 0,
        }));
    }

    async loadExecuting() {
        const orders = await this.orm.searchRead(
            "workshop.order",
            [["state", "=", "in_workshop"]],
            [
                "name",
                "priority",
                "process_id",
                "operation_mode",
                "responsible_id",
                "date_start",
                "production_target_sqm",
                "area_in_total",
                "area_out_total",
                "progress_log_count",
                "input_count",
                "state",
            ],
            { order: "priority desc, date_start asc, id asc", limit: 15 },
        );
        this.state.executingOrders = orders.map((o) => {
            const target = o.production_target_sqm || o.area_in_total || 0;
            const done = o.area_out_total || 0;
            const progress = target > 0 ? Math.min(100, Math.round((done / target) * 100)) : 0;
            return {
                ...this._decorate(o),
                progress,
                target_area: target,
                done_area: done,
            };
        });
    }

    async loadRecentDone() {
        const orders = await this.orm.searchRead(
            "workshop.order",
            [["state", "=", "done"]],
            [
                "name",
                "process_id",
                "operation_mode",
                "responsible_id",
                "date_done",
                "area_in_total",
                "area_out_total",
                "yield_percent",
            ],
            { order: "date_done desc, id desc", limit: 6 },
        );
        this.state.recentDone = orders.map((o) => this._decorate(o));
    }

    async setPriority(orderId, newPriority) {
        await this.orm.write("workshop.order", [orderId], { priority: String(newPriority) });
        await Promise.all([this.loadPriorityQueue(), this.loadExecuting()]);
    }

    bumpPriority(order, direction) {
        const current = parseInt(order.priority || "0", 10);
        const next = Math.max(0, Math.min(2, current + direction));
        if (next !== current) {
            this.setPriority(order.id, next);
        }
    }

    fmt(value, decimals = 2) {
        return fmt(value, decimals);
    }

    priorityStars(priority) {
        if (priority === "2") return "★★★";
        if (priority === "1") return "★★";
        return "★";
    }

    openNew(mode) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Nueva orden de taller",
            res_model: "workshop.order",
            views: [[false, "form"]],
            target: "current",
            context: { default_operation_mode: mode },
        });
    }

    openOrders(domain = []) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Órdenes de Taller",
            res_model: "workshop.order",
            views: [
                [false, "kanban"],
                [false, "list"],
                [false, "form"],
            ],
            target: "current",
            domain,
        });
    }

    openOrder(orderId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "workshop.order",
            res_id: orderId,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("stone_workshop_dashboard", StoneWorkshopDashboard);
```

## ./static/src/scss/workshop_lot_selector.scss
```scss
// Stone Workshop — Selector visual de lotes de entrada
// Homologado visualmente con el selector de placas usado en ventas, pero aislado al módulo de taller.

$wls-bg: #eef8fe;
$wls-panel: #ffffff;
$wls-panel-soft: #f8fcff;
$wls-text: #0f172a;
$wls-muted: #64748b;
$wls-border: #d7e8f2;
$wls-border-strong: #bdd7e8;
$wls-blue: #5CB9F2;
$wls-blue-2: #4BA4F2;
$wls-blue-dark: #155f94;
$wls-green: #04D94F;
$wls-green-dark: #047a31;
$wls-amber: #F2B705;
$wls-red: #dc2626;
$wls-head: #0f172a;
$wls-radius: 18px;
$wls-pill: 999px;
$wls-shadow: 0 12px 34px rgba(75, 164, 242, 0.14);
$wls-popup-shadow: 0 34px 90px rgba(15, 23, 42, 0.34), 0 8px 30px rgba(75, 164, 242, 0.12);

@mixin wls-scrollbar {
    &::-webkit-scrollbar {
        width: 10px;
        height: 10px;
    }

    &::-webkit-scrollbar-track {
        background: rgba(220, 234, 242, 0.65);
        border-radius: $wls-pill;
    }

    &::-webkit-scrollbar-thumb {
        background: rgba(92, 185, 242, 0.62);
        border: 2px solid rgba(238, 248, 254, 0.92);
        border-radius: $wls-pill;
    }
}

@mixin wls-btn-primary {
    border: 1px solid rgba(92, 185, 242, 0.70);
    background: linear-gradient(180deg, $wls-blue, $wls-blue-2);
    color: #ffffff !important;
    box-shadow: 0 10px 22px rgba(75, 164, 242, 0.20);
    font-weight: 900;

    &:hover:not(:disabled) {
        transform: translateY(-1px);
        box-shadow: 0 12px 28px rgba(75, 164, 242, 0.26);
    }
}

.sw-input-selector-config {
    margin-bottom: 8px;
}

.wls-panel,
.wlp-root {
    font-family: "Inter", "SF Pro Display", "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    color: $wls-text;
    letter-spacing: -0.01em;

    .text-muted { color: $wls-muted !important; }
    .fw-bold { font-weight: 900 !important; }
    .text-end { text-align: right; }
}

.wls-panel {
    overflow: hidden;
    margin: 8px 0 14px;
    border: 1px solid rgba(215, 232, 242, 0.96);
    border-radius: 22px;
    background:
        radial-gradient(circle at top left, rgba(92, 185, 242, 0.16), transparent 34%),
        linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(248, 252, 255, 0.96));
    box-shadow: 0 8px 22px rgba(75, 164, 242, 0.10);

    &.is-readonly {
        .wls-btn-primary,
        .wls-remove {
            opacity: 0.55;
            cursor: not-allowed;
        }
    }
}

.wls-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 16px 18px;
    border-bottom: 1px solid rgba(215, 232, 242, 0.96);
    background: rgba(255, 255, 255, 0.82);
}

.wls-title-block {
    display: flex;
    align-items: center;
    min-width: 0;
    gap: 12px;

    h3 {
        margin: 0;
        font-size: 17px;
        font-weight: 950;
        color: $wls-text;
        letter-spacing: -0.035em;
    }

    p {
        margin: 3px 0 0;
        color: $wls-muted;
        font-size: 12px;
        font-weight: 750;
    }
}

.wls-icon {
    width: 42px;
    height: 42px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid rgba(92, 185, 242, 0.42);
    border-radius: 16px;
    background: linear-gradient(145deg, rgba(92, 185, 242, 0.30), rgba(220, 234, 242, 0.62));
    color: $wls-blue-dark;
    box-shadow: 0 12px 26px rgba(92, 185, 242, 0.18);
}

.wls-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    flex-wrap: wrap;
}

.wls-pill,
.wlp-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-height: 30px;
    padding: 6px 11px;
    border-radius: $wls-pill;
    border: 1px solid rgba(215, 232, 242, 0.96);
    background: rgba(255, 255, 255, 0.88);
    color: $wls-muted;
    font-size: 11px;
    font-weight: 950;
    white-space: nowrap;
}

.wls-pill-count,
.wlp-badge {
    border-color: rgba(4, 217, 79, 0.24);
    background: rgba(4, 217, 79, 0.12);
    color: $wls-green-dark;
}

.wls-pill-area,
.wlp-badge-area {
    border-color: rgba(92, 185, 242, 0.30);
    background: rgba(92, 185, 242, 0.14);
    color: $wls-blue-dark;
}

.wls-btn,
.wlp-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    min-height: 32px;
    padding: 7px 14px;
    border-radius: $wls-pill;
    border: 1px solid $wls-border-strong;
    background: rgba(255, 255, 255, 0.92);
    color: $wls-blue-dark;
    cursor: pointer;
    font-size: 11.5px;
    font-weight: 900;
    line-height: 1;
    white-space: nowrap;
    transition: all 0.16s ease;

    &:disabled {
        opacity: 0.55;
        cursor: not-allowed;
        transform: none !important;
        box-shadow: none !important;
    }
}

.wls-btn-primary,
.wlp-btn-primary {
    @include wls-btn-primary;
}

.wls-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 9px;
    min-height: 96px;
    color: $wls-muted;
    background: #ffffff;
    font-size: 12px;
    font-weight: 800;

    i {
        color: $wls-blue;
        font-size: 18px;
    }

    &.wls-empty-warning {
        background: linear-gradient(180deg, #fffdf3, rgba(242, 183, 5, 0.12));
        color: #8f6500;

        i { color: #8f6500; }
    }
}

.wls-table-wrap {
    max-height: 330px;
    overflow: auto;
    background: #ffffff;
    @include wls-scrollbar;
}

.wls-table,
.wlp-table {
    width: 100%;
    min-width: 1040px;
    border-collapse: separate;
    border-spacing: 0;
    font-size: 11px;
    background: #ffffff;

    thead {
        position: sticky;
        top: 0;
        z-index: 4;

        tr {
            background: $wls-head;
        }

        th {
            position: sticky;
            top: 0;
            padding: 10px 9px;
            border-bottom: 1px solid rgba(15, 23, 42, 0.96);
            background: linear-gradient(180deg, #111827 0%, $wls-head 100%) !important;
            color: rgba(255, 255, 255, 0.96) !important;
            font-size: 9px;
            font-weight: 850;
            text-align: left;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            white-space: nowrap;
        }
    }

    tbody tr {
        background: #ffffff;
        transition: background 0.16s ease, box-shadow 0.16s ease;

        &:nth-child(even) {
            background: rgba(248, 252, 255, 0.72);
        }

        &:hover {
            background: rgba(92, 185, 242, 0.13);
        }
    }

    td {
        padding: 8px 9px;
        border-bottom: 1px solid rgba(215, 232, 242, 0.94);
        color: #284256;
        vertical-align: middle;
        white-space: nowrap;
        font-weight: 550;
    }
}

.wls-cell-lot,
.wlp-cell-lot {
    color: $wls-blue-dark !important;
    font-family: "JetBrains Mono", "Fira Code", "SFMono-Regular", Consolas, monospace;
    font-weight: 950 !important;
    letter-spacing: -0.02em;
}

.wls-state,
.wlp-tag,
.wlp-type {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 18px;
    padding: 3px 8px;
    border-radius: $wls-pill;
    border: 1px solid rgba(215, 232, 242, 0.96);
    background: rgba(220, 234, 242, 0.72);
    color: $wls-muted;
    font-size: 9.5px;
    font-weight: 950;
    line-height: 1;
    white-space: nowrap;
}

.wls-state-done,
.wlp-tag-selected {
    border-color: rgba(4, 217, 79, 0.30);
    background: rgba(4, 217, 79, 0.12);
    color: $wls-green-dark;
}

.wls-state-sent_to_workshop,
.wls-state-in_progress,
.wls-state-partial_done {
    border-color: rgba(242, 183, 5, 0.30);
    background: rgba(242, 183, 5, 0.15);
    color: #8f6500;
}

.wls-state-rejected,
.wls-state-damaged,
.wls-state-cancelled {
    border-color: rgba(220, 38, 38, 0.28);
    background: rgba(220, 38, 38, 0.08);
    color: #991b1b;
}

.wls-col-action {
    width: 38px;
    text-align: center;
}

.wls-remove {
    width: 25px;
    height: 25px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid rgba(220, 38, 38, 0.22);
    border-radius: 10px;
    background: rgba(220, 38, 38, 0.08);
    color: #991b1b;
    cursor: pointer;
    transition: all 0.16s ease;

    &:hover {
        background: $wls-red;
        border-color: $wls-red;
        color: #ffffff;
        transform: translateY(-1px);
    }
}

// Fullscreen popup
.wlp-root {
    position: fixed;
    inset: 0;
    z-index: 10500;
}

.wlp-overlay {
    position: fixed;
    inset: 0;
    display: flex;
    align-items: stretch;
    justify-content: stretch;
    padding: 16px;
    background:
        radial-gradient(circle at 20% 10%, rgba(92, 185, 242, 0.24), transparent 28%),
        radial-gradient(circle at 84% 0%, rgba(75, 164, 242, 0.18), transparent 36%),
        rgba(15, 23, 42, 0.58);
    backdrop-filter: blur(5px);
}

.wlp-container {
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    border: 1px solid rgba(215, 232, 242, 0.96);
    border-radius: 30px;
    background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(248, 252, 255, 0.98) 34%, rgba(238, 248, 254, 0.98)),
        #ffffff;
    box-shadow: $wls-popup-shadow;
}

.wlp-header {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 18px;
    padding: 20px 24px 18px;
    border-bottom: 1px solid rgba(215, 232, 242, 0.96);
    background:
        linear-gradient(135deg, rgba(255, 255, 255, 0.98), rgba(248, 252, 255, 0.94)),
        linear-gradient(90deg, rgba(92, 185, 242, 0.12), rgba(220, 234, 242, 0.40));
}

.wlp-title {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;

    > i {
        width: 42px;
        height: 42px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border: 1px solid rgba(92, 185, 242, 0.42);
        border-radius: 16px;
        background: linear-gradient(145deg, rgba(92, 185, 242, 0.30), rgba(220, 234, 242, 0.62));
        color: $wls-blue-dark;
        box-shadow: 0 12px 26px rgba(92, 185, 242, 0.18);
    }

    strong {
        display: block;
        color: $wls-text;
        font-size: 22px;
        font-weight: 950;
        line-height: 1.1;
        letter-spacing: -0.035em;
    }

    span {
        display: block;
        margin-top: 3px;
        color: $wls-muted;
        font-size: 12px;
        font-weight: 750;
    }
}

.wlp-header-actions,
.wlp-footer-actions,
.wlp-filter-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
}

.wlp-btn-ghost {
    width: 36px;
    min-width: 36px;
    padding: 0;

    &:hover {
        border-color: rgba(220, 38, 38, 0.28);
        background: rgba(220, 38, 38, 0.10);
        color: #991b1b;
    }
}

.wlp-btn-outline {
    border-color: rgba(92, 185, 242, 0.42);
    color: $wls-blue-dark;
}

.wlp-btn-soft {
    border-color: rgba(4, 217, 79, 0.24);
    background: rgba(4, 217, 79, 0.12);
    color: $wls-green-dark;
}

.wlp-btn-danger-soft {
    border-color: rgba(220, 38, 38, 0.20);
    background: rgba(220, 38, 38, 0.07);
    color: #991b1b;
}

.wlp-filters {
    flex: 0 0 auto;
    display: flex;
    align-items: flex-end;
    gap: 10px;
    padding: 13px 16px;
    flex-wrap: wrap;
    border-bottom: 1px solid rgba(215, 232, 242, 0.96);
    background: rgba(255, 255, 255, 0.92);
    box-shadow: 0 8px 18px rgba(15, 23, 42, 0.04);

    label {
        display: flex;
        flex-direction: column;
        gap: 4px;
        color: $wls-muted;
        font-size: 9.5px;
        font-weight: 950;
        text-transform: uppercase;
        letter-spacing: 0.10em;
    }

    input,
    select {
        width: 126px;
        min-height: 34px;
        padding: 7px 10px;
        border: 1px solid $wls-border-strong;
        border-radius: 12px;
        background: rgba(255, 255, 255, 0.98);
        color: $wls-text;
        box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.04);
        font-size: 11.5px;
        font-weight: 800;
        text-transform: none;
        letter-spacing: 0;

        &:focus {
            outline: none;
            border-color: $wls-blue;
            box-shadow: 0 0 0 4px rgba(92, 185, 242, 0.18);
        }
    }
}

.wlp-spacer {
    flex: 1 1 auto;
}

.wlp-stat {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    min-height: 30px;
    padding: 6px 11px;
    border-radius: $wls-pill;
    border: 1px solid rgba(92, 185, 242, 0.28);
    background: rgba(255, 255, 255, 0.92);
    color: $wls-blue-dark;
    font-size: 11px;
    font-weight: 900;
}

.wlp-body {
    flex: 1 1 auto;
    min-height: 0;
    overflow: auto;
    position: relative;
    background: #ffffff;
    @include wls-scrollbar;
}

.wlp-empty {
    min-height: 240px;
    height: 58%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    color: $wls-muted;
    font-size: 13px;
    font-weight: 800;

    i {
        color: $wls-blue;
        opacity: 0.84;
        font-size: 38px;
    }

    &.is-error {
        color: #991b1b;

        i { color: #991b1b; }
    }
}

.wlp-table {
    min-width: 1180px;

    tbody tr {
        cursor: pointer;

        &.is-selected {
            background: linear-gradient(90deg, rgba(4, 217, 79, 0.13), rgba(255, 255, 255, 0.96));

            td:first-child {
                box-shadow: inset 5px 0 0 $wls-green;
            }
        }
    }
}

.wlp-col-check {
    width: 42px;
    text-align: center !important;
}

.wlp-col-photo {
    width: 48px;
    min-width: 48px;
    max-width: 48px;
    text-align: center !important;
}

.wlp-check {
    width: 18px;
    height: 18px;
    margin: 0 auto;
    border: 2px solid rgba(92, 185, 242, 0.42);
    border-radius: 7px;
    background: #ffffff;
    display: inline-flex;
    align-items: center;
    justify-content: center;

    i {
        color: #ffffff;
        font-size: 9px;
    }
}

tr.is-selected .wlp-check {
    background: linear-gradient(135deg, $wls-blue, $wls-blue-2);
    border-color: $wls-blue-dark;
}

.wlp-photo {
    width: 38px;
    height: 38px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    border-radius: 13px;
    border: 1px solid rgba(92, 185, 242, 0.26);
    background: #DCEAF2;
    color: $wls-muted;

    img {
        width: 38px;
        height: 38px;
        object-fit: cover;
    }
}

.wlp-tag-free {
    border-color: rgba(4, 217, 79, 0.18);
    background: rgba(255, 255, 255, 0.92);
    color: $wls-green-dark;
}

.wlp-footer {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    padding: 14px 18px;
    border-top: 1px solid rgba(215, 232, 242, 0.96);
    background: #ffffff;
    color: $wls-muted;
    font-size: 11.5px;
    font-weight: 800;

    strong {
        color: $wls-text;
        font-weight: 950;
    }
}

.wlp-sentinel {
    padding: 14px;
    text-align: center;
    background: rgba(248, 252, 255, 0.72);
    color: $wls-muted;
    font-size: 11px;
    font-weight: 800;
}

@media (max-width: 992px) {
    .wls-header,
    .wlp-header {
        align-items: flex-start;
        flex-direction: column;
    }

    .wls-actions,
    .wlp-header-actions {
        justify-content: flex-start;
    }

    .wlp-overlay {
        padding: 8px;
    }

    .wlp-container {
        border-radius: 22px;
    }
}

// ───────────────────────────────────────────────────────────────────────────
// Ajuste de ancho completo en formulario de Orden de Taller
// ───────────────────────────────────────────────────────────────────────────
.o_form_view .sw-input-selector-full {
    width: 100% !important;
    max-width: none !important;
    display: block !important;
    clear: both;
    grid-column: 1 / -1 !important;
    flex: 0 0 100% !important;
}

.o_form_view .sw-input-selector-full > .o_field_widget,
.o_form_view .sw-input-selector-full .o_field_widget,
.o_form_view .sw-input-selector-full .wls-panel {
    width: 100% !important;
    max-width: none !important;
    display: block !important;
    box-sizing: border-box;
}

.o_form_view .wls-panel {
    width: 100% !important;
    max-width: none !important;
}

.o_form_view .wls-table-wrap,
.o_form_view .wls-table {
    width: 100% !important;
}
```

## ./static/src/xml/workshop_templates.xml
```xml
<?xml version="1.0" encoding="UTF-8"?>
<templates xml:space="preserve">
    <t t-name="stone_workshop.Dashboard">
        <div class="sw2-app">

            <!-- ========== HEADER ========== -->
            <header class="sw2-header">
                <div class="sw2-header-text">
                    <h1>Taller de Piedra</h1>
                    <p>Cola priorizada, ejecución en vivo y cierre del día.</p>
                </div>
                <div class="sw2-header-actions">
                    <button class="sw2-btn sw2-btn-ghost" t-on-click="loadDashboard">
                        <span class="sw2-btn-icon">↻</span> Actualizar
                        <small t-if="state.lastRefresh" class="sw2-btn-meta"><t t-esc="state.lastRefresh"/></small>
                    </button>
                    <button class="sw2-btn sw2-btn-secondary" t-on-click="() => this.openOrders()">
                        Todas las órdenes
                    </button>
                </div>
            </header>

            <!-- ========== KPIs ========== -->
            <section class="sw2-kpis">
                <button class="sw2-kpi sw2-kpi-draft" t-on-click="() => this.openOrders([['state', '=', 'draft']])">
                    <span class="sw2-kpi-label">Borradores en cola</span>
                    <strong class="sw2-kpi-value"><t t-esc="state.kpis.draft"/></strong>
                    <span class="sw2-kpi-foot">esperando confirmar taller</span>
                </button>
                <button class="sw2-kpi sw2-kpi-active" t-on-click="() => this.openOrders([['state', '=', 'in_workshop']])">
                    <span class="sw2-kpi-label">En taller</span>
                    <strong class="sw2-kpi-value"><t t-esc="state.kpis.in_workshop"/></strong>
                    <span class="sw2-kpi-foot">en ejecución ahora</span>
                </button>
                <button class="sw2-kpi sw2-kpi-done" t-on-click="() => this.openOrders([['state', '=', 'done']])">
                    <span class="sw2-kpi-label">Cerradas hoy</span>
                    <strong class="sw2-kpi-value"><t t-esc="state.kpis.done_today"/></strong>
                    <span class="sw2-kpi-foot">resultado declarado</span>
                </button>
                <div class="sw2-kpi sw2-kpi-area">
                    <span class="sw2-kpi-label">m² producidos hoy</span>
                    <strong class="sw2-kpi-value"><t t-esc="fmt(state.kpis.area_today)"/></strong>
                    <span class="sw2-kpi-foot">salida útil declarada</span>
                </div>
            </section>

            <!-- ========== COLA + EJECUCIÓN ========== -->
            <section class="sw2-board">

                <!-- Cola priorizada -->
                <div class="sw2-card sw2-card-queue">
                    <div class="sw2-card-head">
                        <div>
                            <h2>Cola priorizada</h2>
                            <p>Borradores. Usa ▲▼ para reordenar — la primera es la siguiente a iniciar.</p>
                        </div>
                        <span class="sw2-count"><t t-esc="state.priorityQueue.length"/></span>
                    </div>

                    <div class="sw2-card-body">
                        <t t-if="state.priorityQueue.length">
                            <t t-foreach="state.priorityQueue" t-as="order" t-key="order.id">
                                <div t-att-class="'sw2-row sw2-row-queue' + (order.is_next ? ' is-next' : '')">

                                    <div class="sw2-row-priority">
                                        <span t-att-class="'sw2-stars sw2-stars-' + (order.priority || '0')"
                                              t-att-title="order.priority_label">
                                            <t t-esc="priorityStars(order.priority || '0')"/>
                                        </span>
                                        <div class="sw2-prio-controls">
                                            <button type="button" class="sw2-prio-btn"
                                                    t-att-disabled="order.priority === '2'"
                                                    t-on-click.stop="() => this.bumpPriority(order, 1)"
                                                    title="Subir prioridad">▲</button>
                                            <button type="button" class="sw2-prio-btn"
                                                    t-att-disabled="order.priority === '0'"
                                                    t-on-click.stop="() => this.bumpPriority(order, -1)"
                                                    title="Bajar prioridad">▼</button>
                                        </div>
                                    </div>

                                    <div class="sw2-row-main" t-on-click="() => this.openOrder(order.id)">
                                        <div class="sw2-row-title">
                                            <t t-if="order.is_next"><span class="sw2-badge-next">SIGUIENTE</span></t>
                                            <strong><t t-esc="order.name"/></strong>
                                        </div>
                                        <div class="sw2-row-meta">
                                            <span><t t-esc="order.process_id and order.process_id[1] or ''"/></span>
                                            <span class="sw2-dot">·</span>
                                            <span><t t-esc="order.mode_label"/></span>
                                            <t t-if="order.responsible_id">
                                                <span class="sw2-dot">·</span>
                                                <span><t t-esc="order.responsible_id[1]"/></span>
                                            </t>
                                        </div>
                                    </div>

                                    <div class="sw2-row-figures">
                                        <div t-if="order.production_target_sqm">
                                            <strong><t t-esc="fmt(order.production_target_sqm)"/></strong>
                                            <span>m² objetivo</span>
                                        </div>
                                        <div class="sw2-muted">
                                            <strong><t t-esc="order.input_count"/></strong>
                                            <span>placas</span>
                                        </div>
                                    </div>
                                </div>
                            </t>
                        </t>
                        <div class="sw2-empty" t-if="!state.priorityQueue.length">
                            <div class="sw2-empty-icon">📋</div>
                            <strong>No hay borradores en cola.</strong>
                            <p>Crea una nueva orden desde las tarjetas de abajo.</p>
                        </div>
                    </div>
                </div>

                <!-- En ejecución -->
                <div class="sw2-card sw2-card-running">
                    <div class="sw2-card-head">
                        <div>
                            <h2>En ejecución</h2>
                            <p>Órdenes que ya están en taller — captura tu avance en la bitácora.</p>
                        </div>
                        <span class="sw2-count sw2-count-warning"><t t-esc="state.executingOrders.length"/></span>
                    </div>

                    <div class="sw2-card-body">
                        <t t-if="state.executingOrders.length">
                            <t t-foreach="state.executingOrders" t-as="order" t-key="order.id">
                                <div class="sw2-row sw2-row-running" t-on-click="() => this.openOrder(order.id)">

                                    <div class="sw2-row-priority sw2-no-controls">
                                        <span t-att-class="'sw2-stars sw2-stars-' + (order.priority || '0')"
                                              t-att-title="order.priority_label">
                                            <t t-esc="priorityStars(order.priority || '0')"/>
                                        </span>
                                    </div>

                                    <div class="sw2-row-main">
                                        <div class="sw2-row-title">
                                            <strong><t t-esc="order.name"/></strong>
                                        </div>
                                        <div class="sw2-row-meta">
                                            <span><t t-esc="order.process_id and order.process_id[1] or ''"/></span>
                                            <span class="sw2-dot">·</span>
                                            <span><t t-esc="order.mode_label"/></span>
                                            <t t-if="order.responsible_id">
                                                <span class="sw2-dot">·</span>
                                                <span><t t-esc="order.responsible_id[1]"/></span>
                                            </t>
                                        </div>
                                        <div class="sw2-progress" t-if="order.target_area">
                                            <div class="sw2-progress-bar"
                                                 t-attf-style="width: {{ order.progress }}%;"
                                                 t-att-class="order.progress >= 100 ? 'is-full' : ''"/>
                                            <span class="sw2-progress-text">
                                                <t t-esc="fmt(order.done_area)"/> / <t t-esc="fmt(order.target_area)"/> m²
                                                · <t t-esc="order.progress"/>%
                                            </span>
                                        </div>
                                    </div>

                                    <div class="sw2-row-figures">
                                        <div>
                                            <strong><t t-esc="order.progress_log_count"/></strong>
                                            <span>corridas</span>
                                        </div>
                                        <div class="sw2-muted">
                                            <strong><t t-esc="order.input_count"/></strong>
                                            <span>placas</span>
                                        </div>
                                    </div>
                                </div>
                            </t>
                        </t>
                        <div class="sw2-empty" t-if="!state.executingOrders.length">
                            <div class="sw2-empty-icon">⚙</div>
                            <strong>Nada en taller ahora.</strong>
                            <p>Confirma una orden de la cola para empezar a producir.</p>
                        </div>
                    </div>
                </div>
            </section>

            <!-- ========== QUICK START ========== -->
            <section class="sw2-quickstart">
                <h2>Nueva orden</h2>
                <div class="sw2-quick-grid">
                    <t t-foreach="state.modeCards" t-as="card" t-key="card.mode">
                        <button class="sw2-quick-card" t-on-click="() => this.openNew(card.mode)">
                            <span class="sw2-quick-icon"><t t-esc="card.icon"/></span>
                            <span class="sw2-quick-title"><t t-esc="card.title"/></span>
                            <span class="sw2-quick-sub"><t t-esc="card.subtitle"/></span>
                        </button>
                    </t>
                </div>
            </section>

            <!-- ========== FOOTER STATS ========== -->
            <section class="sw2-footer-grid">

                <div class="sw2-card sw2-card-mini">
                    <div class="sw2-card-head">
                        <h3>Actividad por modo</h3>
                    </div>
                    <div class="sw2-card-body">
                        <div class="sw2-mode-row" t-on-click="() => this.openOrders([['operation_mode', '=', 'slab_finish'], ['state', 'in', ['draft', 'in_workshop']]])">
                            <span><t t-esc="state.modeLabels.slab_finish"/></span>
                            <strong><t t-esc="state.modeStats.slab_finish"/></strong>
                        </div>
                        <div class="sw2-mode-row" t-on-click="() => this.openOrders([['operation_mode', '=', 'slab_cut'], ['state', 'in', ['draft', 'in_workshop']]])">
                            <span><t t-esc="state.modeLabels.slab_cut"/></span>
                            <strong><t t-esc="state.modeStats.slab_cut"/></strong>
                        </div>
                        <div class="sw2-mode-row" t-on-click="() => this.openOrders([['operation_mode', '=', 'format_process'], ['state', 'in', ['draft', 'in_workshop']]])">
                            <span><t t-esc="state.modeLabels.format_process"/></span>
                            <strong><t t-esc="state.modeStats.format_process"/></strong>
                        </div>
                        <div class="sw2-mode-row" t-on-click="() => this.openOrders([['operation_mode', '=', 'rework'], ['state', 'in', ['draft', 'in_workshop']]])">
                            <span><t t-esc="state.modeLabels.rework"/></span>
                            <strong><t t-esc="state.modeStats.rework"/></strong>
                        </div>
                    </div>
                </div>

                <div class="sw2-card sw2-card-mini">
                    <div class="sw2-card-head">
                        <h3>Cerradas recientes</h3>
                        <button class="sw2-link" t-on-click="() => this.openOrders([['state', '=', 'done']])">Ver todas →</button>
                    </div>
                    <div class="sw2-card-body">
                        <t t-if="state.recentDone.length">
                            <t t-foreach="state.recentDone" t-as="order" t-key="order.id">
                                <div class="sw2-done-row" t-on-click="() => this.openOrder(order.id)">
                                    <div>
                                        <strong><t t-esc="order.name"/></strong>
                                        <span><t t-esc="order.process_id and order.process_id[1] or ''"/></span>
                                    </div>
                                    <div class="sw2-done-meta">
                                        <span><t t-esc="fmt(order.area_out_total)"/> m² útil</span>
                                        <span t-if="order.yield_percent" class="sw2-yield">
                                            <t t-esc="fmt(order.yield_percent, 1)"/>% rend.
                                        </span>
                                    </div>
                                </div>
                            </t>
                        </t>
                        <div class="sw2-empty sw2-empty-small" t-if="!state.recentDone.length">
                            <p>Sin órdenes cerradas todavía.</p>
                        </div>
                    </div>
                </div>
            </section>

        </div>
    </t>
</templates>
```

## ./views/workshop_menus.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <menuitem id="menu_workshop_root"
              name="Taller de Piedra"
              parent="mrp.menu_mrp_root"
              action="action_workshop_dashboard"
              sequence="50"
              groups="stone_workshop.group_workshop_user"/>

    <menuitem id="menu_workshop_dashboard"
              name="Panel de Taller"
              parent="menu_workshop_root"
              action="action_workshop_dashboard"
              sequence="1"
              groups="stone_workshop.group_workshop_user"/>

    <menuitem id="menu_workshop_orders"
              name="Órdenes de Taller"
              parent="menu_workshop_root"
              action="action_workshop_order"
              sequence="10"
              groups="stone_workshop.group_workshop_user"/>

    <menuitem id="menu_workshop_trace"
              name="Trazabilidad"
              parent="menu_workshop_root"
              action="action_workshop_trace"
              sequence="20"
              groups="stone_workshop.group_workshop_user"/>

    <menuitem id="menu_workshop_config"
              name="Configuración"
              parent="menu_workshop_root"
              sequence="90"
              groups="stone_workshop.group_workshop_supervisor"/>

    <menuitem id="menu_workshop_process"
              name="Procesos"
              parent="menu_workshop_config"
              action="action_workshop_process"
              sequence="10"
              groups="stone_workshop.group_workshop_supervisor"/>

    <menuitem id="menu_workshop_input_lines"
              name="Entradas"
              parent="menu_workshop_config"
              action="action_workshop_input_line"
              sequence="20"
              groups="stone_workshop.group_workshop_supervisor"/>

    <menuitem id="menu_workshop_output_lines"
              name="Salidas"
              parent="menu_workshop_config"
              action="action_workshop_output_line"
              sequence="30"
              groups="stone_workshop.group_workshop_supervisor"/>
</odoo>
```

## ./views/workshop_order_views.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_workshop_order_form" model="ir.ui.view">
        <field name="name">workshop.order.form</field>
        <field name="model">workshop.order</field>
        <field name="arch" type="xml">
            <form string="Orden de Taller de Piedra">
                <header>
                    <button name="action_confirm_workshop" string="Confirmar taller" type="object" class="btn-primary"
                            invisible="state != 'draft'"/>
                    <button name="action_declare_result" string="Declarar resultado" type="object" class="btn-success"
                            invisible="state != 'in_workshop'"/>
                    <button name="action_print_pick_report"
                            string="Imprimir recolección"
                            type="object"
                            class="btn-secondary"
                            invisible="input_count == 0 or state == 'cancel'"/>
                    <button name="action_cancel" string="Cancelar" type="object"
                            invisible="state in ('done', 'cancel')"/>
                    <button name="action_draft" string="A borrador" type="object"
                            invisible="state != 'cancel'"/>
                    <field name="state" widget="statusbar"
                           statusbar_visible="draft,in_workshop,done"/>
                </header>
                <sheet>
                    <div class="oe_button_box" name="button_box">
                        <button name="action_view_consume_pickings" type="object" class="oe_stat_button" icon="fa-arrow-right"
                                invisible="consume_picking_count == 0">
                            <field name="consume_picking_count" widget="statinfo" string="Consumos"/>
                        </button>
                        <button name="action_view_produce_pickings" type="object" class="oe_stat_button" icon="fa-arrow-left"
                                invisible="produce_picking_count == 0">
                            <field name="produce_picking_count" widget="statinfo" string="Producciones"/>
                        </button>
                        <button name="action_view_return_pickings" type="object" class="oe_stat_button" icon="fa-undo"
                                invisible="return_picking_count == 0">
                            <field name="return_picking_count" widget="statinfo" string="Devoluciones"/>
                        </button>
                        <button name="action_view_traces" type="object" class="oe_stat_button" icon="fa-random"
                                invisible="trace_count == 0">
                            <field name="trace_count" widget="statinfo" string="Trazas"/>
                        </button>
                    </div>

                    <div class="oe_title">
                        <label for="name"/>
                        <h1>
                            <field name="priority" widget="priority" nolabel="1"/>
                            <field name="name" readonly="1" class="d-inline-block ms-2"/>
                        </h1>
                    </div>

                    <group>
                        <group string="Trabajo">
                            <field name="process_id" readonly="state != 'draft'"/>
                            <field name="process_type" invisible="1"/>
                            <field name="operation_mode" invisible="1"/>
                            <field name="default_product_out_id" invisible="1"/>
                            <field name="remnant_product_id" readonly="state != 'draft'" invisible="operation_mode not in ('slab_cut', 'format_process')"/>
                            <field name="responsible_id"/>
                            <field name="date_planned"/>
                        </group>
                        <group string="Ubicaciones">
                            <field name="company_id" groups="base.group_multi_company"/>
                            <field name="warehouse_id" readonly="state != 'draft'"/>
                            <field name="location_src_id" readonly="state != 'draft'"/>
                            <field name="location_workshop_id" readonly="state != 'draft'"/>
                            <field name="location_dest_id" readonly="state != 'draft'"/>
                        </group>
                    </group>

                    <div class="alert alert-info" role="alert" invisible="operation_mode != 'slab_finish'">
                        Acabado de placas: selecciona varias placas en Entradas y usa <b>Sugerir salidas</b> para crear una salida individual por placa.
                    </div>
                    <div class="alert alert-warning" role="alert" invisible="operation_mode != 'slab_cut'">
                        Corte de placas (modo declarativo): captura en <b>Salidas</b> el área útil y los retazos realmente obtenidos. La merma se calculará automáticamente como el residual (entrada − útil − retazos) al cerrar o al pulsar <b>Cuadrar merma</b>.
                    </div>
                    <div class="alert alert-info" role="alert" invisible="operation_mode != 'format_process'">
                        Formatos / pallets (modo declarativo): captura los formatos terminados y los retazos aprovechables. La merma se calcula sola como el residual; no necesitas planearla por adelantado.
                    </div>

                    <notebook>
                        <page string="Entradas">
                            <div invisible="state != 'draft'">
                                <group class="sw-input-selector-config">
                                    <group string="Selección de material">
                                        <field name="input_product_id"
                                               readonly="state != 'draft'"
                                               options="{'no_create_edit': False}"/>
                                    </group>
                                </group>

                                <div class="sw-input-selector-full">
                                    <field name="input_selector_anchor"
                                           widget="workshop_lot_selector"
                                           nolabel="1"
                                           readonly="state != 'draft'"/>
                                </div>
                            </div>

                            <div invisible="state == 'draft'">
                                <div class="alert alert-info" role="alert" invisible="state != 'in_workshop'">
                                    Las placas marcadas como <b>Usada</b> se llenan automáticamente cuando las registras en alguna corrida de la <b>Bitácora</b>. Las que no aparezcan en ninguna corrida se devolverán íntegras al stock origen al declarar el resultado.
                                </div>
                                <field name="input_line_ids" readonly="1">
                                    <list edit="0" create="0" delete="0" decoration-muted="not is_used" decoration-success="state == 'done'">
                                        <field name="material_type"/>
                                        <field name="product_id"/>
                                        <field name="lot_id"/>
                                        <field name="qty_in"/>
                                        <field name="area_sqm"/>
                                        <field name="width_cm"/>
                                        <field name="height_cm"/>
                                        <field name="thickness_cm"/>
                                        <field name="block_name"/>
                                        <field name="tone"/>
                                        <field name="is_used"/>
                                        <field name="state" widget="badge"/>
                                    </list>
                                </field>
                            </div>

                            <field name="input_line_ids" invisible="1"
                                   context="{'default_product_id': input_product_id}">
                                <list>
                                    <field name="sequence"/>
                                    <field name="material_type"/>
                                    <field name="product_id"/>
                                    <field name="lot_id"/>
                                    <field name="qty_in"/>
                                    <field name="available_qty"/>
                                    <field name="area_sqm"/>
                                    <field name="width_cm"/>
                                    <field name="height_cm"/>
                                    <field name="thickness_cm"/>
                                    <field name="pieces"/>
                                    <field name="block_name"/>
                                    <field name="tone"/>
                                    <field name="current_finish"/>
                                    <field name="location_id"/>
                                    <field name="reserved_origin"/>
                                    <field name="is_used"/>
                                    <field name="state"/>
                                </list>
                            </field>
                        </page>

                        <page string="Bitácora" invisible="state == 'draft'">
                            <div class="alert alert-info" role="alert" invisible="state != 'in_workshop'">
                                Por cada corrida que entres al taller agrega un renglón: la fecha, los lotes que procesaste y los m² producidos. Cada lote sólo puede asignarse a una corrida; los que no aparezcan en ninguna se devolverán al stock al declarar el resultado.
                            </div>
                            <field name="progress_log_ids" readonly="state in ('done', 'cancel')">
                                <list editable="bottom">
                                    <field name="date"/>
                                    <field name="available_input_line_ids" column_invisible="1"/>
                                    <field name="input_line_ids" widget="many2many_tags"
                                           options="{'no_create': True, 'no_open': True}"/>
                                    <field name="area_sqm" string="m² producidos"/>
                                    <field name="notes" optional="hide"/>
                                </list>
                                <form string="Corrida de taller">
                                    <sheet>
                                        <group>
                                            <group>
                                                <field name="date"/>
                                                <field name="area_sqm" string="m² producidos"/>
                                            </group>
                                            <group>
                                                <field name="available_input_line_ids" invisible="1"/>
                                                <field name="input_line_ids" widget="many2many_tags"
                                                       options="{'no_create': True, 'no_open': True}"/>
                                            </group>
                                        </group>
                                        <group string="Notas">
                                            <field name="notes" nolabel="1"/>
                                        </group>
                                    </sheet>
                                </form>
                            </field>
                        </page>

                        <page string="Salidas">
                            <field name="output_line_ids" readonly="state in ('done', 'cancel')">
                                <list editable="bottom" decoration-success="state in ('received', 'scrapped')" decoration-warning="state == 'ready_to_produce'">
                                    <field name="sequence" widget="handle"/>
                                    <field name="input_line_id" column_invisible="1"/>
                                    <field name="output_type"/>
                                    <field name="product_id" invisible="output_type in ('scrap', 'rejected')"/>
                                    <field name="lot_name" invisible="output_type in ('scrap', 'rejected')"/>
                                    <field name="lot_id" readonly="1" invisible="output_type in ('scrap', 'rejected')"/>
                                    <field name="qty_out"/>
                                    <field name="area_sqm"/>
                                    <field name="width_cm"/>
                                    <field name="height_cm"/>
                                    <field name="thickness_cm"/>
                                    <field name="pieces"/>
                                    <field name="finish_result"/>
                                    <field name="location_dest_id" invisible="output_type in ('scrap', 'rejected')"/>
                                    <field name="state" readonly="1" widget="badge"
                                           decoration-info="state in ('draft', 'ready_to_produce')"
                                           decoration-success="state in ('produced', 'received', 'scrapped')"
                                           decoration-muted="state == 'cancelled'"/>
                                </list>
                                <form string="Salida de taller">
                                    <sheet>
                                        <group>
                                            <group>
                                                <field name="input_line_id" invisible="1"/>
                                                <field name="output_type"/>
                                                <field name="product_id" invisible="output_type in ('scrap', 'rejected')"/>
                                                <field name="lot_name" invisible="output_type in ('scrap', 'rejected')"/>
                                                <field name="lot_id" readonly="1" invisible="output_type in ('scrap', 'rejected')"/>
                                            </group>
                                            <group>
                                                <field name="qty_out"/>
                                                <field name="area_sqm"/>
                                                <field name="width_cm"/>
                                                <field name="height_cm"/>
                                                <field name="thickness_cm"/>
                                                <field name="pieces"/>
                                                <field name="finish_result"/>
                                                <field name="location_dest_id" invisible="output_type in ('scrap', 'rejected')"/>
                                                <field name="state" readonly="1"/>
                                            </group>
                                        </group>
                                    </sheet>
                                </form>
                            </field>
                        </page>

                        <page string="Resumen y costos">
                            <group string="Totales operativos">
                                <group>
                                    <field name="input_count"/>
                                    <field name="output_count"/>
                                    <field name="qty_in_total"/>
                                    <field name="qty_out_total"/>
                                </group>
                                <group>
                                    <field name="area_in_total"/>
                                    <field name="area_out_total"/>
                                    <field name="area_remnant_total"/>
                                    <field name="area_loss_total"/>
                                    <field name="total_accounted_area_sqm"/>
                                    <field name="area_balance_delta"/>
                                </group>
                            </group>
                            <group string="KPIs MRP / rendimiento">
                                <group>
                                    <field name="production_target_sqm"/>
                                    <field name="planned_input_required_sqm"/>
                                    <field name="target_coverage_percent"/>
                                </group>
                                <group>
                                    <field name="yield_percent"/>
                                    <field name="remnant_percent"/>
                                    <field name="loss_percent"/>
                                </group>
                            </group>
                            <group string="Costos">
                                <group>
                                    <field name="material_cost"/>
                                    <field name="process_cost"/>
                                    <field name="labor_cost"/>
                                    <field name="machine_cost"/>
                                </group>
                                <group>
                                    <field name="overhead_cost"/>
                                    <field name="loss_cost"/>
                                    <field name="total_cost"/>
                                    <field name="cost_per_sqm"/>
                                </group>
                            </group>
                        </page>

                        <page string="Trazabilidad" invisible="trace_count == 0">
                            <field name="trace_ids" readonly="1">
                                <list>
                                    <field name="date_done"/>
                                    <field name="source_lot_id"/>
                                    <field name="result_lot_id"/>
                                    <field name="process_id"/>
                                    <field name="output_type"/>
                                    <field name="qty_in"/>
                                    <field name="qty_out"/>
                                    <field name="area_in_sqm"/>
                                    <field name="area_out_sqm"/>
                                    <field name="loss_sqm"/>
                                    <field name="responsible_id"/>
                                </list>
                            </field>
                        </page>

                        <page string="Notas">
                            <field name="notes" placeholder="Observaciones de taller, calidad, incidencias o instrucciones especiales..."/>
                        </page>
                    </notebook>
                </sheet>
                <chatter/>
            </form>
        </field>
    </record>

    <record id="view_workshop_order_list" model="ir.ui.view">
        <field name="name">workshop.order.list</field>
        <field name="model">workshop.order</field>
        <field name="arch" type="xml">
            <list string="Órdenes de Taller" decoration-info="state == 'draft'" decoration-warning="state == 'in_workshop'" decoration-success="state == 'done'" decoration-muted="state == 'cancel'">
                <field name="priority" widget="priority" optional="show"/>
                <field name="name"/>
                <field name="operation_mode"/>
                <field name="process_id"/>
                <field name="input_count"/>
                <field name="output_count"/>
                <field name="production_target_sqm"/>
                <field name="area_in_total"/>
                <field name="area_out_total"/>
                <field name="area_remnant_total"/>
                <field name="area_loss_total"/>
                <field name="yield_percent"/>
                <field name="area_balance_delta"/>
                <field name="total_cost" sum="Total"/>
                <field name="responsible_id"/>
                <field name="date_planned"/>
                <field name="state" widget="badge"
                       decoration-info="state == 'draft'"
                       decoration-warning="state == 'in_workshop'"
                       decoration-success="state == 'done'"
                       decoration-muted="state == 'cancel'"/>
            </list>
        </field>
    </record>

    <record id="view_workshop_order_kanban" model="ir.ui.view">
        <field name="name">workshop.order.kanban</field>
        <field name="model">workshop.order</field>
        <field name="arch" type="xml">
            <kanban default_group_by="state" class="o_workshop_kanban">
                <field name="name"/>
                <field name="state"/>
                <field name="priority"/>
                <field name="operation_mode"/>
                <field name="process_id"/>
                <field name="input_count"/>
                <field name="output_count"/>
                <field name="production_target_sqm"/>
                <field name="area_in_total"/>
                <field name="area_out_total"/>
                <field name="area_remnant_total"/>
                <field name="area_loss_total"/>
                <field name="yield_percent"/>
                <field name="responsible_id"/>
                <templates>
                    <t t-name="card">
                        <div class="oe_kanban_global_click">
                            <div class="o_kanban_record_title d-flex align-items-center">
                                <field name="priority" widget="priority" class="me-2"/>
                                <strong><field name="name"/></strong>
                            </div>
                            <div class="text-primary"><field name="process_id"/></div>
                            <div class="mt-2">
                                <span class="badge text-bg-light"><field name="operation_mode"/></span>
                            </div>
                            <div class="mt-2 text-muted">
                                Entradas: <field name="input_count"/> · Salidas: <field name="output_count"/>
                            </div>
                            <div class="mt-1 text-muted">
                                Área: <field name="area_in_total"/> m² → <field name="area_out_total"/> m²
                            </div>
                            <div class="mt-1 text-muted" t-if="record.production_target_sqm.raw_value">
                                Objetivo: <field name="production_target_sqm"/> m² · Rend.: <field name="yield_percent"/>%
                            </div>
                            <div class="mt-1 text-danger" t-if="record.area_loss_total.raw_value">
                                Merma: <field name="area_loss_total"/> m²
                            </div>
                            <footer class="pt-2">
                                <field name="responsible_id" widget="many2one_avatar_user"/>
                            </footer>
                        </div>
                    </t>
                </templates>
            </kanban>
        </field>
    </record>

    <record id="view_workshop_order_search" model="ir.ui.view">
        <field name="name">workshop.order.search</field>
        <field name="model">workshop.order</field>
        <field name="arch" type="xml">
            <search string="Buscar órdenes de taller">
                <field name="name"/>
                <field name="process_id"/>
                <field name="responsible_id"/>
                <filter name="draft" string="Borrador" domain="[('state', '=', 'draft')]"/>
                <filter name="active" string="En taller" domain="[('state', '=', 'in_workshop')]"/>
                <filter name="done" string="Terminadas" domain="[('state', '=', 'done')]"/>
                    <filter name="group_state" string="Estado" context="{'group_by': 'state'}"/>
                    <filter name="group_operation" string="Modo operativo" context="{'group_by': 'operation_mode'}"/>
                    <filter name="group_process" string="Proceso" context="{'group_by': 'process_id'}"/>
                    <filter name="group_responsible" string="Responsable" context="{'group_by': 'responsible_id'}"/>
                <searchpanel>
                    <field name="state" icon="fa-tasks" enable_counters="1"/>
                    <field name="operation_mode" icon="fa-cogs" enable_counters="1"/>
                    <field name="process_id" icon="fa-industry" enable_counters="1"/>
                </searchpanel>
            </search>
        </field>
    </record>

    <record id="view_workshop_input_line_list" model="ir.ui.view">
        <field name="name">workshop.input.line.list</field>
        <field name="model">workshop.input.line</field>
        <field name="arch" type="xml">
            <list string="Entradas de Taller">
                <field name="order_id"/>
                <field name="material_type"/>
                <field name="product_id"/>
                <field name="lot_id"/>
                <field name="qty_in"/>
                <field name="available_qty"/>
                <field name="area_sqm"/>
                <field name="block_name"/>
                <field name="tone"/>
                <field name="state" widget="badge"/>
            </list>
        </field>
    </record>

    <record id="view_workshop_output_line_list" model="ir.ui.view">
        <field name="name">workshop.output.line.list</field>
        <field name="model">workshop.output.line</field>
        <field name="arch" type="xml">
            <list string="Salidas de Taller">
                <field name="order_id"/>
                <field name="input_line_id"/>
                <field name="output_type"/>
                <field name="product_id"/>
                <field name="lot_name"/>
                <field name="lot_id"/>
                <field name="qty_out"/>
                <field name="area_sqm"/>
                <field name="state" widget="badge"/>
            </list>
        </field>
    </record>

    <record id="view_workshop_trace_list" model="ir.ui.view">
        <field name="name">workshop.transformation.trace.list</field>
        <field name="model">workshop.transformation.trace</field>
        <field name="arch" type="xml">
            <list string="Trazabilidad de Transformación" create="0" edit="0">
                <field name="date_done"/>
                <field name="order_id"/>
                <field name="source_lot_id"/>
                <field name="result_lot_id"/>
                <field name="process_id"/>
                <field name="output_type"/>
                <field name="qty_in"/>
                <field name="qty_out"/>
                <field name="area_in_sqm"/>
                <field name="area_out_sqm"/>
                <field name="loss_sqm"/>
                <field name="responsible_id"/>
            </list>
        </field>
    </record>

    <record id="view_workshop_trace_form" model="ir.ui.view">
        <field name="name">workshop.transformation.trace.form</field>
        <field name="model">workshop.transformation.trace</field>
        <field name="arch" type="xml">
            <form string="Traza de Transformación" create="0" edit="0">
                <sheet>
                    <group>
                        <group string="Origen">
                            <field name="order_id"/>
                            <field name="input_line_id"/>
                            <field name="source_product_id"/>
                            <field name="source_lot_id"/>
                            <field name="qty_in"/>
                            <field name="area_in_sqm"/>
                        </group>
                        <group string="Resultado">
                            <field name="output_line_id"/>
                            <field name="output_type"/>
                            <field name="result_product_id"/>
                            <field name="result_lot_id"/>
                            <field name="qty_out"/>
                            <field name="area_out_sqm"/>
                            <field name="loss_sqm"/>
                        </group>
                    </group>
                    <group string="Proceso">
                        <field name="process_id"/>
                        <field name="date_done"/>
                        <field name="responsible_id"/>
                    </group>
                </sheet>
            </form>
        </field>
    </record>

    <record id="action_workshop_order" model="ir.actions.act_window">
        <field name="name">Órdenes de Taller</field>
        <field name="res_model">workshop.order</field>
        <field name="view_mode">kanban,list,form</field>
        <field name="search_view_id" ref="view_workshop_order_search"/>
    </record>

    <record id="action_workshop_input_line" model="ir.actions.act_window">
        <field name="name">Entradas de Taller</field>
        <field name="res_model">workshop.input.line</field>
        <field name="view_mode">list,form</field>
    </record>

    <record id="action_workshop_output_line" model="ir.actions.act_window">
        <field name="name">Salidas de Taller</field>
        <field name="res_model">workshop.output.line</field>
        <field name="view_mode">list,form</field>
    </record>

    <record id="action_workshop_trace" model="ir.actions.act_window">
        <field name="name">Trazabilidad</field>
        <field name="res_model">workshop.transformation.trace</field>
        <field name="view_mode">list,form</field>
    </record>

    <record id="action_workshop_dashboard" model="ir.actions.client">
        <field name="name">Panel de Taller</field>
        <field name="tag">stone_workshop_dashboard</field>
    </record>
</odoo>
```

## ./views/workshop_process_views.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
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
                        <group string="Identificación">
                            <field name="code"/>
                            <field name="process_type"/>
                            <field name="default_operation_mode"/>
                            <field name="sequence"/>
                            <field name="active"/>
                        </group>
                        <group string="Costeo base">
                            <field name="cost_per_sqm"/>
                            <field name="labor_cost"/>
                            <field name="machine_cost"/>
                            <field name="overhead_cost"/>
                        </group>
                        <group string="Planeación MRP">
                            <field name="expected_yield_percent"/>
                            <field name="default_loss_percent"/>
                        </group>
                    </group>
                    <group string="Descripción">
                        <field name="description" placeholder="Describe cómo se ejecuta este proceso en taller..."/>
                    </group>
                </sheet>
            </form>
        </field>
    </record>

    <record id="view_workshop_process_list" model="ir.ui.view">
        <field name="name">workshop.process.list</field>
        <field name="model">workshop.process</field>
        <field name="arch" type="xml">
            <list string="Procesos" editable="bottom">
                <field name="sequence" widget="handle"/>
                <field name="name"/>
                <field name="code"/>
                <field name="process_type"/>
                <field name="default_operation_mode"/>
                <field name="cost_per_sqm"/>
                <field name="labor_cost"/>
                <field name="machine_cost"/>
                <field name="overhead_cost"/>
                <field name="expected_yield_percent"/>
                <field name="default_loss_percent"/>
                <field name="active"/>
            </list>
        </field>
    </record>

    <record id="action_workshop_process" model="ir.actions.act_window">
        <field name="name">Procesos</field>
        <field name="res_model">workshop.process</field>
        <field name="view_mode">list,form</field>
    </record>
</odoo>
```

