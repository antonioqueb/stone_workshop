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

    _code_uniq = models.Constraint(
        'unique(code)',
        'El código del proceso debe ser único.',
    )

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
