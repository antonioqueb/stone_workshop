import json

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class WorkshopTicketWizard(models.TransientModel):
    _name = 'workshop.ticket.wizard'
    _description = 'Wizard de Ticket de Taller'

    order_id = fields.Many2one(
        'workshop.order',
        string='Orden de Taller',
        required=True,
    )
    editing_ticket_id = fields.Many2one(
        'workshop.ticket',
        string='Ticket a editar',
    )
    ticket_id = fields.Many2one(
        'workshop.ticket',
        string='Ticket generado',
        readonly=True,
    )
    is_editing = fields.Boolean(
        compute='_compute_is_editing',
        string='Modo edición',
    )
    selector_anchor = fields.Boolean(
        string='Selector de placas',
        default=True,
    )
    widget_selections = fields.Text(
        string='Selecciones del selector',
        default='[]',
    )
    total_selected_count = fields.Integer(
        compute='_compute_totals',
        string='Placas seleccionadas',
    )
    total_selected_area = fields.Float(
        compute='_compute_totals',
        string='m² seleccionados',
        digits=(12, 4),
    )
    notes = fields.Text(string='Notas / Instrucciones')

    @api.depends('editing_ticket_id')
    def _compute_is_editing(self):
        for wiz in self:
            wiz.is_editing = bool(wiz.editing_ticket_id)

    @api.depends('widget_selections')
    def _compute_totals(self):
        for wiz in self:
            count = 0
            area = 0.0
            try:
                selections = json.loads(wiz.widget_selections or '[]')
            except (TypeError, json.JSONDecodeError):
                selections = []

            if isinstance(selections, list):
                for item in selections:
                    try:
                        input_line_id = int(item.get('inputLineId') or 0)
                    except (TypeError, ValueError):
                        input_line_id = 0
                    if not input_line_id:
                        continue
                    count += 1
                    try:
                        area += float(item.get('areaSqm') or 0.0)
                    except (TypeError, ValueError):
                        pass

            wiz.total_selected_count = count
            wiz.total_selected_area = area

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        order_id = (
            res.get('order_id')
            or self.env.context.get('default_order_id')
            or self.env.context.get('active_id')
        )
        if order_id:
            order = self.env['workshop.order'].browse(order_id).exists()
            if order:
                res['order_id'] = order.id

        editing_ticket_id = (
            res.get('editing_ticket_id')
            or self.env.context.get('default_editing_ticket_id')
        )
        if editing_ticket_id:
            ticket = self.env['workshop.ticket'].browse(editing_ticket_id).exists()
            if ticket:
                res['editing_ticket_id'] = ticket.id
                res['ticket_id'] = ticket.id
                res['order_id'] = ticket.order_id.id
                res['notes'] = ticket.notes or ''
                res['widget_selections'] = json.dumps([
                    {
                        'inputLineId': line.input_line_id.id,
                        'lotId': line.lot_id.id if line.lot_id else 0,
                        'lotName': line.lot_id.name if line.lot_id else '',
                        'productId': line.product_id.id if line.product_id else 0,
                        'areaSqm': line.area_sqm or 0.0,
                        'qty': line.qty or 0.0,
                    }
                    for line in ticket.line_ids
                ])

        return res

    def _get_selections(self):
        self.ensure_one()
        try:
            raw = json.loads(self.widget_selections or '[]')
        except (TypeError, json.JSONDecodeError):
            raw = []

        if not isinstance(raw, list):
            raw = []

        input_line_ids = []
        for item in raw:
            try:
                input_line_id = int(item.get('inputLineId') or 0)
            except (TypeError, ValueError):
                input_line_id = 0
            if input_line_id and input_line_id not in input_line_ids:
                input_line_ids.append(input_line_id)

        if not input_line_ids:
            raise UserError(_('Selecciona al menos una placa/lote para generar el ticket.'))

        lines = self.env['workshop.input.line'].browse(input_line_ids).exists()
        if len(lines) != len(input_line_ids):
            raise UserError(_('Una o más placas seleccionadas ya no existen. Recarga el selector.'))

        return lines

    def _validate_no_ticket_collision(self, input_lines, exclude_ticket_id=None):
        self.ensure_one()
        ticket_map = self.order_id._get_ticket_line_to_ticket_map(
            exclude_ticket_id=exclude_ticket_id
        )
        collisions = {}
        for line in input_lines:
            if line.id in ticket_map:
                collisions[line.lot_id.name or line.display_name] = ticket_map[line.id]

        if collisions:
            msg = '\n'.join(
                '• %s → %s' % (lot, ', '.join(ticket_names))
                for lot, ticket_names in collisions.items()
            )
            raise UserError(_(
                'Las siguientes placas ya están en otro ticket abierto o consumido:\n\n%s\n\n'
                'Edita/cancela el otro ticket antes de continuar.'
            ) % msg)

    def _prepare_ticket_line_commands(self, input_lines):
        self.ensure_one()
        commands = []
        seq = 10
        for line in input_lines.sorted(lambda l: (l.product_id.display_name or '', l.lot_id.name or '', l.id)):
            commands.append((0, 0, {
                'sequence': seq,
                'input_line_id': line.id,
                'qty': line.qty_in or 0.0,
                'area_sqm': self.order_id._input_line_area(line),
            }))
            seq += 10
        return commands

    def action_generate_ticket(self):
        self.ensure_one()
        if self.order_id.state != 'in_workshop':
            raise UserError(_('Sólo se pueden generar tickets cuando la orden está en taller.'))

        input_lines = self._get_selections()
        target_ticket = self.editing_ticket_id or (
            self.ticket_id
            if self.ticket_id and self.ticket_id.state == 'prepared'
            else False
        )
        self._validate_no_ticket_collision(
            input_lines,
            exclude_ticket_id=target_ticket.id if target_ticket else None,
        )

        commands = self._prepare_ticket_line_commands(input_lines)
        if target_ticket:
            if target_ticket.state != 'prepared':
                raise UserError(_('Sólo se pueden editar tickets en estado Preparado.'))
            target_ticket.line_ids.unlink()
            target_ticket.write({
                'notes': self.notes or False,
                'line_ids': commands,
            })
            target_ticket.action_prepare()
            ticket = target_ticket
        else:
            ticket = self.env['workshop.ticket'].create({
                'order_id': self.order_id.id,
                'responsible_id': self.env.user.id,
                'notes': self.notes or False,
                'line_ids': commands,
            })
            ticket.action_prepare()

        self.ticket_id = ticket.id
        self.editing_ticket_id = ticket.id

        return self.env.ref(
            'stone_workshop.action_report_workshop_ticket'
        ).report_action(ticket)

    def action_generate_and_consume_ticket(self):
        self.ensure_one()
        result = self.action_generate_ticket()
        ticket = self.ticket_id or self.editing_ticket_id
        if ticket:
            ticket.action_mark_consumed()
        return result
