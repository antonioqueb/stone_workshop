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
