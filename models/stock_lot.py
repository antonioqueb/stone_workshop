# -*- coding: utf-8 -*-
from odoo import fields, models


class StockLot(models.Model):
    _inherit = 'stock.lot'

    # stock.lot no trae active en el core de Odoo 19; se agrega para que la
    # reclasificación pueda archivar el lote original (no puede borrarse: los
    # movimientos de inventario y la línea de reclasificación lo referencian).
    active = fields.Boolean(
        default=True,
        help='Un lote archivado deja de aparecer en listados y selectores. '
             'La reclasificación archiva el lote original al transferir sus '
             'existencias al lote espejo.',
    )
