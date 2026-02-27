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
    production_id = fields.Many2one('mrp.production', string='Orden Producción', readonly=True, copy=False)
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
        _logger.info('>>> WORKSHOP _onchange_lot_in CALLED, lot_in_id=%s', self.lot_in_id)
        if self.lot_in_id:
            _logger.info(
                '>>> lot_in_id.id=%s, product_in_id=%s, company=%s',
                self.lot_in_id.id,
                self.product_in_id.id if self.product_in_id else None,
                self.company_id.id if self.company_id else None
            )
            quants = self.env['stock.quant'].search([
                ('lot_id', '=', self.lot_in_id.id),
                ('location_id.usage', '=', 'internal'),
            ])
            _logger.info(
                '>>> quants found: %d, details: %s',
                len(quants),
                [(q.location_id.complete_name, q.quantity) for q in quants]
            )
            total_qty = sum(quants.mapped('quantity'))
            _logger.info('>>> total_qty=%s, setting qty_in and qty_out', total_qty)
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

    def _create_production(self):
        """Crea movimientos de stock: consume lote entrada, produce lote salida."""
        self.ensure_one()
        qty = self.qty_out or self.qty_in or 1

        # Crear lote de salida
        lot_out = self.env['stock.lot'].create({
            'name': self.lot_out_name,
            'product_id': self.product_out_id.id,
            'company_id': self.company_id.id,
        })

        warehouse = self.env['stock.warehouse'].search([
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        production_location = self.env['stock.location'].search([
            ('usage', '=', 'production'),
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        if not production_location:
            production_location = self.env.ref('stock.location_production', raise_if_not_found=False)

        stock_location = warehouse.lot_stock_id if warehouse else self.env['stock.location'].search([
            ('usage', '=', 'internal'),
            ('company_id', '=', self.company_id.id),
        ], limit=1)

        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'mrp_operation'),
            ('warehouse_id', '=', warehouse.id),
        ], limit=1)
        if not picking_type:
            picking_type = self.env['stock.picking.type'].search([
                ('code', '=', 'internal'),
                ('warehouse_id', '=', warehouse.id),
            ], limit=1)

        # Movimiento de consumo: lote entrada sale del stock
        consume_move = self.env['stock.move'].create({
            'name': f'{self.name} - Consumo {self.product_in_id.name}',
            'product_id': self.product_in_id.id,
            'product_uom_qty': self.qty_in or qty,
            'product_uom': self.product_in_id.uom_id.id,
            'location_id': stock_location.id,
            'location_dest_id': production_location.id,
            'company_id': self.company_id.id,
            'origin': self.name,
        })
        consume_move._action_confirm()
        consume_move.move_line_ids.write({
            'lot_id': self.lot_in_id.id,
            'quantity': self.qty_in or qty,
        })
        if not consume_move.move_line_ids:
            self.env['stock.move.line'].create({
                'move_id': consume_move.id,
                'product_id': self.product_in_id.id,
                'lot_id': self.lot_in_id.id,
                'location_id': stock_location.id,
                'location_dest_id': production_location.id,
                'quantity': self.qty_in or qty,
                'product_uom_id': self.product_in_id.uom_id.id,
            })
        consume_move._action_done()

        # Movimiento de producción: lote salida entra al stock
        produce_move = self.env['stock.move'].create({
            'name': f'{self.name} - Producción {self.product_out_id.name}',
            'product_id': self.product_out_id.id,
            'product_uom_qty': qty,
            'product_uom': self.product_out_id.uom_id.id,
            'location_id': production_location.id,
            'location_dest_id': stock_location.id,
            'company_id': self.company_id.id,
            'origin': self.name,
        })
        produce_move._action_confirm()
        produce_move.move_line_ids.write({
            'lot_id': lot_out.id,
            'quantity': qty,
        })
        if not produce_move.move_line_ids:
            self.env['stock.move.line'].create({
                'move_id': produce_move.id,
                'product_id': self.product_out_id.id,
                'lot_id': lot_out.id,
                'location_id': production_location.id,
                'location_dest_id': stock_location.id,
                'quantity': qty,
                'product_uom_id': self.product_out_id.uom_id.id,
            })
        produce_move._action_done()

        _logger.info(
            '>>> WORKSHOP production done: consumed lot %s (%s), produced lot %s (%s)',
            self.lot_in_id.name, self.qty_in, lot_out.name, qty
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