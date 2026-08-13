# -*- coding: utf-8 -*-
import logging

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .som_date_format import som_format_date
from .som_history_log import som_sort_general_logs

_logger = logging.getLogger(__name__)


class StockLotWriteoff(models.Model):
    """Baja masiva de material (write-off).

    Espejo operativo de la reclasificación: mismo selector visual de lotes
    (por eso los campos se llaman product_from_id / lot_from_id, para heredar
    el widget), pero en vez de crear un lote espejo, el material SALE del
    inventario vía stock.scrap validado (ubicación de desecho) y el lote se
    archiva. El Walkthrough detecta estas salidas por el movimiento a la
    ubicación de scrap.
    """
    _name = 'stock.lot.writeoff'
    _description = 'Baja masiva de material (write-off)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'

    name = fields.Char(
        string='Folio',
        default='Nuevo',
        readonly=True,
        copy=False,
        tracking=True,
    )
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('done', 'Aplicada'),
        ('cancel', 'Cancelada'),
    ], string='Estado', default='draft', required=True, tracking=True, copy=False)

    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        default=lambda self: self.env.company,
        required=True,
    )
    user_id = fields.Many2one(
        'res.users',
        string='Responsable',
        default=lambda self: self.env.user,
        tracking=True,
    )
    date_done = fields.Datetime(string='Fecha de aplicación', readonly=True, copy=False)

    # Se llama product_from_id (no product_id) a propósito: el selector visual
    # heredado de la reclasificación lee ese campo del registro padre.
    product_from_id = fields.Many2one(
        'product.product',
        string='Producto',
        required=True,
        tracking=True,
        domain=[('tracking', '!=', 'none')],
        help='Producto cuyos lotes se darán de baja.',
    )
    reason_type = fields.Selection([
        ('broken', 'Rotura / material dañado'),
        ('shrinkage', 'Merma'),
        ('lost', 'Extravío / robo'),
        ('quality', 'Calidad / no vendible'),
        ('other', 'Otro'),
    ], string='Tipo de baja', required=True, default='broken', tracking=True)
    reason = fields.Text(
        string='Motivo',
        required=True,
        tracking=True,
        help='Justificación de la baja (queda en el historial de cada lote).',
    )
    line_ids = fields.One2many(
        'stock.lot.writeoff.line',
        'writeoff_id',
        string='Lotes a dar de baja',
        copy=False,
    )
    line_count = fields.Integer(compute='_compute_totals', string='Lotes')
    total_qty = fields.Float(
        compute='_compute_totals',
        string='Cantidad total',
        digits=(12, 4),
        help='Suma de las existencias dadas de baja (o por dar de baja).',
    )
    scrap_count = fields.Integer(compute='_compute_scrap_count', string='Desechos')

    @api.depends('line_ids.qty_moved', 'line_ids.qty_available')
    def _compute_totals(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)
            if rec.state == 'done':
                rec.total_qty = sum(rec.line_ids.mapped('qty_moved'))
            else:
                rec.total_qty = sum(rec.line_ids.mapped('qty_available'))

    def _compute_scrap_count(self):
        for rec in self:
            rec.scrap_count = len(rec.line_ids.scrap_ids)

    @api.model
    def _prune_ghost_line_commands(self, vals):
        """Misma barrera que la reclasificación: el selector visual puede
        dejar filas fantasma [0, 0, {}] sin lot_from_id si el update del
        cliente falla a medias; se descartan antes de escribir."""
        commands = vals.get('line_ids')
        if not commands:
            return
        pruned = [
            cmd for cmd in commands
            if not (
                isinstance(cmd, (list, tuple)) and len(cmd) == 3
                and cmd[0] == 0 and not (cmd[2] or {}).get('lot_from_id')
            )
        ]
        if len(pruned) != len(commands):
            _logger.warning(
                '[BAJA MATERIAL] Se descartaron %s línea(s) fantasma sin '
                'lot_from_id al guardar.', len(commands) - len(pruned),
            )
            vals['line_ids'] = pruned

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'Nuevo') == 'Nuevo':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'stock.lot.writeoff'
                ) or 'Nuevo'
            self._prune_ghost_line_commands(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._prune_ghost_line_commands(vals)
        return super().write(vals)

    def unlink(self):
        if any(rec.state == 'done' for rec in self):
            raise UserError(_(
                'No puedes eliminar una baja aplicada: es historial de '
                'inventario.'
            ))
        return super().unlink()

    # -------------------------------------------------------------------------
    # Validaciones previas (mismo criterio que la reclasificación)
    # -------------------------------------------------------------------------

    def _get_lot_internal_quants(self, lot):
        return self.env['stock.quant'].sudo().search([
            ('lot_id', '=', lot.id),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '!=', 0),
        ])

    def _assert_lines_applicable(self):
        self.ensure_one()

        if not self.line_ids:
            raise UserError(_('Agrega al menos un lote a dar de baja.'))

        # Un traslado interno de carrito/escáner abierto es reserva DÉBIL
        # (reacomodo de ubicación): no debe impedir la baja. Se libera antes
        # de validar (helper de inventory_shopping_cart; hasattr por si no
        # está instalado).
        Picking = self.env['stock.picking'].sudo()
        if hasattr(Picking, '_release_cart_internal_reservations'):
            release_lot_ids = [
                l.lot_from_id.id for l in self.line_ids if l.lot_from_id
            ]
            Picking._release_cart_internal_reservations(
                release_lot_ids,
                reason=_('Liberado automáticamente: el lote se va a dar '
                         'de baja.'),
            )

        Quant = self.env['stock.quant']
        committed_lot_ids = set()
        if hasattr(Quant, '_get_committed_lot_ids'):
            try:
                committed_lot_ids = set(
                    Quant._get_committed_lot_ids(self.product_from_id.id)
                )
            except Exception:
                _logger.exception(
                    '[BAJA MATERIAL] No se pudo consultar lotes comprometidos; '
                    'se continúa con las validaciones directas.'
                )

        seen = set()
        problems = []

        for line in self.line_ids:
            lot = line.lot_from_id

            if lot.id in seen:
                problems.append(_('El lote %s está repetido en la lista.') % lot.name)
                continue
            seen.add(lot.id)

            if lot.product_id != self.product_from_id:
                problems.append(_(
                    'El lote %(lot)s pertenece a %(product)s, no al producto '
                    'de esta baja.'
                ) % {'lot': lot.name, 'product': lot.product_id.display_name})
                continue

            quants = self._get_lot_internal_quants(lot)
            if not quants:
                problems.append(_(
                    'El lote %s no tiene existencias en ubicaciones internas.'
                ) % lot.name)
                continue

            if any((q.reserved_quantity or 0.0) > 0 for q in quants):
                problems.append(_(
                    'El lote %s tiene cantidad reservada (entrega, taller u '
                    'otro documento). Libera la reserva antes de darlo de baja.'
                ) % lot.name)
                continue

            if any(getattr(q, 'x_tiene_hold', False) for q in quants):
                problems.append(_(
                    'El lote %s tiene un apartado (hold) activo. Cancélalo '
                    'antes de darlo de baja.'
                ) % lot.name)
                continue

            if lot.id in committed_lot_ids:
                problems.append(_(
                    'El lote %s está comprometido en una venta, apartado u '
                    'orden de taller. Libéralo antes de darlo de baja.'
                ) % lot.name)
                continue

        if problems:
            raise UserError(_(
                'No se puede aplicar la baja de material:\n\n%s'
            ) % '\n'.join('- %s' % p for p in problems))

        return True

    # -------------------------------------------------------------------------
    # Aplicación
    # -------------------------------------------------------------------------

    def _get_scrap_location(self):
        """Odoo 19 eliminó el booleano scrap_location: el desecho es una
        ubicación usage='inventory'. Se prefiere la ubicación Scrap estándar
        (stock.stock_location_scrapped) y, si no aplica, la primera de tipo
        inventory de la compañía (mismo criterio que el default de
        stock.scrap)."""
        self.ensure_one()
        location = self.env.ref(
            'stock.stock_location_scrapped', raise_if_not_found=False)
        if location and (
            location.usage != 'inventory'
            or not location.active
            or location.company_id.id not in (False, self.company_id.id)
        ):
            location = None
        if not location:
            location = self.env['stock.location'].search([
                ('usage', '=', 'inventory'),
                ('company_id', 'in', [self.company_id.id, False]),
            ], order='id', limit=1)
        if not location:
            raise UserError(_(
                'No hay una ubicación de desecho (tipo "Pérdida de '
                'inventario") configurada para la compañía %s. Crea una en '
                'Inventario > Configuración > Ubicaciones.'
            ) % self.company_id.display_name)
        return location

    def _writeoff_stock_context(self):
        """Bypass de guardias externos (holds, lote completo, duplicados):
        la validación de negocio ya se hizo en _assert_lines_applicable."""
        return {
            'skip_hold_validation': True,
            'skip_whole_lot': True,
            'skip_whole_lot_removal': True,
            'skip_whole_lot_no_assign': True,
            'skip_duplicate_lot_validation': True,
            'skip_lot_duplicate_check': True,
            'skip_stock_lot_duplicate_check': True,
        }

    def action_confirm(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('Solo puedes aplicar bajas en borrador.'))
            if not self.env.user.has_group('stock.group_stock_manager'):
                raise UserError(_(
                    'Solo un administrador de inventario puede aplicar bajas '
                    'de material.'
                ))

            rec._assert_lines_applicable()
            scrap_location = rec._get_scrap_location()

            for line in rec.line_ids:
                line._apply_writeoff(scrap_location)

            rec.write({
                'state': 'done',
                'date_done': fields.Datetime.now(),
            })

            rec.message_post(body=Markup(_(
                'Baja de material aplicada: %(count)s lote(s), %(qty).4f de '
                '<strong>%(product)s</strong> enviados a desecho '
                '(%(location)s).<br/>Tipo: %(rtype)s. Motivo: %(reason)s'
            )) % {
                'count': len(rec.line_ids),
                'qty': sum(rec.line_ids.mapped('qty_moved')),
                'product': rec.product_from_id.display_name,
                'location': scrap_location.display_name,
                'rtype': dict(rec._fields['reason_type'].selection).get(
                    rec.reason_type, rec.reason_type),
                'reason': rec.reason or '',
            })

            _logger.info(
                '[BAJA MATERIAL] %s aplicada: %s lotes de %s por %s',
                rec.name,
                len(rec.line_ids),
                rec.product_from_id.display_name,
                self.env.user.name,
            )

        return True

    def action_cancel(self):
        for rec in self:
            if rec.state == 'done':
                raise UserError(_(
                    'No puedes cancelar una baja ya aplicada: el material ya '
                    'salió del inventario.'
                ))
            rec.write({'state': 'cancel'})
        return True

    def action_draft(self):
        for rec in self:
            if rec.state != 'cancel':
                raise UserError(_('Solo puedes reactivar bajas canceladas.'))
            rec.write({'state': 'draft'})
        return True

    def action_view_scraps(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Desechos de %s') % self.name,
            'res_model': 'stock.scrap',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.line_ids.scrap_ids.ids)],
        }


class StockLotWriteoffLine(models.Model):
    _name = 'stock.lot.writeoff.line'
    _description = 'Lote dado de baja'
    _order = 'writeoff_id desc, id'

    writeoff_id = fields.Many2one(
        'stock.lot.writeoff',
        string='Baja',
        required=True,
        ondelete='cascade',
        index=True,
    )
    company_id = fields.Many2one(
        related='writeoff_id.company_id',
        store=True,
        readonly=True,
    )
    state = fields.Selection(
        related='writeoff_id.state',
        store=True,
        readonly=True,
    )
    product_from_id = fields.Many2one(
        related='writeoff_id.product_from_id',
        string='Producto',
        store=True,
        readonly=True,
    )
    lot_from_id = fields.Many2one(
        'stock.lot',
        string='Lote',
        required=True,
        index=True,
        ondelete='restrict',
    )
    qty_available = fields.Float(
        string='Existencia actual',
        compute='_compute_qty_available',
        digits=(12, 4),
    )
    qty_moved = fields.Float(
        string='Cantidad dada de baja',
        readonly=True,
        copy=False,
        digits=(12, 4),
    )
    location_note = fields.Char(
        string='Ubicaciones',
        readonly=True,
        copy=False,
    )
    scrap_ids = fields.Many2many(
        'stock.scrap',
        'stock_lot_writeoff_line_scrap_rel',
        'line_id',
        'scrap_id',
        string='Desechos',
        readonly=True,
        copy=False,
    )

    @api.depends('lot_from_id')
    def _compute_qty_available(self):
        for line in self:
            if not line.lot_from_id:
                line.qty_available = 0.0
                continue
            quants = line.writeoff_id._get_lot_internal_quants(line.lot_from_id)
            line.qty_available = sum(quants.mapped('quantity'))

    def _apply_writeoff(self, scrap_location):
        self.ensure_one()
        rec = self.writeoff_id
        lot = self.lot_from_id

        Scrap = self.env['stock.scrap'].sudo().with_context(
            **rec._writeoff_stock_context()
        )

        moved_qty = 0.0
        location_names = []
        scraps = self.env['stock.scrap'].sudo()

        # Un scrap por quant (ubicación): salida real con movimiento a la
        # ubicación de desecho — trazabilidad y valoración estándar de Odoo.
        for quant in rec._get_lot_internal_quants(lot):
            qty = quant.quantity or 0.0
            if qty <= 0:
                continue
            scrap = Scrap.create({
                'product_id': rec.product_from_id.id,
                'product_uom_id': rec.product_from_id.uom_id.id,
                'lot_id': lot.id,
                'scrap_qty': qty,
                'location_id': quant.location_id.id,
                'scrap_location_id': scrap_location.id,
                'company_id': rec.company_id.id,
                'origin': rec.name,
            })
            scrap.with_context(**rec._writeoff_stock_context()).action_validate()
            scraps |= scrap
            moved_qty += qty
            location_names.append(
                quant.location_id.display_name or quant.location_id.name or ''
            )

        self.write({
            'qty_moved': moved_qty,
            'location_note': ', '.join(filter(None, location_names)),
            'scrap_ids': [(6, 0, scraps.ids)],
        })

        # Rastro en el chatter del lote y archivado: el material ya no existe.
        reason_label = dict(rec._fields['reason_type'].selection).get(
            rec.reason_type, rec.reason_type)
        lot.message_post(body=Markup(_(
            'Dado de baja por %(folio)s (%(rtype)s): %(qty).4f enviados a '
            'desecho. Este lote queda archivado. Motivo: %(reason)s'
        )) % {
            'folio': rec.name,
            'rtype': reason_label,
            'qty': moved_qty,
            'reason': rec.reason or '',
        })

        if 'active' in lot._fields:
            lot.sudo().write({'active': False})

        return scraps


class StockQuantWriteoffHistory(models.Model):
    _inherit = 'stock.quant'

    @api.model
    def get_lot_history(self, quant_id):
        """Agrega las bajas de material al "Historial de logs" del
        Inventario Visual / Walkthrough."""
        result = super().get_lot_history(quant_id)

        if not isinstance(result, dict) or result.get('error'):
            return result

        quant = self.browse(quant_id)
        lot = quant.lot_id
        if not lot:
            return result

        lines = self.env['stock.lot.writeoff.line'].sudo().search([
            ('writeoff_id.state', '=', 'done'),
            ('lot_from_id', '=', lot.id),
        ])
        if not lines:
            return result

        logs = result.setdefault('general_logs', [])
        for line in lines:
            rec = line.writeoff_id
            fecha = rec.date_done or rec.write_date
            reason_label = dict(rec._fields['reason_type'].selection).get(
                rec.reason_type, rec.reason_type)
            logs.append({
                'fecha_sort': fecha.strftime('%Y-%m-%d %H:%M') if fecha else '',
                'fecha': som_format_date(fecha, empty='', with_time=True),
                'usuario': rec.user_id.name if rec.user_id else 'Sistema',
                'origen': 'Baja de material',
                'descripcion': (
                    f"Dado de baja ({reason_label}): {line.qty_moved:.4f}. "
                    f"Folio: {rec.name}. Motivo: {rec.reason or ''}"
                ),
            })

        som_sort_general_logs(logs)
        return result
