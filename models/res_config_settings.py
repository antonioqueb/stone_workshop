from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    workshop_daily_capacity_hours = fields.Float(
        string='Capacidad diaria del taller (h)',
        config_parameter='stone_workshop.daily_capacity_hours',
        default=8.0,
        help='Horas-máquina disponibles por día en el taller. Base del indicador '
             '"próximo espacio en taller": próximo_espacio = trabajo pendiente ÷ esta capacidad. '
             'Si hay varias máquinas en paralelo, súmalas (ej. 2 máquinas = 16).',
    )
