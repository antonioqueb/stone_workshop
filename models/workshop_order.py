from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)


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
    picking_consume_id = fields.Many2one('stock.picking', string='Picking Consumo', readonly=True, copy=False)
    picking_produce_id = fields.Many2one('stock.picking', string='Picking Producción', readonly=True, copy=False)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    user_id = fields.Many2one('res.users', string='Responsable', default=lambda self: self.env.user)
    date_planned = fields.Datetime(string='Fecha Planeada')
    date_done = fields.Datetime(string='Fecha Terminado', readonly=True)

    def _parse_dimension(self, val):
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
            total_qty = sum(quants.mapped('quantity'))
            self.qty_in = total_qty
            self.qty_out = total_qty
        else:
            self.qty_in = 0
            self.qty_out = 0

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

    def _get_workshop_locations(self):
        """Obtiene las ubicaciones necesarias para los movimientos de taller."""
        warehouse = self.env['stock.warehouse'].search([
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        if not warehouse:
            raise UserError(_('No se encontró almacén para la compañía %s.') % self.company_id.name)

        stock_location = warehouse.lot_stock_id
        if not stock_location:
            raise UserError(_('No se encontró ubicación de stock en el almacén.'))

        production_location = self.env['stock.location'].search([
            ('usage', '=', 'production'),
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        if not production_location:
            production_location = self.env.ref('stock.location_production', raise_if_not_found=False)
        if not production_location:
            raise UserError(_('No se encontró ubicación de producción virtual.'))

        return warehouse, stock_location, production_location

    def _get_picking_type(self, warehouse, code='internal'):
        """Obtiene el tipo de operación del almacén."""
        picking_type = self.env['stock.picking.type'].search([
            ('warehouse_id', '=', warehouse.id),
            ('code', '=', code),
        ], limit=1)
        if not picking_type:
            # Fallback: buscar cualquier tipo interno de la compañía
            picking_type = self.env['stock.picking.type'].search([
                ('code', '=', code),
                ('company_id', '=', self.company_id.id),
            ], limit=1)
        return picking_type

    def _create_picking(self, picking_type, location_src, location_dest, product, qty, lot, origin):
        """Crea un picking, lo confirma, asigna cantidad y valida."""
        self.ensure_one()

        # Detectar nombres de campos disponibles en stock.move
        move_fields = self.env['stock.move'].fields_get()
        move_line_fields = self.env['stock.move.line'].fields_get()

        # Construir vals del move
        move_vals = {
            'product_id': product.id,
            'location_id': location_src.id,
            'location_dest_id': location_dest.id,
            'company_id': self.company_id.id,
        }

        # name vs description (Odoo 19 renombró name a description en algunos casos)
        if 'name' in move_fields:
            move_vals['name'] = f'{origin} - {product.name}'
        elif 'description' in move_fields:
            move_vals['description'] = f'{origin} - {product.name}'

        # product_uom vs product_uom_id
        if 'product_uom_id' in move_fields:
            move_vals['product_uom_id'] = product.uom_id.id
        elif 'product_uom' in move_fields:
            move_vals['product_uom'] = product.uom_id.id

        # quantity fields - en Odoo 18+ es 'quantity', en versiones anteriores 'product_uom_qty'
        if 'product_uom_qty' in move_fields:
            move_vals['product_uom_qty'] = qty
        if 'quantity' in move_fields:
            move_vals['quantity'] = qty

        # Detectar campo de moves en picking (move_ids vs move_lines)
        picking_fields = self.env['stock.picking'].fields_get()
        if 'move_ids' in picking_fields:
            move_field_name = 'move_ids'
        else:
            move_field_name = 'move_lines'

        # Crear el picking
        picking_vals = {
            'picking_type_id': picking_type.id,
            'location_id': location_src.id,
            'location_dest_id': location_dest.id,
            'origin': origin,
            'company_id': self.company_id.id,
            move_field_name: [(0, 0, move_vals)],
        }

        picking = self.env['stock.picking'].create(picking_vals)
        _logger.info('>>> WORKSHOP picking created: %s (id=%s)', picking.name, picking.id)

        # Confirmar picking
        picking.action_confirm()
        _logger.info('>>> WORKSHOP picking confirmed, state=%s', picking.state)

        # Obtener los moves del picking
        moves = picking[move_field_name]

        # Intentar reservar
        try:
            picking.action_assign()
            _logger.info('>>> WORKSHOP picking assigned, state=%s', picking.state)
        except Exception as e:
            _logger.warning('>>> WORKSHOP action_assign failed (may be normal for production locations): %s', e)

        # Asignar lote y cantidad en las move lines
        for move in moves:
            if move.move_line_ids:
                for ml in move.move_line_ids:
                    ml_vals = {'lot_id': lot.id}
                    # En Odoo 17+ el campo es 'quantity', en anteriores 'qty_done'
                    if 'quantity' in move_line_fields and 'qty_done' not in move_line_fields:
                        ml_vals['quantity'] = qty
                    elif 'qty_done' in move_line_fields:
                        ml_vals['qty_done'] = qty
                    else:
                        ml_vals['quantity'] = qty
                    ml.write(ml_vals)
            else:
                # Crear move line manualmente si no se creó
                ml_vals = {
                    'move_id': move.id,
                    'picking_id': picking.id,
                    'product_id': product.id,
                    'lot_id': lot.id,
                    'location_id': location_src.id,
                    'location_dest_id': location_dest.id,
                    'product_uom_id': product.uom_id.id,
                    'company_id': self.company_id.id,
                }
                if 'quantity' in move_line_fields and 'qty_done' not in move_line_fields:
                    ml_vals['quantity'] = qty
                elif 'qty_done' in move_line_fields:
                    ml_vals['qty_done'] = qty
                else:
                    ml_vals['quantity'] = qty

                self.env['stock.move.line'].create(ml_vals)
                _logger.info('>>> WORKSHOP created move line manually for move %s', move.id)

        # Validar el picking usando button_validate (maneja wizards de backorder, etc.)
        try:
            res = picking.with_context(
                skip_backorder=True,
                skip_immediate=True,
                skip_sms=True,
                cancel_backorder=True,
            ).button_validate()

            # Si button_validate retorna un wizard (dict con res_model), procesarlo
            if isinstance(res, dict) and res.get('res_model'):
                wizard_model = res['res_model']
                wizard_id = res.get('res_id')
                _logger.info('>>> WORKSHOP button_validate returned wizard: %s', wizard_model)

                if wizard_model == 'stock.immediate.transfer':
                    wizard = self.env[wizard_model].browse(wizard_id) if wizard_id else \
                        self.env[wizard_model].with_context(
                            **res.get('context', {})
                        ).create({})
                    wizard.process()

                elif wizard_model == 'stock.backorder.confirmation':
                    wizard = self.env[wizard_model].browse(wizard_id) if wizard_id else \
                        self.env[wizard_model].with_context(
                            **res.get('context', {})
                        ).create({})
                    # Procesar sin backorder
                    if hasattr(wizard, 'process_cancel_backorder'):
                        wizard.process_cancel_backorder()
                    elif hasattr(wizard, 'process'):
                        wizard.process()

        except Exception as e:
            _logger.error('>>> WORKSHOP button_validate failed: %s', e)
            # Fallback: intentar con _action_done en los moves
            try:
                for move in moves:
                    if move.state not in ('done', 'cancel'):
                        move._action_done()
                _logger.info('>>> WORKSHOP fallback _action_done succeeded')
            except Exception as e2:
                _logger.error('>>> WORKSHOP fallback _action_done also failed: %s', e2)
                raise UserError(
                    _('No se pudo validar el picking %s. Error: %s') % (picking.name, str(e))
                )

        _logger.info(
            '>>> WORKSHOP picking done: %s, state=%s, product=%s, lot=%s, qty=%s',
            picking.name, picking.state, product.name, lot.name, qty
        )
        return picking

    def _create_production(self):
        """Crea movimientos de stock: consume lote entrada, produce lote salida."""
        self.ensure_one()
        qty_out = self.qty_out or self.qty_in or 1
        qty_in = self.qty_in or qty_out

        warehouse, stock_location, production_location = self._get_workshop_locations()
        picking_type = self._get_picking_type(warehouse, 'internal')
        if not picking_type:
            raise UserError(_('No se encontró tipo de operación interna en el almacén.'))

        # Crear lote de salida
        lot_out = self.env['stock.lot'].create({
            'name': self.lot_out_name,
            'product_id': self.product_out_id.id,
            'company_id': self.company_id.id,
        })
        _logger.info('>>> WORKSHOP lot created: %s (id=%s)', lot_out.name, lot_out.id)

        # === PICKING 1: CONSUMO - lote entrada sale del stock hacia producción ===
        picking_consume = self._create_picking(
            picking_type=picking_type,
            location_src=stock_location,
            location_dest=production_location,
            product=self.product_in_id,
            qty=qty_in,
            lot=self.lot_in_id,
            origin=self.name,
        )
        self.picking_consume_id = picking_consume.id

        # === PICKING 2: PRODUCCIÓN - lote salida entra al stock desde producción ===
        picking_produce = self._create_picking(
            picking_type=picking_type,
            location_src=production_location,
            location_dest=stock_location,
            product=self.product_out_id,
            qty=qty_out,
            lot=lot_out,
            origin=self.name,
        )
        self.picking_produce_id = picking_produce.id

        _logger.info(
            '>>> WORKSHOP production complete: consumed lot %s (%s), produced lot %s (%s)',
            self.lot_in_id.name, qty_in, lot_out.name, qty_out
        )


class WorkshopOrderLine(models.Model):
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
                line.area_sqm = 0