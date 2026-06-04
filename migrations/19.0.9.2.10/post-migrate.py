"""Inicializa el cronómetro de trabajo para órdenes ya en taller.

Antes de esta versión no se medía el tiempo neto de trabajo. Para que las
órdenes que YA están `in_workshop` arranquen con el cronómetro corriendo, se
crea una sesión abierta a partir del momento del upgrade (no desde date_start,
para no inflar el tiempo con horas/noches en que nadie trabajó).

Las órdenes `done` históricas no se rellenan: su tiempo trabajado real no se
registró nunca, así que se dejan en 0 en lugar de inventar un valor de
reloj-de-pared.
"""
import logging

from odoo import api, SUPERUSER_ID, fields

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    Session = env['workshop.work.session']

    active_orders = env['workshop.order'].search([('state', '=', 'in_workshop')])
    now = fields.Datetime.now()
    created = 0
    for order in active_orders:
        # No duplicar si por alguna razón ya existe una sesión abierta.
        if order.work_session_ids.filtered(lambda s: not s.end):
            continue
        Session.create({
            'order_id': order.id,
            'responsible_id': (order.responsible_id.id or SUPERUSER_ID),
            'start': now,
        })
        created += 1

    _logger.info(
        "[stone_workshop] Cronómetro inicializado: %s sesión(es) abiertas para órdenes en taller.",
        created,
    )
