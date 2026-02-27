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

    # Para cortes / formatos
    format_width = fields.Float(string='Ancho (cm)', digits=(12, 2))
    format_height = fields.Float(string='Alto (cm)', digits=(12, 2))
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
            if rec.process_type == 'cut' and rec.format_width and rec.format_height and rec.format_qty:
                rec.area_sqm = (rec.format_width / 100) * (rec.format_height / 100) * rec.format_qty
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

    @api.onchange('process_type')
    def _onchange_process_type(self):
        if self.process_type == 'finish':
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
        # Buscar o crear BoM simple
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
        # Crear lote de salida
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
    width = fields.Float(string='Ancho (cm)', digits=(12, 2))
    height = fields.Float(string='Alto (cm)', digits=(12, 2))
    qty = fields.Integer(string='Piezas', default=1)
    area_sqm = fields.Float(string='Área m²', compute='_compute_area', store=True, digits=(12, 4))

    @api.depends('width', 'height', 'qty')
    def _compute_area(self):
        for line in self:
            line.area_sqm = (line.width / 100) * (line.height / 100) * line.qty if line.width and line.height else 0
