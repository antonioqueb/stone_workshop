from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare, float_is_zero
from html import escape
import logging

_logger = logging.getLogger(__name__)

ACTIVE_WORKSHOP_STATES = (
    'validated',
    'confirmed',
    'sent_to_workshop',
    'in_progress',
    'partial_done',
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
        ('validated', 'Validada'),
        ('confirmed', 'Confirmada'),
        ('sent_to_workshop', 'Enviada a taller'),
        ('in_progress', 'En proceso'),
        ('partial_done', 'Parcialmente terminada'),
        ('done', 'Terminada'),
        ('cancel', 'Cancelada'),
    ], string='Estado', default='draft', tracking=True)

    operation_mode = fields.Selection([
        ('slab_finish', 'Acabado de placas'),
        ('slab_cut', 'Corte de placas'),
        ('format_process', 'Formatos / pallets'),
        ('rework', 'Reproceso / reparación'),
    ], string='Modo operativo', required=True, default='slab_finish', tracking=True)

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
    auto_generate_outputs = fields.Boolean(
        string='Pre-llenar salidas al validar',
        default=False,
        help='Modo declarativo (default): capturas tú las salidas reales al cerrar la orden y la merma se calcula como el residual. '
             'Si lo activas, al validar se pre-llenan salidas sugeridas (útil + retazo + merma planeada) que luego puedes editar.',
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

    input_count = fields.Integer(string='Entradas', compute='_compute_counts')
    output_count = fields.Integer(string='Salidas', compute='_compute_counts')
    trace_count = fields.Integer(string='Trazas', compute='_compute_counts')
    consume_picking_count = fields.Integer(string='Consumos', compute='_compute_counts')
    produce_picking_count = fields.Integer(string='Producciones', compute='_compute_counts')

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

    @api.depends('input_line_ids', 'output_line_ids', 'trace_ids')
    def _compute_counts(self):
        for rec in self:
            rec.input_count = len(rec.input_line_ids)
            rec.output_count = len(rec.output_line_ids)
            rec.trace_count = len(rec.trace_ids)
            rec.consume_picking_count = len(rec.consume_picking_ids)
            rec.produce_picking_count = len(rec.produce_picking_ids)

    @api.depends(
        'input_line_ids.qty_in',
        'input_line_ids.area_sqm',
        'input_line_ids.state',
        'output_line_ids.qty_out',
        'output_line_ids.area_sqm',
        'output_line_ids.output_type',
        'output_line_ids.state',
        'production_target_sqm',
        'expected_yield_percent',
    )
    def _compute_totals(self):
        for rec in self:
            active_inputs = rec.input_line_ids.filtered(lambda l: l.state != 'cancelled')
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

    @api.onchange('process_id')
    def _onchange_process_id(self):
        for rec in self:
            if rec.process_id:
                rec.operation_mode = rec.process_id.default_operation_mode or rec.operation_mode
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
        active_inputs = self._get_active_input_lines()
        active_outputs = self._get_active_output_lines()

        input_area = sum(self._input_line_area(line) for line in active_inputs)
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

    def action_generate_outputs(self):
        for rec in self:
            if not rec.input_line_ids.filtered(lambda l: l.state != 'cancelled'):
                raise UserError(_('Agrega al menos una línea de entrada antes de generar salidas.'))
            rec._ensure_default_locations()

            if rec.operation_mode in ('slab_cut', 'format_process'):
                created = rec._generate_cut_or_format_outputs()
                rec.message_post(body=_(
                    'Se pre-llenaron %(count)s salida(s) sugeridas. Edita las cantidades reales obtenidas; '
                    'la merma se calculará automáticamente como el residual al cerrar la orden.'
                ) % {'count': created})
            else:
                created = rec._generate_finish_like_outputs()
                if created:
                    rec.message_post(body=_('Se generaron %s salida(s) esperada(s) automáticamente.') % created)
        return True


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

    def _validate_output_lines(self, require_outputs=False):
        """Valida salidas con criterio declarativo.

        En modo declarativo (corte/formato), la merma se calcula como el residual
        entre entrada y útil+retazos, así que ya NO se exige que el balance cuadre
        ni que la salida útil coincida con production_target_sqm. La merma residual
        se materializa después con _ensure_residual_scrap_line().
        """
        precision = self.env['decimal.precision'].precision_get('Product Unit of Measure') or 4
        for rec in self:
            active_inputs = rec._get_active_input_lines()
            active_outputs = rec._get_active_output_lines()

            if require_outputs and not active_outputs:
                raise ValidationError(_('La orden %s debe tener al menos una salida registrada.') % rec.name)

            if not active_outputs:
                continue

            if rec.operation_mode in ('slab_finish', 'rework'):
                for input_line in active_inputs:
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

    def _validate_business_rules(self, require_outputs=False):
        for rec in self:
            rec._ensure_default_locations()
            if not rec.process_id:
                raise ValidationError(_('Selecciona un proceso.'))
            if not rec.location_src_id or not rec.location_workshop_id or not rec.location_dest_id:
                raise ValidationError(_('Define ubicación origen, ubicación taller y ubicación destino.'))
            rec._normalize_input_area_values()
            rec._validate_input_lines()
            rec._validate_output_lines(require_outputs=require_outputs)

    def action_validate_order(self):
        for rec in self:
            active_outputs = rec.output_line_ids.filtered(lambda l: l.state != 'cancelled')
            if rec.auto_generate_outputs and not active_outputs:
                rec.action_generate_outputs()
            elif rec.operation_mode in ('slab_finish', 'rework') and not active_outputs:
                rec.action_generate_outputs()
            rec._validate_business_rules()
            rec.write({'state': 'validated'})
            rec.message_post(body=_('Orden validada correctamente.'))
        return True

    def action_confirm(self):
        for rec in self:
            if rec.state == 'draft':
                rec.action_validate_order()
            rec._validate_business_rules()
            rec.write({'state': 'confirmed'})
            rec.message_post(body=_('Orden confirmada.'))
        return True

    def action_send_to_workshop(self):
        for rec in self:
            if rec.state in ('draft', 'validated'):
                rec.action_confirm()
            if rec.state not in ('confirmed', 'sent_to_workshop'):
                raise UserError(_('Solo puedes enviar a taller órdenes confirmadas.'))
            rec._validate_business_rules()
            pending_inputs = rec.input_line_ids.filtered(lambda l: l.state not in ('cancelled',) and not l.is_consumed)
            if pending_inputs:
                picking = rec._create_consume_picking(pending_inputs)
                rec.consume_picking_ids = [(4, picking.id)]
                pending_inputs.write({
                    'state': 'sent_to_workshop',
                    'is_consumed': True,
                    'consume_picking_id': picking.id,
                })
            rec.write({'state': 'sent_to_workshop'})
            rec.message_post(body=_('Material enviado a taller.'))
        return True

    def action_start(self):
        for rec in self:
            if rec.state in ('draft', 'validated', 'confirmed'):
                rec.action_send_to_workshop()
            rec.input_line_ids.filtered(lambda l: l.state == 'sent_to_workshop').write({'state': 'in_progress'})
            rec.write({
                'state': 'in_progress',
                'date_start': rec.date_start or fields.Datetime.now(),
            })
            rec.message_post(body=_('Orden iniciada.'))
        return True

    def action_receive_outputs(self):
        for rec in self:
            if rec.state in ('draft', 'validated', 'confirmed'):
                rec.action_start()
            # Cierre declarativo: antes de validar y materializar el picking de
            # producción, calculamos la merma como el residual entrada − útil −
            # retazos − merma manual. Así el usuario solo necesita capturar lo
            # útil y los retazos; el sistema cuadra el balance.
            rec._ensure_residual_scrap_line()
            rec._validate_business_rules(require_outputs=True)
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
            rec._refresh_order_state_after_production()
        return True

    def action_done(self):
        for rec in self:
            rec.action_receive_outputs()
            if rec.state != 'done':
                rec._refresh_order_state_after_production(force_done=True)
        return True

    def action_balance_residual_loss(self):
        """Acción de UI para que el usuario cuadre la merma sin cerrar la orden.

        Calcula entrada − útil − retazos − merma manual y materializa la diferencia
        como línea scrap automática. Útil para previsualizar el balance antes de
        recibir salidas.
        """
        for rec in self:
            if rec.operation_mode in ('slab_finish', 'rework'):
                raise UserError(_(
                    'El cuadre de merma residual solo aplica a órdenes de corte o formato. '
                    'En acabado/reproceso, cada entrada genera su salida 1:1.'
                ))
            if not rec._get_active_input_lines():
                raise UserError(_('Agrega al menos una entrada antes de cuadrar la merma.'))
            delta = rec._ensure_residual_scrap_line()
            if delta > 0:
                rec.message_post(body=_(
                    'Merma residual cuadrada: %(delta).4f m² registrados como salida scrap automática.'
                ) % {'delta': delta})
            elif delta < 0:
                rec.message_post(body=_(
                    'Las salidas registradas (%(over).4f m²) superan el área de entrada. '
                    'Revisa las cantidades; no se generó línea de merma.'
                ) % {'over': -delta})
            else:
                rec.message_post(body=_('Balance cuadrado sin merma residual.'))
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
                if active_outputs and all(o.state in ('received', 'scrapped') for o in active_outputs):
                    if all(o.output_type in ('scrap', 'rejected') for o in active_outputs):
                        rec.input_line_ids.filtered(lambda l: l.state not in ('cancelled',)).write({'state': 'rejected'})
                    else:
                        rec.input_line_ids.filtered(lambda l: l.state not in ('cancelled',)).write({'state': 'done'})
                    continue
                elif active_outputs and any(o.state in ('received', 'scrapped') for o in active_outputs):
                    rec.input_line_ids.filtered(lambda l: l.state not in ('cancelled',)).write({'state': 'partial_done'})
                    continue

            for input_line in rec.input_line_ids.filtered(lambda l: l.state not in ('cancelled',)):
                outputs = active_outputs.filtered(lambda o: o.input_line_id == input_line)
                if outputs and all(o.state in ('received', 'scrapped') for o in outputs):
                    if all(o.output_type in ('scrap', 'rejected') for o in outputs):
                        input_line.state = 'rejected'
                    else:
                        input_line.state = 'done'
                elif outputs and any(o.state in ('received', 'scrapped') for o in outputs):
                    input_line.state = 'partial_done'

    def _refresh_order_state_after_production(self, force_done=False):
        for rec in self:
            active_outputs = rec.output_line_ids.filtered(lambda o: o.state != 'cancelled')
            if not active_outputs:
                continue
            all_done = all(o.state in ('received', 'scrapped') for o in active_outputs)
            any_done = any(o.state in ('received', 'scrapped') for o in active_outputs)
            if all_done or force_done:
                rec.write({'state': 'done', 'date_done': fields.Datetime.now()})
                rec.message_post(body=_('Orden terminada.'))
            elif any_done:
                rec.write({'state': 'partial_done'})
                rec.message_post(body=_('Orden parcialmente terminada.'))

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

    def action_view_consume_pickings(self):
        self.ensure_one()
        return self._action_view_records('stock.picking', self.consume_picking_ids, _('Pickings de consumo'))

    def action_view_produce_pickings(self):
        self.ensure_one()
        return self._action_view_records('stock.picking', self.produce_picking_ids, _('Pickings de producción'))

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
        ('reserved_for_workshop', 'Reservada taller'),
        ('sent_to_workshop', 'Enviada a taller'),
        ('in_progress', 'En proceso'),
        ('partial_done', 'Parcial'),
        ('done', 'Terminada'),
        ('rejected', 'Rechazada'),
        ('damaged', 'Dañada'),
        ('cancelled', 'Cancelada'),
    ], string='Estado', default='pending')
    is_consumed = fields.Boolean(string='Consumida en taller', copy=False)
    consume_picking_id = fields.Many2one('stock.picking', string='Picking consumo', readonly=True, copy=False)
    name = fields.Char(string='Descripción', compute='_compute_name', store=True)

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