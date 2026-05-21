from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare, float_is_zero
import logging

_logger = logging.getLogger(__name__)

ACTIVE_WORKSHOP_STATES = (
    'validated',
    'confirmed',
    'sent_to_workshop',
    'in_progress',
    'partial_done',
)


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
        string='Producto salida de la orden',
        domain=[('tracking', '!=', 'none')],
        help='Producto único que se usará para generar las salidas automáticas de la orden. '
             'Si se deja vacío, se reutiliza el producto de entrada.',
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

            width_float = float(width or 0.0) if isinstance(width, (int, float)) else 0.0
            height_float = float(height or 0.0) if isinstance(height, (int, float)) else 0.0
            thickness_float = float(thickness or 0.0) if isinstance(thickness, (int, float)) else 0.0
            area_float = float(area or 0.0) if isinstance(area, (int, float)) else 0.0
            if not area_float and width_float and height_float:
                area_float = (width_float / 100.0) * (height_float / 100.0)

            qty_in = available_qty or area_float or 1.0

            line_vals.append({
                'material_type': order_stub._map_lot_material_type(lot),
                'product_id': line_product.id,
                'lot_id': lot.id,
                'qty_in': qty_in,
                'area_sqm': area_float or qty_in,
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
        'output_line_ids.qty_out',
        'output_line_ids.area_sqm',
        'output_line_ids.output_type',
        'output_line_ids.state',
    )
    def _compute_totals(self):
        for rec in self:
            active_outputs = rec.output_line_ids.filtered(lambda l: l.state != 'cancelled')
            useful_outputs = active_outputs.filtered(lambda l: l.output_type in ('finished_slab', 'format_piece'))
            remnant_outputs = active_outputs.filtered(lambda l: l.output_type == 'remnant')
            scrap_outputs = active_outputs.filtered(lambda l: l.output_type in ('scrap', 'rejected'))

            rec.qty_in_total = sum(rec.input_line_ids.mapped('qty_in'))
            rec.qty_out_total = sum(useful_outputs.mapped('qty_out'))
            rec.area_in_total = sum(rec.input_line_ids.mapped('area_sqm'))
            rec.area_out_total = sum(useful_outputs.mapped('area_sqm'))
            rec.area_remnant_total = sum(remnant_outputs.mapped('area_sqm'))
            rec.area_loss_total = sum(scrap_outputs.mapped('area_sqm'))

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

    def _make_unique_lot_name(self, base_name, product=False, exclude_output=False):
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

    def action_generate_outputs(self):
        for rec in self:
            if not rec.input_line_ids:
                raise UserError(_('Agrega al menos una línea de entrada antes de generar salidas.'))
            created = 0
            for input_line in rec.input_line_ids.filtered(lambda l: l.state != 'cancelled'):
                existing = rec.output_line_ids.filtered(lambda o: o.input_line_id == input_line and o.state != 'cancelled')
                if existing:
                    continue

                if rec.operation_mode == 'slab_cut':
                    # En corte, las salidas deben ser capturadas por el usuario porque pueden ser múltiples.
                    continue

                product_out = rec.default_product_out_id or input_line.product_id
                if rec.operation_mode in ('slab_finish', 'rework'):
                    output_type = 'finished_slab'
                else:
                    output_type = 'format_piece'

                lot_name = rec._make_unique_lot_name(
                    '%s-%s' % (input_line.lot_id.name if input_line.lot_id else input_line.product_id.display_name, rec.process_id.code or 'PROC'),
                    product=product_out,
                )
                self.env['workshop.output.line'].create({
                    'order_id': rec.id,
                    'input_line_id': input_line.id,
                    'output_type': output_type,
                    'product_id': product_out.id,
                    'lot_name': lot_name,
                    'qty_out': input_line.qty_in,
                    'area_sqm': input_line.area_sqm,
                    'width_cm': input_line.width_cm,
                    'height_cm': input_line.height_cm,
                    'thickness_cm': input_line.thickness_cm,
                    'pieces': input_line.pieces or 1,
                    'finish_result': rec.process_id.name,
                    'location_dest_id': rec.location_dest_id.id,
                })
                created += 1
            if created:
                rec.message_post(body=_('Se generaron %s salida(s) esperada(s) automáticamente.') % created)
            elif rec.operation_mode == 'slab_cut':
                rec.message_post(body=_('Modo corte: captura manualmente formatos, retazos y merma en la pestaña Salidas.'))
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

    def _validate_output_lines(self):
        precision = self.env['decimal.precision'].precision_get('Product Unit of Measure') or 4
        for rec in self:
            active_inputs = rec.input_line_ids.filtered(lambda l: l.state != 'cancelled')
            active_outputs = rec.output_line_ids.filtered(lambda l: l.state != 'cancelled')
            if not active_outputs:
                raise ValidationError(_('La orden %s debe tener al menos una salida esperada.') % rec.name)

            for input_line in active_inputs:
                outputs = active_outputs.filtered(lambda o: o.input_line_id == input_line)
                if not outputs:
                    raise ValidationError(_('La entrada %s no tiene ninguna salida esperada.') % input_line.display_name)

                if rec.operation_mode == 'slab_cut':
                    if not input_line.area_sqm:
                        raise ValidationError(_('Para corte, la entrada %s debe tener área m².') % input_line.display_name)
                    output_area = sum(outputs.mapped('area_sqm'))
                    tolerance = input_line.area_sqm * ((rec.area_tolerance_percent or 0.0) / 100.0)
                    if abs(output_area - input_line.area_sqm) > tolerance:
                        raise ValidationError(_(
                            'El área de salida de %(line)s no cuadra contra la entrada. Entrada: %(area_in).4f m², '
                            'salidas/retazos/merma: %(area_out).4f m², tolerancia: %(tolerance).4f m².'
                        ) % {
                            'line': input_line.display_name,
                            'area_in': input_line.area_sqm,
                            'area_out': output_area,
                            'tolerance': tolerance,
                        })

            for output in active_outputs:
                if output.output_type not in ('scrap', 'rejected'):
                    if not output.product_id:
                        raise ValidationError(_('Las salidas productivas deben tener producto.'))
                    if float_compare(output.qty_out, 0.0, precision_digits=precision) <= 0:
                        raise ValidationError(_('La salida %s debe tener cantidad mayor a cero.') % output.display_name)
                    if not output.lot_name and not output.lot_id:
                        output.lot_name = rec._make_unique_lot_name(
                            rec._default_output_lot_name(output.input_line_id),
                            product=output.product_id,
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
            rec._validate_input_lines()
            rec._validate_output_lines()

    def action_validate_order(self):
        for rec in self:
            if rec.operation_mode != 'slab_cut':
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
            rec._validate_business_rules()
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
            for input_line in rec.input_line_ids.filtered(lambda l: l.state not in ('cancelled',)):
                outputs = rec.output_line_ids.filtered(lambda o: o.input_line_id == input_line and o.state != 'cancelled')
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
        input_line = output_line.input_line_id
        vals = {
            'order_id': self.id,
            'input_line_id': input_line.id,
            'output_line_id': output_line.id,
            'source_product_id': input_line.product_id.id,
            'source_lot_id': input_line.lot_id.id,
            'result_product_id': output_line.product_id.id if output_line.product_id else False,
            'result_lot_id': output_line.lot_id.id if output_line.lot_id else False,
            'process_id': self.process_id.id,
            'qty_in': input_line.qty_in,
            'qty_out': output_line.qty_out,
            'area_in_sqm': input_line.area_sqm,
            'area_out_sqm': output_line.area_sqm if output_line.output_type not in ('scrap', 'rejected') else 0.0,
            'loss_sqm': output_line.area_sqm if output_line.output_type in ('scrap', 'rejected') else 0.0,
            'output_type': output_line.output_type,
            'date_done': fields.Datetime.now(),
            'responsible_id': self.responsible_id.id,
        }
        trace = self.env['workshop.transformation.trace'].search([
            ('output_line_id', '=', output_line.id),
        ], limit=1)
        if trace:
            trace.write(vals)
        else:
            self.env['workshop.transformation.trace'].create(vals)

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

        if qty_float and not area_float:
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
            if not line.area_sqm and line.qty_in:
                line.area_sqm = line.qty_in

    @api.onchange('width_cm', 'height_cm', 'pieces')
    def _onchange_dimensions(self):
        for line in self:
            if line.width_cm and line.height_cm and line.pieces:
                line.area_sqm = (line.width_cm / 100.0) * (line.height_cm / 100.0) * (line.pieces or 1)

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
    input_line_id = fields.Many2one('workshop.input.line', string='Entrada origen', required=True, ondelete='cascade')
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
            line.qty_out = line.qty_out or line.input_line_id.qty_in
            line.area_sqm = line.area_sqm or line.input_line_id.area_sqm
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
                line.area_sqm = (line.width_cm / 100.0) * (line.height_cm / 100.0) * (line.pieces or 1)

    def _ensure_result_lot(self):
        self.ensure_one()
        if self.output_type in ('scrap', 'rejected'):
            return False
        if self.lot_id:
            return self.lot_id
        if not self.product_id:
            raise UserError(_('La salida %s no tiene producto definido.') % self.display_name)
        if not self.lot_name:
            self.lot_name = self.order_id._make_unique_lot_name(
                self.order_id._default_output_lot_name(self.input_line_id),
                product=self.product_id,
                exclude_output=self,
            )
        existing = self.env['stock.lot'].search([
            ('name', '=', self.lot_name),
            ('product_id', '=', self.product_id.id),
            '|', ('company_id', '=', self.company_id.id), ('company_id', '=', False),
        ], limit=1)
        if existing:
            self.lot_id = existing.id
            return existing
        lot = self.env['stock.lot'].create({
            'name': self.lot_name,
            'product_id': self.product_id.id,
            'company_id': self.company_id.id,
        })
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