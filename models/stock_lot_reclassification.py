# -*- coding: utf-8 -*-
import logging

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Campos de stock.lot que NUNCA se copian al lote espejo.
LOT_METADATA_BLOCKED_FIELDS = {
    'id', 'name', 'display_name', '__last_update', 'active',
    'product_id', 'product_tmpl_id', 'product_qty', 'product_uom_id',
    'company_id', 'quant_ids', 'location_id',
    'create_uid', 'create_date', 'write_uid', 'write_date',
    'message_main_attachment_id', 'lot_properties',
}

LOT_METADATA_BLOCKED_PREFIXES = (
    'message_', 'activity_', 'website_', 'rating_', 'access_',
)


class StockLotReclassification(models.Model):
    _name = 'stock.lot.reclassification'
    _description = 'Reclasificación de lotes entre productos'
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

    product_from_id = fields.Many2one(
        'product.product',
        string='Producto origen',
        required=True,
        tracking=True,
        domain=[('tracking', '!=', 'none')],
        help='Producto al que están asignados los lotes por error.',
    )
    product_to_id = fields.Many2one(
        'product.product',
        string='Producto destino',
        required=True,
        tracking=True,
        domain=[('tracking', '!=', 'none')],
        help='Producto al que realmente pertenece el material.',
    )
    reason = fields.Text(
        string='Motivo',
        required=True,
        tracking=True,
        help='Justificación de la reclasificación (queda en el historial de ambos lotes).',
    )
    line_ids = fields.One2many(
        'stock.lot.reclassification.line',
        'reclassification_id',
        string='Lotes a reclasificar',
        copy=False,
    )
    line_count = fields.Integer(compute='_compute_totals', string='Lotes')
    total_qty = fields.Float(
        compute='_compute_totals',
        string='Cantidad total',
        digits=(12, 4),
        help='Suma de las existencias movidas (o por mover) de todos los lotes.',
    )

    @api.depends('line_ids.qty_moved', 'line_ids.qty_available')
    def _compute_totals(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)
            if rec.state == 'done':
                rec.total_qty = sum(rec.line_ids.mapped('qty_moved'))
            else:
                rec.total_qty = sum(rec.line_ids.mapped('qty_available'))

    @api.constrains('product_from_id', 'product_to_id')
    def _check_products(self):
        for rec in self:
            if rec.product_from_id == rec.product_to_id:
                raise ValidationError(_(
                    'El producto origen y el destino deben ser distintos.'
                ))

    @api.model
    def _prune_ghost_line_commands(self, vals):
        """Descarta comandos CREATE de líneas sin lot_from_id.

        El selector visual puede dejar filas fantasma en el one2many si el
        update del cliente falla a medias (onchange caído, assets viejos);
        esas filas viajan como [0, 0, {}] y truenan el required de
        lot_from_id. Una línea sin lote nunca tiene sentido de negocio,
        así que se filtra aquí como última barrera."""
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
                '[RECLASIFICACION] Se descartaron %s línea(s) fantasma sin '
                'lot_from_id al guardar.', len(commands) - len(pruned),
            )
            vals['line_ids'] = pruned

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'Nuevo') == 'Nuevo':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'stock.lot.reclassification'
                ) or 'Nuevo'
            self._prune_ghost_line_commands(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._prune_ghost_line_commands(vals)
        return super().write(vals)

    def unlink(self):
        if any(rec.state == 'done' for rec in self):
            raise UserError(_(
                'No puedes eliminar una reclasificación aplicada: es historial '
                'de inventario. Puedes hacer una reclasificación inversa si fue '
                'un error.'
            ))
        return super().unlink()

    # -------------------------------------------------------------------------
    # Validaciones previas
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
            raise UserError(_('Agrega al menos un lote a reclasificar.'))

        Quant = self.env['stock.quant']
        committed_lot_ids = set()
        if hasattr(Quant, '_get_committed_lot_ids'):
            try:
                committed_lot_ids = set(
                    Quant._get_committed_lot_ids(self.product_from_id.id)
                )
            except Exception:
                _logger.exception(
                    '[RECLASIFICACION] No se pudo consultar lotes comprometidos; '
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
                    'El lote %(lot)s pertenece a %(product)s, no al producto origen.'
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
                    'El lote %s tiene cantidad reservada (entrega, taller u otro '
                    'documento). Libera la reserva antes de reclasificar.'
                ) % lot.name)
                continue

            if any(getattr(q, 'x_tiene_hold', False) for q in quants):
                problems.append(_(
                    'El lote %s tiene un apartado (hold) activo. Cancélalo antes '
                    'de reclasificar.'
                ) % lot.name)
                continue

            if lot.id in committed_lot_ids:
                problems.append(_(
                    'El lote %s está comprometido en una venta, apartado u orden '
                    'de taller. Libéralo antes de reclasificar.'
                ) % lot.name)
                continue


        if problems:
            raise UserError(_(
                'No se puede aplicar la reclasificación:\n\n%s'
            ) % '\n'.join('- %s' % p for p in problems))

        return True

    # -------------------------------------------------------------------------
    # Aplicación
    # -------------------------------------------------------------------------

    def _get_unique_target_lot_name(self, base_name):
        """Nombre del lote espejo en el producto destino.

        Se conserva el nombre original (es el mismo material, mismo folio
        físico). Solo si ya existe un lote con ese nombre en el producto
        destino se agrega un sufijo incremental -R2, -R3... para evitar
        la colisión sin bloquear la operación."""
        self.ensure_one()
        # active_test=False: un lote archivado también colisiona por nombre.
        Lot = self.env['stock.lot'].sudo().with_context(active_test=False)

        def taken(name):
            return bool(Lot.search_count([
                ('name', '=', name),
                ('product_id', '=', self.product_to_id.id),
                '|',
                ('company_id', '=', self.company_id.id),
                ('company_id', '=', False),
            ]))

        if not taken(base_name):
            return base_name
        suffix = 2
        while taken('%s-R%s' % (base_name, suffix)):
            suffix += 1
        return '%s-R%s' % (base_name, suffix)

    def _lot_metadata_copy_vals(self, lot):
        """Copia introspectiva de la metadata del lote (dimensiones, bloque,
        atado, tono, pedimento, contenedor, x_*...). No copia fotos binarias
        directas ni relaciones one2many: las fotos se re-ligan aparte."""
        vals = {}
        Lot = self.env['stock.lot']

        for fname, field in lot._fields.items():
            if fname in LOT_METADATA_BLOCKED_FIELDS or fname not in Lot._fields:
                continue
            if fname.startswith(LOT_METADATA_BLOCKED_PREFIXES):
                continue
            if field.compute and not field.inverse:
                continue
            if field.type in ('one2many', 'reference', 'binary'):
                continue

            value = lot[fname]

            if field.type == 'many2one':
                vals[fname] = value.id if value else False
            elif field.type == 'many2many':
                vals[fname] = [(6, 0, value.ids)]
            else:
                if value in (False, None) and field.type != 'boolean':
                    continue
                vals[fname] = value

        return vals

    def _move_lot_photos(self, lot_from, lot_to):
        """Re-liga las fotos del lote original al lote nuevo (si el modelo de
        fotos existe). El lote original queda en cero, sin uso operativo."""
        if 'x_fotografia_ids' not in lot_from._fields:
            return False
        photos = lot_from.x_fotografia_ids
        if not photos:
            return False
        inverse_name = lot_from._fields['x_fotografia_ids'].inverse_name
        photos.sudo().write({inverse_name: lot_to.id})
        return True

    def _reclassification_stock_context(self):
        """Bypass de guardias externos (holds, lote completo, duplicados) al
        aplicar los ajustes de inventario: la validación de negocio ya se hizo
        ANTES en _assert_lines_applicable."""
        return {
            'inventory_mode': True,
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
                raise UserError(_('Solo puedes aplicar reclasificaciones en borrador.'))
            if not self.env.user.has_group('stock.group_stock_manager'):
                raise UserError(_(
                    'Solo un administrador de inventario puede aplicar reclasificaciones.'
                ))

            rec._assert_lines_applicable()

            for line in rec.line_ids:
                line._apply_reclassification()

            rec.write({
                'state': 'done',
                'date_done': fields.Datetime.now(),
            })

            rec.message_post(body=Markup(_(
                'Reclasificación aplicada: %(count)s lote(s), %(qty).4f de '
                '<strong>%(origin)s</strong> → <strong>%(target)s</strong>.<br/>'
                'Motivo: %(reason)s'
            )) % {
                'count': len(rec.line_ids),
                'qty': sum(rec.line_ids.mapped('qty_moved')),
                'origin': rec.product_from_id.display_name,
                'target': rec.product_to_id.display_name,
                'reason': rec.reason or '',
            })

            _logger.info(
                '[RECLASIFICACION] %s aplicada: %s lotes de %s a %s por %s',
                rec.name,
                len(rec.line_ids),
                rec.product_from_id.display_name,
                rec.product_to_id.display_name,
                self.env.user.name,
            )

        return True

    def action_open_label_wizard(self):
        """Etiquetas ZPL de los lotes NUEVOS: solo con la reclasificación
        aplicada (antes no existen los lotes espejo)."""
        self.ensure_one()
        if self.state != 'done':
            raise UserError(_(
                'Aplica la reclasificación antes de imprimir etiquetas.'
            ))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Imprimir etiquetas'),
            'res_model': 'stock.lot.reclassification.label.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_reclassification_id': self.id},
        }

    def action_cancel(self):
        for rec in self:
            if rec.state == 'done':
                raise UserError(_(
                    'No puedes cancelar una reclasificación ya aplicada. '
                    'Haz una reclasificación inversa si fue un error.'
                ))
            rec.write({'state': 'cancel'})
        return True

    def action_draft(self):
        for rec in self:
            if rec.state != 'cancel':
                raise UserError(_('Solo puedes reactivar reclasificaciones canceladas.'))
            rec.write({'state': 'draft'})
        return True


class StockLotReclassificationLine(models.Model):
    _name = 'stock.lot.reclassification.line'
    _description = 'Lote reclasificado'
    _order = 'reclassification_id desc, id'

    reclassification_id = fields.Many2one(
        'stock.lot.reclassification',
        string='Reclasificación',
        required=True,
        ondelete='cascade',
        index=True,
    )
    company_id = fields.Many2one(
        related='reclassification_id.company_id',
        store=True,
        readonly=True,
    )
    state = fields.Selection(
        related='reclassification_id.state',
        store=True,
        readonly=True,
    )
    product_from_id = fields.Many2one(
        related='reclassification_id.product_from_id',
        string='Producto origen',
        store=True,
        readonly=True,
    )
    product_to_id = fields.Many2one(
        related='reclassification_id.product_to_id',
        string='Producto destino',
        store=True,
        readonly=True,
    )
    lot_from_id = fields.Many2one(
        'stock.lot',
        string='Lote original',
        required=True,
        index=True,
        ondelete='restrict',
    )
    lot_to_id = fields.Many2one(
        'stock.lot',
        string='Lote nuevo',
        readonly=True,
        copy=False,
        index=True,
        ondelete='restrict',
        help='Lote espejo creado en el producto correcto al aplicar.',
    )
    qty_available = fields.Float(
        string='Existencia actual',
        compute='_compute_qty_available',
        digits=(12, 4),
    )
    qty_moved = fields.Float(
        string='Cantidad movida',
        readonly=True,
        copy=False,
        digits=(12, 4),
    )
    location_note = fields.Char(
        string='Ubicaciones',
        readonly=True,
        copy=False,
    )

    @api.depends('lot_from_id')
    def _compute_qty_available(self):
        for line in self:
            if not line.lot_from_id:
                line.qty_available = 0.0
                continue
            quants = line.reclassification_id._get_lot_internal_quants(line.lot_from_id)
            line.qty_available = sum(quants.mapped('quantity'))

    def _apply_reclassification(self):
        self.ensure_one()
        rec = self.reclassification_id
        lot_from = self.lot_from_id

        # 1) Lote espejo en el producto correcto: mismo nombre + metadata.
        #    Si el nombre ya existe en el destino, se agrega sufijo -R2, -R3...
        target_name = rec._get_unique_target_lot_name(lot_from.name)
        lot_vals = {
            'name': target_name,
            'product_id': rec.product_to_id.id,
            'company_id': lot_from.company_id.id or rec.company_id.id,
        }
        lot_vals.update(rec._lot_metadata_copy_vals(lot_from))
        lot_to = self.env['stock.lot'].sudo().create(lot_vals)

        # 2) Fotos: se re-ligan al lote nuevo.
        rec._move_lot_photos(lot_from, lot_to)

        # 3) Transferir existencias vía ajuste de inventario, por ubicación.
        Quant = self.env['stock.quant'].sudo().with_context(
            **rec._reclassification_stock_context()
        )
        moved_qty = 0.0
        location_names = []

        for quant in rec._get_lot_internal_quants(lot_from):
            qty = quant.quantity or 0.0
            location = quant.location_id

            quant.with_context(**rec._reclassification_stock_context()).write({
                'inventory_quantity': 0.0,
            })
            quant.with_context(**rec._reclassification_stock_context()).action_apply_inventory()

            new_quant = Quant.create({
                'product_id': rec.product_to_id.id,
                'lot_id': lot_to.id,
                'location_id': location.id,
                'inventory_quantity': qty,
            })
            new_quant.action_apply_inventory()

            moved_qty += qty
            location_names.append(location.display_name or location.name or '')

        self.write({
            'lot_to_id': lot_to.id,
            'qty_moved': moved_qty,
            'location_note': ', '.join(filter(None, location_names)),
        })

        # 4) Rastro cruzado en el chatter de ambos lotes.
        reason = rec.reason or ''
        lot_from.message_post(body=Markup(_(
            'Reclasificado por %(folio)s: este material pertenece a '
            '<strong>%(target)s</strong>. Existencias transferidas al lote '
            '%(new_lot)s (%(qty).4f). Este lote queda archivado. '
            'Motivo: %(reason)s'
        )) % {
            'folio': rec.name,
            'target': rec.product_to_id.display_name,
            'new_lot': lot_to.name,
            'qty': moved_qty,
            'reason': reason,
        })
        lot_to.message_post(body=Markup(_(
            'Creado por reclasificación %(folio)s desde '
            '<strong>%(origin)s</strong> (lote %(old_lot)s, %(qty).4f). '
            'Motivo: %(reason)s'
        )) % {
            'folio': rec.name,
            'origin': rec.product_from_id.display_name,
            'old_lot': lot_from.name,
            'qty': moved_qty,
            'reason': reason,
        })

        # 5) El lote original ya no existe operativamente: se archiva para que
        #    desaparezca de listados y selectores (borrar es imposible: los
        #    movimientos de inventario y esta línea lo referencian).
        if 'active' in lot_from._fields:
            lot_from.sudo().write({'active': False})

        return lot_to


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    # ------------------------------------------------------------------
    # Búsqueda del selector de reclasificación
    # ------------------------------------------------------------------
    @api.model
    def _reclassification_committed_lot_ids(self, product_id):
        """Lotes comprometidos en venta/apartado/taller: la MISMA regla que
        usa _assert_lines_applicable, para que el selector nunca ofrezca un
        lote que la validación final rechazaría."""
        if not hasattr(self, '_get_committed_lot_ids'):
            return []
        try:
            return list(self._get_committed_lot_ids(int(product_id)))
        except Exception:
            _logger.exception(
                '[RECLASIFICACION] No se pudieron consultar lotes comprometidos '
                'para el selector; se muestran sin ese filtro.'
            )
            return []

    @api.model
    def _build_reclassification_lot_domain(self, product_id, filters=None,
                                           current_lot_ids=None):
        domain = self._build_workshop_lot_domain(
            product_id=product_id,
            filters=filters,
            current_lot_ids=current_lot_ids,
            location_id=False,
            order_id=False,
        )
        committed = self._reclassification_committed_lot_ids(product_id)
        if committed:
            domain.append(('lot_id', 'not in', committed))
        return domain

    @api.model
    def search_reclassification_lot_inventory(self, product_id, filters=None,
                                              current_lot_ids=None):
        domain = self._build_reclassification_lot_domain(
            product_id, filters=filters, current_lot_ids=current_lot_ids,
        )
        quants = self.search(domain, limit=300, order='lot_id, location_id, id')
        lots_data = self._build_workshop_lots_data(quants.mapped('lot_id').ids)
        return self._workshop_quants_to_result(quants, lots_data)

    @api.model
    def search_reclassification_lot_inventory_paginated(self, product_id, filters=None,
                                                        current_lot_ids=None,
                                                        page=0, page_size=35):
        page = int(page or 0)
        page_size = int(page_size or 35)
        domain = self._build_reclassification_lot_domain(
            product_id, filters=filters, current_lot_ids=current_lot_ids,
        )
        total = self.search_count(domain)
        quants = self.search(
            domain, limit=page_size, offset=page * page_size,
            order='lot_id, location_id, id',
        )
        lots_data = self._build_workshop_lots_data(quants.mapped('lot_id').ids)
        return {
            'items': self._workshop_quants_to_result(quants, lots_data),
            'total': total,
        }

    @api.model
    def get_lot_history(self, quant_id):
        """Agrega las reclasificaciones al historial del Inventario Visual.

        El lote nuevo muestra de dónde viene (producto y lote original) y el
        lote original hacia dónde se fue, con folio, motivo y usuario — ambos
        quedan identificados entre sí en el "Historial de logs".
        """
        result = super().get_lot_history(quant_id)

        if not isinstance(result, dict) or result.get('error'):
            return result

        quant = self.browse(quant_id)
        lot = quant.lot_id
        if not lot:
            return result

        lines = self.env['stock.lot.reclassification.line'].sudo().search([
            '&',
            ('reclassification_id.state', '=', 'done'),
            '|',
            ('lot_from_id', '=', lot.id),
            ('lot_to_id', '=', lot.id),
        ])
        if not lines:
            return result

        logs = result.setdefault('general_logs', [])

        for line in lines:
            rec = line.reclassification_id
            fecha = rec.date_done or rec.write_date

            if line.lot_from_id == lot:
                descripcion = (
                    f"Reclasificado HACIA {rec.product_to_id.display_name} "
                    f"(lote {line.lot_to_id.name if line.lot_to_id else lot.name}, "
                    f"{line.qty_moved:.4f}). Folio: {rec.name}. "
                    f"Motivo: {rec.reason or ''}"
                )
            else:
                descripcion = (
                    f"Creado por reclasificación DESDE {rec.product_from_id.display_name} "
                    f"(lote {line.lot_from_id.name}, {line.qty_moved:.4f}). "
                    f"Folio: {rec.name}. Motivo: {rec.reason or ''}"
                )

            logs.append({
                'fecha': fecha.strftime('%Y-%m-%d %H:%M') if fecha else '',
                'usuario': rec.user_id.name if rec.user_id else 'Sistema',
                'origen': 'Reclasificación',
                'descripcion': descripcion,
            })

        # El formato de fecha (YYYY-MM-DD HH:MM) ordena bien como texto.
        logs.sort(key=lambda l: l.get('fecha') or '', reverse=True)

        return result
