# -*- coding: utf-8 -*-
"""Operadores de la tableta de taller.

La tableta entra con UN solo login de Odoo (el "usuario de tableta"). Para
saber quién hizo cada cosa, ese usuario tiene una lista de operadores
(Fernando, Luis...) que se configura en su ficha (pestaña "Taller en
tableta"). Antes de cada acción la app pregunta "¿Quién eres?" y manda el
`operator_id`; aquí se estampa en la OT, en la sesión de reloj y en la
corrida de bitácora.
"""
from odoo import api, fields, models


class WorkshopTabletOperator(models.Model):
    _name = 'workshop.tablet.operator'
    _description = 'Operador de tableta de taller'
    _order = 'sequence, name, id'

    name = fields.Char(string='Nombre', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    user_id = fields.Many2one(
        'res.users', string='Usuario de tableta', required=True, index=True,
        ondelete='cascade',
        help='Login compartido de la tableta en el que aparece este operador.',
    )
    linked_user_id = fields.Many2one(
        'res.users', string='Usuario real (opcional)',
        help='Si el operador también tiene su propio usuario en Odoo, las OTs '
             'que trabaje quedarán con él como responsable.',
    )
    color = fields.Integer(string='Color', default=0)
    company_id = fields.Many2one(related='user_id.company_id', store=True, readonly=True)

    def _tablet_payload(self):
        self.ensure_one()
        parts = [p for p in (self.name or '').split() if p]
        initials = ''.join(p[0] for p in parts[:2]).upper() or '?'
        return {
            'id': self.id,
            'name': self.name,
            'initials': initials,
            'color': self.color or 0,
        }


class ResUsersTablet(models.Model):
    _inherit = 'res.users'

    workshop_tablet_operator_ids = fields.One2many(
        'workshop.tablet.operator', 'user_id', string='Operadores de tableta',
        help='Personas que pueden operar la tableta de taller con este login. '
             'La app pregunta quién es antes de cada acción.',
    )
    workshop_tablet_lock_minutes = fields.Integer(
        string='Bloqueo por inactividad (min)', default=10,
        help='Minutos sin tocar la tableta tras los cuales se vuelve a pedir '
             'la identidad del operador. 0 = pedirla en cada acción.',
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            'workshop_tablet_operator_ids', 'workshop_tablet_lock_minutes',
        ]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + [
            'workshop_tablet_operator_ids', 'workshop_tablet_lock_minutes',
        ]


class WorkshopOrderOperator(models.Model):
    _inherit = 'workshop.order'

    tablet_operator_id = fields.Many2one(
        'workshop.tablet.operator', string='Operador (tableta)', copy=False,
        tracking=True,
        help='Último operador que tocó la orden desde la tableta.',
    )


class WorkshopWorkSessionOperator(models.Model):
    _inherit = 'workshop.work.session'

    operator_id = fields.Many2one(
        'workshop.tablet.operator', string='Operador', copy=False,
        help='Quién abrió esta sesión de trabajo desde la tableta.',
    )


class WorkshopProgressLogOperator(models.Model):
    _inherit = 'workshop.progress.log'

    operator_id = fields.Many2one(
        'workshop.tablet.operator', string='Operador', copy=False,
        help='Quién registró esta corrida desde la tableta.',
    )
