from collections import OrderedDict

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


WORKSHOP_TICKET_LOCK_STATES = ('draft', 'prepared', 'consumed')


class WorkshopOrder(models.Model):
    _inherit = 'workshop.order'

    workshop_ticket_ids = fields.One2many(
        'workshop.ticket',
        'order_id',
        string='Tickets de Taller',
    )
    workshop_ticket_count = fields.Integer(
        string='Tickets',
        compute='_compute_workshop_ticket_counts',
    )
    workshop_ticket_open_count = fields.Integer(
        string='Tickets Abiertos',
        compute='_compute_workshop_ticket_counts',
    )

    @api.depends('workshop_ticket_ids.state')
    def _compute_workshop_ticket_counts(self):
        for order in self:
            tickets = order.workshop_ticket_ids
            order.workshop_ticket_count = len(tickets)
            order.workshop_ticket_open_count = len(
                tickets.filtered(lambda t: t.state in ('draft', 'prepared'))
            )

    def _get_open_workshop_tickets(self):
        self.ensure_one()
        return self.workshop_ticket_ids.filtered(
            lambda t: t.state in ('draft', 'prepared')
        ).sorted(lambda t: (t.create_date or fields.Datetime.now(), t.id), reverse=True)

    def _get_locked_workshop_ticket_input_line_ids(self, exclude_ticket_id=None):
        self.ensure_one()
        tickets = self.workshop_ticket_ids.filtered(
            lambda t: t.state in WORKSHOP_TICKET_LOCK_STATES
        )
        if exclude_ticket_id:
            tickets = tickets.filtered(lambda t: t.id != exclude_ticket_id)
        return set(tickets.mapped('line_ids.input_line_id').ids)

    def _get_ticket_line_to_ticket_map(self, exclude_ticket_id=None):
        self.ensure_one()
        tickets = self.workshop_ticket_ids.filtered(
            lambda t: t.state in WORKSHOP_TICKET_LOCK_STATES
        )
        if exclude_ticket_id:
            tickets = tickets.filtered(lambda t: t.id != exclude_ticket_id)

        result = {}
        for ticket in tickets:
            for line in ticket.line_ids:
                if line.input_line_id:
                    result.setdefault(line.input_line_id.id, []).append(ticket.name)
        return result

    def get_workshop_ticket_selector_data(self, editing_ticket_id=None):
        self.ensure_one()

        editing_ticket = self.env['workshop.ticket'].browse(
            int(editing_ticket_id or 0)
        ).exists()
        editing_input_ids = set(editing_ticket.mapped('line_ids.input_line_id').ids)
        locked_input_ids = self._get_locked_workshop_ticket_input_line_ids(
            exclude_ticket_id=editing_ticket.id if editing_ticket else None
        )

        groups_map = OrderedDict()
        active_lines = self.input_line_ids.filtered(
            lambda line: line.state != 'cancelled'
            and line.lot_id
            and line.product_id
            and line.is_consumed
        ).sorted(lambda line: (
            line.product_id.display_name or '',
            line.lot_id.name or '',
            line.sequence or 0,
            line.id,
        ))

        for line in active_lines:
            if line.id in locked_input_ids:
                continue
            if line.is_used and line.id not in editing_input_ids:
                continue

            product = line.product_id
            pid = product.id
            if pid not in groups_map:
                groups_map[pid] = {
                    'groupKey': 'product-%s' % pid,
                    'productId': pid,
                    'productName': product.display_name or '',
                    'lines': [],
                    'lineCount': 0,
                    'selectedCount': 0,
                    'totalArea': 0.0,
                }

            area = self._input_line_area(line)
            selected = line.id in editing_input_ids

            line_data = {
                'rowKey': 'input-%s' % line.id,
                'inputLineId': line.id,
                'lotId': line.lot_id.id,
                'lotName': line.lot_id.name or '',
                'productId': pid,
                'productName': product.display_name or '',
                'qty': line.qty_in or 0.0,
                'areaSqm': area or 0.0,
                'widthCm': line.width_cm or 0.0,
                'heightCm': line.height_cm or 0.0,
                'thicknessCm': line.thickness_cm or 0.0,
                'blockName': line.block_name or '',
                'tone': line.tone or '',
                'locationId': line.location_id.id if line.location_id else 0,
                'locationName': line.location_id.display_name if line.location_id else '',
                'state': line.state or '',
                'isUsed': bool(line.is_used),
                'isSelected': selected,
            }

            group = groups_map[pid]
            group['lines'].append(line_data)
            group['lineCount'] += 1
            if selected:
                group['selectedCount'] += 1
                group['totalArea'] += area or 0.0

        return [group for group in groups_map.values() if group['lineCount'] > 0]

    def action_open_workshop_ticket_wizard(self):
        self.ensure_one()
        if self.state != 'in_workshop':
            raise UserError(_(
                'Los tickets de taller sólo se generan cuando la orden está en taller.'
            ))
        if not self.input_line_ids.filtered(lambda l: l.state != 'cancelled' and l.is_consumed):
            raise UserError(_('No hay placas consumidas en taller para generar ticket.'))

        view = self.env.ref(
            'stone_workshop.workshop_ticket_wizard_form',
            raise_if_not_found=False,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Crear Ticket de Taller'),
            'res_model': 'workshop.ticket.wizard',
            'view_mode': 'form',
            'views': [(view.id if view else False, 'form')],
            'target': 'new',
            'context': {
                'default_order_id': self.id,
                'active_id': self.id,
                'active_model': 'workshop.order',
            },
        }

    def action_view_workshop_tickets(self):
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Tickets de Taller'),
            'res_model': 'workshop.ticket',
            'view_mode': 'list,form',
            'domain': [('order_id', '=', self.id)],
            'context': {'default_order_id': self.id},
        }
        if len(self.workshop_ticket_ids) == 1:
            action.update({
                'view_mode': 'form',
                'res_id': self.workshop_ticket_ids.id,
            })
        return action


class WorkshopTicket(models.Model):
    _name = 'workshop.ticket'
    _description = 'Ticket de Taller'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'

    name = fields.Char(
        string='Folio',
        default='/',
        readonly=True,
        copy=False,
        tracking=True,
    )
    order_id = fields.Many2one(
        'workshop.order',
        string='Orden de Taller',
        required=True,
        ondelete='cascade',
        index=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        related='order_id.company_id',
        store=True,
        readonly=True,
    )
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('prepared', 'Preparado'),
        ('consumed', 'Consumido'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', required=True, tracking=True)

    responsible_id = fields.Many2one(
        'res.users',
        string='Responsable',
        default=lambda self: self.env.user,
        tracking=True,
    )
    date_ticket = fields.Datetime(
        string='Fecha de ticket',
        default=fields.Datetime.now,
        tracking=True,
    )
    consumed_date = fields.Datetime(
        string='Fecha de consumo',
        readonly=True,
        copy=False,
    )
    progress_log_id = fields.Many2one(
        'workshop.progress.log',
        string='Corrida generada',
        readonly=True,
        copy=False,
    )
    notes = fields.Text(string='Notas / Instrucciones')
    line_ids = fields.One2many(
        'workshop.ticket.line',
        'ticket_id',
        string='Placas / lotes del ticket',
    )
    line_count = fields.Integer(
        string='Líneas',
        compute='_compute_totals',
    )
    total_qty = fields.Float(
        string='Cantidad total',
        compute='_compute_totals',
        digits=(12, 4),
    )
    total_area_sqm = fields.Float(
        string='Área total m²',
        compute='_compute_totals',
        digits=(12, 4),
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'workshop.ticket'
                ) or '/'
        return super().create(vals_list)

    @api.depends('line_ids.qty', 'line_ids.area_sqm')
    def _compute_totals(self):
        for ticket in self:
            ticket.line_count = len(ticket.line_ids)
            ticket.total_qty = sum(ticket.line_ids.mapped('qty'))
            ticket.total_area_sqm = sum(ticket.line_ids.mapped('area_sqm'))

    def _validate_ticket_lines(self):
        for ticket in self:
            if not ticket.line_ids:
                raise UserError(_('Selecciona al menos una placa/lote para el ticket.'))

            if ticket.order_id.state != 'in_workshop':
                raise UserError(_(
                    'Sólo se pueden preparar tickets cuando la orden está en taller.'
                ))

            input_lines = ticket.line_ids.mapped('input_line_id')
            duplicates = {}
            seen = set()
            for line in input_lines:
                if line.id in seen:
                    duplicates[line.lot_id.name or line.display_name] = True
                seen.add(line.id)
                if line.order_id != ticket.order_id:
                    raise ValidationError(_(
                        'La placa %(lot)s pertenece a otra orden de taller.'
                    ) % {'lot': line.lot_id.name or line.display_name})
                if line.state == 'cancelled':
                    raise ValidationError(_(
                        'La placa %(lot)s está cancelada y no puede usarse en ticket.'
                    ) % {'lot': line.lot_id.name or line.display_name})
                if not line.is_consumed:
                    raise ValidationError(_(
                        'La placa %(lot)s aún no fue enviada al taller.'
                    ) % {'lot': line.lot_id.name or line.display_name})
                if line.is_used and ticket.state != 'consumed':
                    raise ValidationError(_(
                        'La placa %(lot)s ya fue registrada como usada en una corrida.'
                    ) % {'lot': line.lot_id.name or line.display_name})

            if duplicates:
                raise ValidationError(_(
                    'Hay placas duplicadas dentro del mismo ticket: %s'
                ) % ', '.join(duplicates.keys()))

            locked_map = ticket.order_id._get_ticket_line_to_ticket_map(
                exclude_ticket_id=ticket.id
            )
            collisions = {}
            for input_line in input_lines:
                if input_line.id in locked_map:
                    collisions[input_line.lot_id.name or input_line.display_name] = locked_map[input_line.id]

            if collisions:
                msg = '\n'.join(
                    '• %s → %s' % (lot, ', '.join(ticket_names))
                    for lot, ticket_names in collisions.items()
                )
                raise ValidationError(_(
                    'Las siguientes placas ya están incluidas en otro ticket abierto o consumido:\n\n%s'
                ) % msg)

    def action_prepare(self):
        for ticket in self:
            if ticket.state not in ('draft', 'prepared'):
                continue
            ticket._validate_ticket_lines()
            ticket.write({'state': 'prepared'})
            ticket.message_post(body=_(
                'Ticket preparado con %(count)s placa(s), %(area).4f m².'
            ) % {
                'count': ticket.line_count,
                'area': ticket.total_area_sqm,
            })
        return True

    def action_mark_consumed(self):
        for ticket in self:
            if ticket.state == 'consumed':
                continue
            if ticket.state == 'cancelled':
                raise UserError(_('No puedes consumir un ticket cancelado.'))

            if ticket.state == 'draft':
                ticket.action_prepare()

            ticket._validate_ticket_lines()
            input_lines = ticket.line_ids.mapped('input_line_id')
            area = sum(ticket.order_id._input_line_area(line) for line in input_lines)

            # El ticket consume cada placa íntegra por defecto (puede ajustarse
            # luego desde el selector visual de la bitácora). Una fila de
            # consumo por placa con su área total.
            consumption_commands = [
                (0, 0, {
                    'input_line_id': line.id,
                    'consumed_sqm': ticket.order_id._input_line_area(line),
                })
                for line in input_lines
            ]
            progress_log = self.env['workshop.progress.log'].create({
                'order_id': ticket.order_id.id,
                'ticket_id': ticket.id,
                'date': fields.Date.context_today(ticket),
                'responsible_id': ticket.responsible_id.id or self.env.user.id,
                'consumption_line_ids': consumption_commands,
                'area_sqm': area,
                'notes': ticket.notes or _('Consumo registrado desde %s') % ticket.name,
            })

            ticket.write({
                'state': 'consumed',
                'consumed_date': fields.Datetime.now(),
                'progress_log_id': progress_log.id,
            })
            ticket.message_post(body=_(
                'Ticket consumido. Se generó la corrida %(log)s con %(count)s placa(s).'
            ) % {
                'log': progress_log.display_name,
                'count': len(input_lines),
            })
        return True

    def action_cancel(self):
        for ticket in self:
            if ticket.state == 'consumed':
                raise UserError(_(
                    'No se puede cancelar un ticket consumido porque ya generó una corrida. '
                    'Corrige la bitácora si necesitas revertir el consumo.'
                ))
            ticket.write({'state': 'cancelled'})
            ticket.message_post(body=_(
                'Ticket cancelado por %s. Las placas quedan liberadas para otro ticket.'
            ) % self.env.user.name)
        return True

    def action_edit_in_wizard(self):
        self.ensure_one()
        if self.state != 'prepared':
            raise UserError(_(
                'Sólo se pueden editar tickets en estado Preparado.'
            ))
        view = self.env.ref(
            'stone_workshop.workshop_ticket_wizard_form',
            raise_if_not_found=False,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Editar Ticket %s') % self.name,
            'res_model': 'workshop.ticket.wizard',
            'view_mode': 'form',
            'views': [(view.id if view else False, 'form')],
            'target': 'new',
            'context': {
                'default_order_id': self.order_id.id,
                'default_editing_ticket_id': self.id,
                'active_id': self.order_id.id,
                'active_model': 'workshop.order',
            },
        }

    def action_print_ticket(self):
        self.ensure_one()
        return self.env.ref(
            'stone_workshop.action_report_workshop_ticket'
        ).report_action(self)


class WorkshopTicketLine(models.Model):
    _name = 'workshop.ticket.line'
    _description = 'Línea de Ticket de Taller'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10)
    ticket_id = fields.Many2one(
        'workshop.ticket',
        string='Ticket',
        required=True,
        ondelete='cascade',
        index=True,
    )
    order_id = fields.Many2one(
        related='ticket_id.order_id',
        store=True,
        readonly=True,
    )
    input_line_id = fields.Many2one(
        'workshop.input.line',
        string='Placa / lote consumido',
        required=True,
        ondelete='restrict',
    )
    product_id = fields.Many2one(
        related='input_line_id.product_id',
        store=True,
        readonly=True,
    )
    lot_id = fields.Many2one(
        related='input_line_id.lot_id',
        store=True,
        readonly=True,
    )
    location_id = fields.Many2one(
        related='input_line_id.location_id',
        store=True,
        readonly=True,
    )
    block_name = fields.Char(
        related='input_line_id.block_name',
        store=True,
        readonly=True,
    )
    tone = fields.Char(
        related='input_line_id.tone',
        store=True,
        readonly=True,
    )
    qty = fields.Float(
        string='Cantidad',
        digits=(12, 4),
        help='Snapshot de cantidad incluido en el ticket.',
    )
    area_sqm = fields.Float(
        string='Área m²',
        digits=(12, 4),
        help='Snapshot de área incluido en el ticket.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for vals in vals_list:
            prepared.append(self._prepare_snapshot_values(vals))
        return super().create(prepared)

    def write(self, vals):
        clean_vals = dict(vals or {})
        if 'input_line_id' in clean_vals:
            for line in self:
                super(WorkshopTicketLine, line).write(
                    line._prepare_snapshot_values(dict(clean_vals), existing_line=line)
                )
            return True
        return super().write(clean_vals)

    @api.model
    def _prepare_snapshot_values(self, vals, existing_line=False):
        input_line_id = vals.get('input_line_id')
        if isinstance(input_line_id, (list, tuple)):
            input_line_id = input_line_id[0] if input_line_id else False
        if not input_line_id and existing_line:
            input_line = existing_line.input_line_id
        else:
            input_line = self.env['workshop.input.line'].browse(
                int(input_line_id or 0)
            ).exists()

        if input_line:
            order = input_line.order_id
            vals['input_line_id'] = input_line.id
            vals.setdefault('qty', input_line.qty_in or 0.0)
            vals.setdefault('area_sqm', order._input_line_area(input_line) if order else (input_line.area_sqm or input_line.qty_in or 0.0))
        return vals


class WorkshopProgressLog(models.Model):
    _inherit = 'workshop.progress.log'

    ticket_id = fields.Many2one(
        'workshop.ticket',
        string='Ticket de taller',
        readonly=True,
        copy=False,
    )
